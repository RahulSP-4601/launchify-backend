from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import get_settings
from app.models.projects import (
    FocusBox,
    FrameSignalRecord,
    LaunchScriptRecord,
    ProjectRecord,
    RecordingSessionRecord,
    SessionEventRecord,
    SessionEventType,
    TranscriptSegment,
    UiElementRecord,
    VisualSceneAnalysisRecord,
)
from app.services.inferred_recording_support import (
    InteractionWindow,
    MIN_WINDOW_SCORE,
    actionable_label,
    best_matching_ui_box,
    box_area,
    box_center_delta,
    build_session_event,
    canonical_scene_windows,
    contains_any,
    dedupe_events,
    duplicate_event,
    fallback_intent_label,
    intent_overlap_score,
    intent_tokens,
    label_quality_score,
    low_signal_label,
    merge_windows,
    normalize_label,
    select_distinct_windows,
    state_like_label,
    transcript_window,
)
from app.services.guide_event_dedupe import synthetic_event_score
from app.services.inferred_action_recovery import needs_action_recovery, recover_events_from_analyses
from app.services.inferred_action_selection import SceneEventCandidate, select_global_events
from app.services.grounding_diagnostics import recording_diagnostics
from app.services.visual_analysis import analysis_map

DEFAULT_VIEWPORT = (1280, 720)
MAX_EVENTS = 16
MAX_EVENTS_PER_SCENE = 3
CANDIDATE_EVIDENCE_THRESHOLD = 0.44
LOCAL_TRANSCRIPT_PADDING_SECONDS = 2.2
CLICK_WORDS = frozenset({"click", "tap", "press", "select", "choose", "continue", "open", "start", "launch", "login", "log in"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password", "name"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "home", "next", "continue", "course"})
def infer_recording_session(
    project: ProjectRecord,
    video_path: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    visual_analyses: list[VisualSceneAnalysisRecord] | None,
) -> RecordingSessionRecord | None:
    analyses_by_scene = analysis_map(visual_analyses or [])
    viewport_width, viewport_height = video_dimensions(video_path)
    events = build_inferred_events(launch_script, transcript, analyses_by_scene, viewport_width, viewport_height)
    if not events:
        return None
    return RecordingSessionRecord(
        source="manual_upload_inferred",
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        page_title=project.product_name,
        grounding_diagnostics=recording_diagnostics(events, transcript, list(analyses_by_scene.values())),
        events=events[:MAX_EVENTS],
        started_at="0.0",
        ended_at=f"{max((segment.end for segment in transcript), default=0.0):.2f}",
    )
def build_inferred_events(
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
) -> list[SessionEventRecord]:
    inferred_events: list[SceneEventCandidate] = []
    source_excerpt_by_scene = {scene.scene_number: scene.source_excerpt for scene in launch_script.scenes}
    for scene in launch_script.scenes:
        analysis = analyses_by_scene.get(scene.scene_number)
        if analysis is None:
            continue
        source_excerpt = source_excerpt_by_scene.get(scene.scene_number, scene.source_excerpt)
        transcript_excerpt = transcript_window(transcript, analysis.start, analysis.end)
        windows = infer_scene_windows(analysis, transcript, source_excerpt)
        inferred_events.extend(
            SceneEventCandidate(scene.scene_number, build_session_event(window, scene.scene_number, viewport_width, viewport_height))
            for window in canonical_scene_windows(windows, analysis, transcript_excerpt, scene.source_excerpt)[:MAX_EVENTS_PER_SCENE]
        )
    deduped = dedupe_events(sorted((candidate.event for candidate in inferred_events), key=lambda item: item.timestamp))
    if needs_action_recovery(deduped, transcript):
        recovered = recover_events_from_analyses(transcript, list(analyses_by_scene.values()), viewport_width, viewport_height)
        deduped = dedupe_events(sorted([*deduped, *recovered], key=lambda item: item.timestamp))
    candidates = [SceneEventCandidate(int(event.metadata.get("scene_number", "0") or 0), event) for event in deduped]
    return select_global_events(candidates)


def infer_scene_windows(
    analysis: VisualSceneAnalysisRecord,
    transcript: list[TranscriptSegment],
    source_excerpt: str,
) -> list[InteractionWindow]:
    windows: list[InteractionWindow] = []
    for index in range(len(analysis.frames)):
        transcript_excerpt = local_transcript_excerpt(transcript, analysis, index)
        if not frame_is_candidate(analysis.frames, index, transcript_excerpt, source_excerpt):
            continue
        window = build_window(analysis, index, transcript_excerpt, source_excerpt)
        if window is not None:
            windows.append(window)
    ranked = sorted((window for window in windows if window is not None), key=lambda item: item.score, reverse=True)
    merged = merge_windows(sorted(ranked, key=lambda item: item.timestamp))
    return select_distinct_windows(merged)


def local_transcript_excerpt(
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord,
    index: int,
) -> str:
    timestamp = analysis.frames[index].timestamp
    start = max(analysis.start, timestamp - LOCAL_TRANSCRIPT_PADDING_SECONDS)
    end = min(analysis.end, timestamp + LOCAL_TRANSCRIPT_PADDING_SECONDS)
    excerpt = transcript_window(transcript, start, end)
    return excerpt or transcript_window(transcript, analysis.start, analysis.end)


def frame_is_candidate(
    frames: list[FrameSignalRecord],
    index: int,
    transcript_excerpt: str,
    source_excerpt: str,
) -> bool:
    frame = frames[index]
    stop_score = cursor_stop_score(frames, index)
    label_change = label_change_score(frames, index)
    intent = transcript_intent_score(transcript_excerpt, source_excerpt, frame)
    action_phrase = action_phrase_score(transcript_excerpt, source_excerpt)
    visual_strength = max(frame.click_confidence, frame.diff_score, frame.importance_score)
    evidence = max(frame.click_confidence, frame.diff_score, frame.importance_score, stop_score, label_change, intent)
    if evidence < CANDIDATE_EVIDENCE_THRESHOLD:
        return False
    if action_phrase < 0.24 and max(frame.click_confidence, stop_score, intent) < 0.28 and visual_strength < 0.48:
        return False
    return inferred_focus_box(frame, transcript_excerpt, source_excerpt) is not None
def build_window(
    analysis: VisualSceneAnalysisRecord,
    index: int,
    transcript_excerpt: str,
    source_excerpt: str,
) -> InteractionWindow | None:
    frame = analysis.frames[index]
    focus_box = inferred_focus_box(frame, transcript_excerpt, source_excerpt) or analysis.click_target_box or analysis.anchor_box or analysis.primary_focus_box
    if focus_box is None:
        return None
    label = inferred_label(frame, analysis.visible_labels, transcript_excerpt, source_excerpt, focus_box)
    event_type = inferred_event_type(transcript_excerpt, source_excerpt, label, frame)
    score = interaction_score(analysis.frames, index, transcript_excerpt, source_excerpt)
    if score < MIN_WINDOW_SCORE or not semantically_valid_window(label, transcript_excerpt, source_excerpt, frame, score):
        return None
    return InteractionWindow(
        timestamp=round(frame.timestamp, 2),
        score=score,
        event_type=event_type,
        label=label,
        text=analysis.summary,
        focus_box=focus_box,
        transcript_excerpt=transcript_excerpt,
    )
def interaction_score(
    frames: list[FrameSignalRecord],
    index: int,
    transcript_excerpt: str,
    source_excerpt: str,
) -> float:
    frame = frames[index]
    score = (
        frame.click_confidence * 0.38
        + frame.diff_score * 0.12
        + frame.importance_score * 0.14
        + cursor_stop_score(frames, index) * 0.16
        + label_change_score(frames, index) * 0.06
        + transcript_intent_score(transcript_excerpt, source_excerpt, frame) * 0.14
    )
    return round(min(max(score + semantic_bonus(transcript_excerpt, source_excerpt, frame), 0.0), 1.0), 3)


def semantic_bonus(
    transcript_excerpt: str,
    source_excerpt: str,
    frame: FrameSignalRecord,
) -> float:
    label_match = transcript_intent_score(transcript_excerpt, source_excerpt, frame)
    action_phrase = action_phrase_score(transcript_excerpt, source_excerpt)
    return round(label_match * 0.08 + action_phrase * 0.06, 3)


def semantically_valid_window(
    label: str,
    transcript_excerpt: str,
    source_excerpt: str,
    frame: FrameSignalRecord,
    score: float,
) -> bool:
    transcript_match = intent_overlap_score(label, intent_tokens(transcript_excerpt, source_excerpt))
    strong_visual_signal = max(frame.click_confidence, frame.importance_score, frame.diff_score) >= 0.56
    compact_focus = frame.click_target_box is not None and box_area(frame.click_target_box) <= 0.14
    actionable = actionable_label(label)
    if state_like_label(label):
        return transcript_match >= 0.42 and strong_visual_signal and compact_focus and score >= 0.52
    if not actionable:
        return transcript_match >= 0.36 and strong_visual_signal and compact_focus and score >= 0.5
    evidence_count = 0
    if action_phrase_score(transcript_excerpt, source_excerpt) >= 0.24:
        evidence_count += 1
    if transcript_match >= 0.22:
        evidence_count += 1
    if compact_focus:
        evidence_count += 1
    if strong_visual_signal:
        evidence_count += 1
    return evidence_count >= 2
def cursor_stop_score(frames: list[FrameSignalRecord], index: int) -> float:
    current = frames[index]
    if current.cursor_box is None or index == 0:
        return 0.0
    previous = frames[index - 1]
    next_frame = frames[index + 1] if index + 1 < len(frames) else None
    prev_delta = box_center_delta(previous.cursor_box, current.cursor_box)
    next_delta = box_center_delta(current.cursor_box, next_frame.cursor_box if next_frame is not None else None)
    if prev_delta <= 0.015:
        return 0.0
    slowdown = max(prev_delta - next_delta, 0.0)
    return min(slowdown * 6.0, 1.0)
def label_change_score(frames: list[FrameSignalRecord], index: int) -> float:
    if index == 0:
        return 0.0
    current_labels = frame_label_set(frames[index])
    previous_labels = frame_label_set(frames[index - 1])
    if not current_labels and not previous_labels:
        return 0.0
    overlap = len(current_labels & previous_labels)
    union = len(current_labels | previous_labels)
    return round(1.0 - (overlap / union if union else 0.0), 3)
def frame_label_set(frame: FrameSignalRecord) -> set[str]:
    labels = {normalize_label(label) for label in frame.ocr_labels}
    labels.update(normalize_label(element.label) for element in frame.ui_elements)
    return {label for label in labels if label}
def transcript_intent_score(
    transcript_excerpt: str,
    source_excerpt: str,
    frame: FrameSignalRecord,
) -> float:
    transcript_tokens = intent_tokens(transcript_excerpt, source_excerpt)
    if not transcript_tokens:
        return 0.0
    label_tokens = frame_label_set(frame)
    if not label_tokens:
        return 0.0
    overlap = len(transcript_tokens & label_tokens)
    return round(min(overlap / max(len(transcript_tokens), 1), 1.0), 3)


def action_phrase_score(transcript_excerpt: str, source_excerpt: str) -> float:
    combined = f"{transcript_excerpt} {source_excerpt}".lower()
    score = 0.0
    if contains_any(combined, CLICK_WORDS):
        score += 0.52
    if contains_any(combined, INPUT_WORDS):
        score += 0.28
    if contains_any(combined, NAVIGATION_WORDS):
        score += 0.2
    return round(min(score, 1.0), 3)
def inferred_event_type(
    transcript_excerpt: str,
    source_excerpt: str,
    label: str,
    frame: FrameSignalRecord,
) -> SessionEventType:
    intent_text = f"{transcript_excerpt} {source_excerpt} {label}".lower()
    if contains_any(intent_text, INPUT_WORDS):
        return "input"
    if frame.click_confidence >= 0.42 or frame.click_target_box is not None or contains_any(intent_text, CLICK_WORDS):
        return "click"
    if contains_any(intent_text, NAVIGATION_WORDS):
        return "navigation"
    return "focus"
def inferred_focus_box(
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> FocusBox | None:
    matched_box = intent_matching_ui_box(frame, transcript_excerpt, source_excerpt)
    candidates = (
        frame.click_target_box,
        matched_box,
        nearest_ui_box(frame),
        best_matching_ui_box(frame),
        frame.cursor_box,
        compact_focus_candidate(frame.dominant_box),
    )
    return next((box for box in candidates if box is not None), None)


def compact_focus_candidate(box: FocusBox | None) -> FocusBox | None:
    if box is None or box_area(box) > 0.18:
        return None
    return box
def nearest_ui_box(frame: FrameSignalRecord) -> FocusBox | None:
    if frame.cursor_box is None or not frame.ui_elements:
        return first_ui_box(frame.ui_elements)
    ranked = sorted(
        (element for element in frame.ui_elements if element.box is not None),
        key=lambda element: box_center_delta(frame.cursor_box, element.box),
    )
    return ranked[0].box if ranked else None
def first_ui_box(elements: list[UiElementRecord]) -> FocusBox | None:
    for element in elements:
        if element.box is not None:
            return element.box
    return None


def intent_matching_ui_box(
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> FocusBox | None:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    if not tokens:
        return None
    ranked = sorted(
        (element for element in frame.ui_elements if element.box is not None and element.label.strip()),
        key=lambda element: (
            intent_overlap_score(element.label, tokens),
            label_quality_score(element.label),
            -box_center_delta(frame.cursor_box, element.box),
        ),
        reverse=True,
    )
    if not ranked:
        return None
    return ranked[0].box if intent_overlap_score(ranked[0].label, tokens) >= 0.2 else None


def inferred_label(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
) -> str:
    preferred = ranked_candidate_labels(frame, visible_labels, transcript_excerpt, source_excerpt, focus_box)
    fallback = fallback_intent_label(transcript_excerpt, source_excerpt)
    if not preferred:
        return fallback
    lead = preferred[0]
    if low_signal_label(lead):
        return fallback or lead
    if state_like_label(lead) and fallback and actionable_label(fallback):
        return fallback
    if fallback and intent_overlap_score(lead, intent_tokens(transcript_excerpt, source_excerpt)) < 0.34:
        return fallback
    return lead


def ranked_candidate_labels(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
) -> list[str]:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    labels = unique_label_candidates(label_candidates(frame, visible_labels))
    unique = [candidate for candidate in labels if candidate[0] and candidate[0].strip() and not low_signal_label(candidate[0])]
    ranked = sorted(
        unique,
        key=lambda candidate: label_rank(candidate, tokens, frame, focus_box),
        reverse=True,
    )
    return [label for label, _box, _source_weight in ranked]


def label_candidates(
    frame: FrameSignalRecord,
    visible_labels: list[str],
) -> list[tuple[str, FocusBox | None, float]]:
    candidates: list[tuple[str, FocusBox | None, float]] = [(element.label, element.box, 1.0) for element in frame.ui_elements]
    candidates.extend((label, None, 0.72) for label in frame.ocr_labels)
    candidates.extend((label, None, 0.55) for label in visible_labels)
    return candidates


def unique_label_candidates(
    candidates: list[tuple[str, FocusBox | None, float]],
) -> list[tuple[str, FocusBox | None, float]]:
    deduped: dict[str, tuple[str, FocusBox | None, float]] = {}
    for label, box, source_weight in candidates:
        key = normalize_label(label)
        current = deduped.get(key)
        if current is None or source_weight > current[2]:
            deduped[key] = (label, box, source_weight)
    return list(deduped.values())


def label_rank(
    candidate: tuple[str, FocusBox | None, float],
    tokens: set[str],
    frame: FrameSignalRecord,
    focus_box: FocusBox | None,
) -> tuple[float, float, float, float]:
    label, candidate_box, source_weight = candidate
    focus_delta = box_center_delta(focus_box or frame.cursor_box, candidate_box or focus_box)
    proximity = 0.0 if focus_delta == 0.0 and candidate_box is None else max(0.0, 1.0 - focus_delta * 3.0)
    box_compactness = 0.0 if candidate_box is None else max(0.0, 0.16 - box_area(candidate_box))
    return (
        intent_overlap_score(label, tokens),
        label_quality_score(label),
        source_weight + proximity,
        box_compactness,
    )




def video_dimensions(video_path: Path) -> tuple[int, int]:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
        width_text, height_text = result.stdout.strip().split("x", maxsplit=1)
        width = max(int(width_text), 1)
        height = max(int(height_text), 1)
        return width, height
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return DEFAULT_VIEWPORT
