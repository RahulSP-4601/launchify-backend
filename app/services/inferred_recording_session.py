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
    best_matching_ui_box,
    box_center_delta,
    build_session_event,
    canonical_scene_windows,
    contains_any,
    dedupe_events,
    fallback_intent_label,
    intent_overlap_score,
    intent_tokens,
    low_signal_label,
    merge_windows,
    normalize_label,
    select_distinct_windows,
    transcript_window,
)
from app.services.visual_analysis import analysis_map

DEFAULT_VIEWPORT = (1280, 720)
MAX_EVENTS = 16
MAX_EVENTS_PER_SCENE = 1
CANDIDATE_EVIDENCE_THRESHOLD = 0.34
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
    inferred_events: list[SessionEventRecord] = []
    for scene in launch_script.scenes:
        analysis = analyses_by_scene.get(scene.scene_number)
        if analysis is None:
            continue
        transcript_excerpt = transcript_window(transcript, analysis.start, analysis.end)
        windows = infer_scene_windows(analysis, transcript_excerpt, scene.source_excerpt)
        inferred_events.extend(
            build_session_event(window, scene.scene_number, viewport_width, viewport_height)
            for window in canonical_scene_windows(windows, analysis, transcript_excerpt, scene.source_excerpt)[:MAX_EVENTS_PER_SCENE]
        )
    return dedupe_events(sorted(inferred_events, key=lambda item: item.timestamp))
def infer_scene_windows(
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[InteractionWindow]:
    windows = [
        build_window(analysis, index, transcript_excerpt, source_excerpt)
        for index in range(len(analysis.frames))
        if frame_is_candidate(analysis.frames, index, transcript_excerpt, source_excerpt)
    ]
    ranked = sorted((window for window in windows if window is not None), key=lambda item: item.score, reverse=True)
    merged = merge_windows(sorted(ranked, key=lambda item: item.timestamp))
    return select_distinct_windows(merged)
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
    evidence = max(frame.click_confidence, frame.diff_score, frame.importance_score, stop_score, label_change, intent)
    return evidence >= CANDIDATE_EVIDENCE_THRESHOLD and inferred_focus_box(frame) is not None
def build_window(
    analysis: VisualSceneAnalysisRecord,
    index: int,
    transcript_excerpt: str,
    source_excerpt: str,
) -> InteractionWindow | None:
    frame = analysis.frames[index]
    focus_box = inferred_focus_box(frame) or analysis.click_target_box or analysis.anchor_box or analysis.primary_focus_box
    if focus_box is None:
        return None
    label = inferred_label(frame, analysis.visible_labels, transcript_excerpt, source_excerpt)
    event_type = inferred_event_type(transcript_excerpt, source_excerpt, label, frame)
    score = interaction_score(analysis.frames, index, transcript_excerpt, source_excerpt)
    if score < MIN_WINDOW_SCORE:
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
        frame.click_confidence * 0.34
        + frame.diff_score * 0.18
        + frame.importance_score * 0.14
        + cursor_stop_score(frames, index) * 0.18
        + label_change_score(frames, index) * 0.08
        + transcript_intent_score(transcript_excerpt, source_excerpt, frame) * 0.08
    )
    return round(min(max(score, 0.0), 1.0), 3)
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
def inferred_focus_box(frame: FrameSignalRecord) -> FocusBox | None:
    return frame.click_target_box or nearest_ui_box(frame) or best_matching_ui_box(frame) or frame.dominant_box or frame.cursor_box
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


def inferred_label(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
) -> str:
    preferred = ranked_candidate_labels(frame, visible_labels, transcript_excerpt, source_excerpt)
    if preferred:
        return preferred[0]
    return fallback_intent_label(transcript_excerpt, source_excerpt)


def ranked_candidate_labels(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[str]:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    labels = [element.label for element in frame.ui_elements] + frame.ocr_labels + visible_labels
    unique = [label.strip() for label in labels if label and label.strip() and not low_signal_label(label)]
    ranked = sorted(
        dict.fromkeys(unique),
        key=lambda label: (intent_overlap_score(label, tokens), len(label)),
        reverse=True,
    )
    return ranked


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
