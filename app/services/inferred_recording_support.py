from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.projects import (
    FocusBox,
    FrameSignalRecord,
    SessionEventRecord,
    SessionEventType,
    SessionTargetRecord,
    TranscriptSegment,
    UiElementRecord,
    VisualSceneAnalysisRecord,
)

INTERACTION_GAP_SECONDS = 0.55
EVENT_FOCUS_DISTANCE_PIXELS = 48.0
MIN_DISTINCT_WINDOW_SECONDS = 0.9
MIN_WINDOW_SCORE = 0.32
SYNTHETIC_DUPLICATE_GAP_SECONDS = 1.8
GENERIC_LABELS = frozenset(
    {
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
        "learning",
        "login",
    }
)
LOW_SIGNAL_TOKENS = frozenset(
    {
        "and",
        "for",
        "from",
        "into",
        "language",
        "the",
        "this",
        "that",
        "with",
        "your",
    }
)


@dataclass(frozen=True)
class InteractionWindow:
    timestamp: float
    score: float
    event_type: SessionEventType
    label: str
    text: str
    focus_box: FocusBox | None
    transcript_excerpt: str


def low_signal_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not normalized or normalized in GENERIC_LABELS or normalized.startswith("continue "):
        return True
    tokens = normalized.split()
    if len(tokens) == 1 and (tokens[0] in LOW_SIGNAL_TOKENS or len(tokens[0]) <= 3):
        return True
    return len(normalized) < 4


def fallback_intent_label(transcript_excerpt: str, source_excerpt: str) -> str:
    combined = f"{transcript_excerpt} {source_excerpt}".strip()
    if not combined:
        return ""
    patterns = (
        (r"\bgoogle login\b", "Google Login"),
        (r"\blog in with google\b", "Log In With Google"),
        (r"\bsign up with google\b", "Sign Up With Google"),
        (r"\bjapanese(?: basic 1)? course\b", "Japanese Course"),
        (r"\bcreate account\b", "Create Account"),
        (r"\blog in\b", "Log In"),
        (r"\bsign up\b", "Sign Up"),
        (r"\bstart free\b", "Start Free"),
    )
    lowered = combined.lower()
    for pattern, label in patterns:
        if re.search(pattern, lowered):
            return label
    sentence_label = sentence_fallback_label(combined)
    return "" if low_signal_label(sentence_label) else sentence_label


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


def canonical_scene_windows(
    windows: list[InteractionWindow],
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[InteractionWindow]:
    ranked = sorted(
        (
            window
            for window in windows
            if keepable_window(window, analysis, transcript_excerpt, source_excerpt)
        ),
        key=lambda item: canonical_window_rank(item, analysis, transcript_excerpt, source_excerpt),
        reverse=True,
    )
    selected: list[InteractionWindow] = []
    for window in ranked:
        if any(window_conflicts(window, existing) or should_merge(existing, window) for existing in selected):
            continue
        selected.append(window)
    return selected


def keepable_window(
    window: InteractionWindow,
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> bool:
    label = normalize_label(window.label)
    transcript_match = intent_overlap_score(window.label, intent_tokens(transcript_excerpt, source_excerpt))
    has_direct_click_signal = analysis.click_detected or "login" in label or transcript_match >= 0.2
    if low_signal_label(window.label) and not has_direct_click_signal:
        return False
    return window.score >= MIN_WINDOW_SCORE


def canonical_window_rank(
    window: InteractionWindow,
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> tuple[float, float, float, float, float]:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    label_overlap = intent_overlap_score(window.label, tokens)
    label_quality = 0.0 if low_signal_label(window.label) else min(len(normalize_label(window.label)) / 18.0, 1.0)
    click_bonus = 1.0 if (analysis.click_detected or window.event_type == "click") else 0.0
    compact_focus = 0.0 if window.focus_box is None else max(0.0, 0.12 - (window.focus_box.width * window.focus_box.height))
    return (window.score, click_bonus, label_overlap, label_quality, compact_focus)


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
            selector="",
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
            "synthetic_selector": f"[data-launchify-scene='{scene_number}']",
            "score": f"{window.score:.2f}",
            "transcript_excerpt": window.transcript_excerpt[:180],
        },
    )


def dedupe_events(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    deduped: list[SessionEventRecord] = []
    for event in events:
        duplicate_index = next((index for index, existing in enumerate(deduped) if duplicate_event(existing, event)), None)
        if duplicate_index is not None:
            if float(event.metadata.get("score", "0")) > float(deduped[duplicate_index].metadata.get("score", "0")):
                deduped[duplicate_index] = event
            continue
        deduped.append(event)
    return deduped


def duplicate_event(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    same_label = normalize_label(left.target.label) == normalize_label(right.target.label)
    same_scene = left.metadata.get("scene_number") == right.metadata.get("scene_number")
    close_in_time = abs(left.timestamp - right.timestamp) <= INTERACTION_GAP_SECONDS
    close_in_inferred_time = abs(left.timestamp - right.timestamp) <= SYNTHETIC_DUPLICATE_GAP_SECONDS if same_scene else close_in_time
    same_type = left.type == right.type
    same_focus = focus_distance(left, right) <= EVENT_FOCUS_DISTANCE_PIXELS
    return close_in_inferred_time and same_type and (same_label or same_focus)


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


def best_matching_ui_box(frame: FrameSignalRecord) -> FocusBox | None:
    if not frame.ui_elements:
        return None
    ranked = sorted((element for element in frame.ui_elements if element.box is not None), key=ui_element_rank, reverse=True)
    return ranked[0].box if ranked else None


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
        return float(left) + float(width) / 2, float(top) + float(height) / 2
    if event.x is None or event.y is None:
        return None
    return event.x, event.y


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


def ui_element_rank(element: UiElementRecord) -> tuple[float, float, float]:
    label = normalize_label(element.label)
    label_quality = 0.0 if low_signal_label(label) else min(len(label) / 18.0, 1.0)
    box_area = element.box.width * element.box.height if element.box is not None else 0.0
    compactness = max(0.0, 0.08 - abs(box_area - 0.08))
    return (label_quality, element.confidence, compactness)
