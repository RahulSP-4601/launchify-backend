from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
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
    SessionTargetRecord,
    TranscriptSegment,
    UiElementRecord,
    VisualSceneAnalysisRecord,
)
from app.services.visual_analysis import analysis_map

DEFAULT_VIEWPORT = (1280, 720)
MAX_EVENTS = 12
MAX_EVENTS_PER_SCENE = 4
INTERACTION_GAP_SECONDS = 0.55
EVENT_FOCUS_DISTANCE_PIXELS = 48.0
MIN_DISTINCT_WINDOW_SECONDS = 1.35
GENERIC_LABELS = frozenset({
    "button",
    "control",
    "continue",
    "next",
    "one",
    "two",
    "three",
    "four",
    "five",
    "learn",
    "free",
})

CLICK_WORDS = frozenset({"click", "tap", "press", "select", "choose", "continue", "open", "start", "launch", "login", "log in"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password", "name"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "home", "next", "continue", "course"})


@dataclass(frozen=True)
class InteractionWindow:
    timestamp: float
    score: float
    event_type: SessionEventType
    label: str
    text: str
    focus_box: FocusBox | None
    transcript_excerpt: str
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
            for window in windows[:MAX_EVENTS_PER_SCENE]
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
    return evidence >= 0.28 and inferred_focus_box(frame) is not None
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
    return best_matching_ui_box(frame) or frame.click_target_box or nearest_ui_box(frame) or frame.dominant_box or frame.cursor_box
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
    fallback = source_excerpt.strip() or transcript_excerpt.strip() or "Product interaction"
    return sentence_fallback_label(fallback)


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


def low_signal_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not normalized:
        return True
    if normalized in GENERIC_LABELS:
        return True
    tokens = normalized.split()
    if len(tokens) == 1 and len(tokens[0]) <= 3:
        return True
    return len(normalized) < 4


def sentence_fallback_label(text: str) -> str:
    sentence = re.split(r"[.!?]", text, maxsplit=1)[0].strip()
    return sentence[:72] if sentence else "Product interaction"


def intent_overlap_score(label: str, intent_tokens_set: set[str]) -> float:
    if not intent_tokens_set:
        return 0.0
    label_tokens = set(normalize_label(label).split())
    overlap = len(label_tokens & intent_tokens_set)
    return overlap / max(len(label_tokens), 1)


def intent_tokens(*texts: str) -> set[str]:
    tokens: set[str] = set()
    for text in texts:
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def normalize_label(label: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", label.lower()))


def merge_windows(windows: list[InteractionWindow]) -> list[InteractionWindow]:
    if not windows:
        return []
    merged: list[InteractionWindow] = [windows[0]]
    for window in windows[1:]:
        previous = merged[-1]
        if should_merge(previous, window):
            merged[-1] = previous if previous.score >= window.score else window
            continue
        merged.append(window)
    return sorted(merged, key=lambda item: item.score, reverse=True)


def select_distinct_windows(windows: list[InteractionWindow]) -> list[InteractionWindow]:
    selected: list[InteractionWindow] = []
    for window in windows:
        if any(window_conflicts(window, existing) for existing in selected):
            continue
        selected.append(window)
    return selected


def window_conflicts(left: InteractionWindow, right: InteractionWindow) -> bool:
    close_in_time = abs(left.timestamp - right.timestamp) < MIN_DISTINCT_WINDOW_SECONDS
    same_label = normalize_label(left.label) == normalize_label(right.label)
    same_focus = box_center_delta(left.focus_box, right.focus_box) <= 0.08
    return close_in_time and (same_label or same_focus)


def should_merge(left: InteractionWindow, right: InteractionWindow) -> bool:
    if abs(left.timestamp - right.timestamp) > INTERACTION_GAP_SECONDS:
        return False
    same_label = normalize_label(left.label) == normalize_label(right.label)
    same_type = left.event_type == right.event_type
    same_focus = box_center_delta(left.focus_box, right.focus_box) <= 0.06
    return same_type and (same_label or same_focus)


def build_session_event(
    window: InteractionWindow,
    scene_number: int,
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord:
    x, y, width, height = denormalize_box(window.focus_box, viewport_width, viewport_height)
    return SessionEventRecord(
        type=window.event_type,
        timestamp=window.timestamp,
        x=x,
        y=y,
        target=SessionTargetRecord(
            selector=f"[data-launchify-scene='{scene_number}']",
            label=window.label,
            text=window.text,
            role="control",
            bbox_x=x - width / 2 if x is not None and width is not None else None,
            bbox_y=y - height / 2 if y is not None and height is not None else None,
            bbox_width=width,
            bbox_height=height,
        ),
        metadata={
            "inferred": "true",
            "scene_number": str(scene_number),
            "score": f"{window.score:.2f}",
            "transcript_excerpt": window.transcript_excerpt[:180],
        },
    )


def dedupe_events(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    deduped: list[SessionEventRecord] = []
    for event in events:
        if deduped and duplicate_event(deduped[-1], event):
            if float(event.metadata.get("score", "0")) > float(deduped[-1].metadata.get("score", "0")):
                deduped[-1] = event
            continue
        deduped.append(event)
    return deduped


def duplicate_event(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    same_label = normalize_label(left.target.label) == normalize_label(right.target.label)
    close_in_time = abs(left.timestamp - right.timestamp) <= INTERACTION_GAP_SECONDS
    same_type = left.type == right.type
    same_focus = focus_distance(left, right) <= EVENT_FOCUS_DISTANCE_PIXELS
    return close_in_time and same_type and (same_label or same_focus)


def focus_distance(left: SessionEventRecord, right: SessionEventRecord) -> float:
    left_point = event_point(left)
    right_point = event_point(right)
    if left_point is None or right_point is None:
        return 1.0
    return abs(left_point[0] - right_point[0]) + abs(left_point[1] - right_point[1])


def event_point(event: SessionEventRecord) -> tuple[float, float] | None:
    width = event.target.bbox_width
    height = event.target.bbox_height
    left = event.target.bbox_x
    top = event.target.bbox_y
    if width is not None and height is not None and left is not None and top is not None:
        safe_width = float(width)
        safe_height = float(height)
        safe_left = float(left)
        safe_top = float(top)
        return safe_left + safe_width / 2, safe_top + safe_height / 2
    if event.x is None or event.y is None:
        return None
    return event.x, event.y


def transcript_window(transcript: list[TranscriptSegment], start: float, end: float) -> str:
    parts = [segment.text.strip() for segment in transcript if segment.end >= start and segment.start <= end and segment.text.strip()]
    return " ".join(parts)


def box_center_delta(left: FocusBox | None, right: FocusBox | None) -> float:
    if left is None or right is None:
        return 0.0
    left_center = (left.x + left.width / 2, left.y + left.height / 2)
    right_center = (right.x + right.width / 2, right.y + right.height / 2)
    return abs(left_center[0] - right_center[0]) + abs(left_center[1] - right_center[1])


def contains_any(text: str, words: frozenset[str]) -> bool:
    return any(word in text for word in words)


def denormalize_box(
    focus_box: FocusBox | None,
    viewport_width: int,
    viewport_height: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    if focus_box is None:
        return None, None, None, None
    width = round(focus_box.width * viewport_width, 2)
    height = round(focus_box.height * viewport_height, 2)
    x = round((focus_box.x + focus_box.width / 2) * viewport_width, 2)
    y = round((focus_box.y + focus_box.height / 2) * viewport_height, 2)
    return x, y, width, height


def best_matching_ui_box(frame: FrameSignalRecord) -> FocusBox | None:
    if not frame.ui_elements:
        return None
    ranked = sorted(
        (element for element in frame.ui_elements if element.box is not None),
        key=ui_element_rank,
        reverse=True,
    )
    return ranked[0].box if ranked else None


def ui_element_rank(element: UiElementRecord) -> tuple[float, float, float]:
    label = normalize_label(element.label)
    label_quality = 0.0 if low_signal_label(label) else min(len(label) / 18.0, 1.0)
    box_area = element.box.width * element.box.height if element.box is not None else 0.0
    compactness = max(0.0, 0.08 - abs(box_area - 0.08))
    return (label_quality, element.confidence, compactness)


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
