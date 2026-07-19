from __future__ import annotations

from typing import Literal, Sequence

from app.models.projects import FocusBox, FrameSignalRecord, SessionEventRecord, SessionEventType, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.action_classifier import classify_action
from app.services.inferred_recording_support import (
    InteractionWindow,
    actionable_label,
    box_area,
    build_session_event,
    fallback_intent_label,
    intent_overlap_score,
    intent_tokens,
    low_signal_label,
    normalize_label,
    state_like_label,
)
from app.services.scene_intent_resolver import resolve_scene_intent
from app.services.walkthrough_guardrails import meaningful_event_count, sparse_action_count

RecoverySceneState = Literal["action", "result_state", "explanation_hold"]

MIN_RECOVERY_SCORE = 0.4
STRICT_RECOVERY_SCORE = 0.5
MAX_RECOVERY_EVENTS = 8
CLICK_WORDS = frozenset({"click", "tap", "press", "select", "open", "start", "login", "log in"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "course", "continue", "next"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password"})
EXPLANATION_WORDS = frozenset({"here", "notice", "now", "this", "where", "you can", "you will"})


def needs_action_recovery(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
) -> bool:
    duration = max((segment.end for segment in transcript), default=0.0)
    return sparse_action_count(meaningful_event_count(events), duration)


def recover_events_from_analyses(
    transcript: Sequence[TranscriptSegment],
    analyses: Sequence[VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
    strict: bool = False,
) -> list[SessionEventRecord]:
    recovered: list[SessionEventRecord] = []
    for analysis in sorted(analyses, key=lambda item: item.start):
        event = recovered_event(analysis, transcript, viewport_width, viewport_height, strict)
        if event is not None:
            recovered.append(event)
    return recovered[:MAX_RECOVERY_EVENTS]


def recovered_event(
    analysis: VisualSceneAnalysisRecord,
    transcript: Sequence[TranscriptSegment],
    viewport_width: int,
    viewport_height: int,
    strict: bool,
) -> SessionEventRecord | None:
    transcript_excerpt = transcript_window(transcript, analysis.start, analysis.end)
    if not transcript_excerpt and not analysis.visible_labels:
        return None
    label = recovered_label(analysis, transcript_excerpt)
    frame = best_recovery_frame(analysis)
    if not label or frame is None:
        return None
    scene_state = recovery_scene_state(analysis, transcript_excerpt, label, frame)
    event_type = recovered_event_type(transcript_excerpt, label, scene_state)
    score = recovered_score(analysis, frame, transcript_excerpt, label, scene_state)
    if not valid_recovered_event(label, transcript_excerpt, analysis, frame, score, scene_state, strict):
        return None
    return build_recovered_event(
        analysis, frame, label, transcript_excerpt, event_type, scene_state, score, viewport_width, viewport_height,
    )


def transcript_window(
    transcript: Sequence[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    parts = [segment.text.strip() for segment in transcript if segment.end >= start and segment.start <= end and segment.text.strip()]
    return " ".join(parts)


def recovered_label(analysis: VisualSceneAnalysisRecord, transcript_excerpt: str) -> str:
    labels = label_pool(analysis)
    resolution = resolve_scene_intent(transcript_excerpt, analysis.summary)
    tokens = resolution.focus_tokens or intent_tokens(transcript_excerpt)
    ranked = sorted(
        dict.fromkeys(labels),
        key=lambda label: recovery_label_rank(label, tokens, resolution.negative_tokens),
        reverse=True,
    )
    if not ranked:
        return fallback_intent_label(transcript_excerpt, analysis.summary)
    preferred = ranked[0]
    fallback = fallback_intent_label(transcript_excerpt, analysis.summary)
    if should_use_recovery_fallback(preferred, fallback, labels):
        return fallback
    return preferred


def recovery_label_rank(
    label: str,
    tokens: set[str],
    negative_tokens: set[str],
) -> tuple[float, float, float]:
    normalized_tokens = set(normalize_label(label).split())
    penalty = 1.0 if negative_tokens.intersection(normalized_tokens) else 0.0
    return (
        intent_overlap_score(label, tokens) - penalty,
        0.0 if low_signal_label(label) else 1.0,
        len(label),
    )


def label_pool(analysis: VisualSceneAnalysisRecord) -> list[str]:
    labels = [label.strip() for label in analysis.visible_labels if label and label.strip()]
    labels.extend(element.label.strip() for frame in analysis.frames for element in frame.ui_elements if element.label.strip())
    labels.extend(label.strip() for frame in analysis.frames for label in frame.ocr_labels if label.strip())
    return labels


def should_use_recovery_fallback(preferred: str, fallback: str, labels: list[str]) -> bool:
    if not fallback or not actionable_label(fallback):
        return False
    if not state_like_label(preferred):
        return False
    normalized_fallback = normalize_label(fallback)
    return normalized_fallback in {normalize_label(label) for label in labels}


def best_recovery_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    if not analysis.frames:
        return None
    return max(analysis.frames, key=frame_recovery_score)


def frame_recovery_score(frame: FrameSignalRecord) -> float:
    return frame.click_confidence * 0.4 + frame.importance_score * 0.32 + frame.diff_score * 0.28


def recovery_scene_state(
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    label: str,
    frame: FrameSignalRecord,
) -> RecoverySceneState:
    if recovery_action_signal_count(analysis, transcript_excerpt, frame) >= 2:
        return "action"
    if stable_recovery_scene(analysis, frame) and contains_explanation_cue(transcript_excerpt):
        return "explanation_hold"
    if state_like_label(label) or stable_recovery_scene(analysis, frame):
        return "result_state"
    return "action"


def recovery_action_signal_count(
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    frame: FrameSignalRecord,
) -> int:
    transcript_text = transcript_excerpt.lower()
    signals = 0
    if any(word in transcript_text for word in CLICK_WORDS | INPUT_WORDS | NAVIGATION_WORDS):
        signals += 1
    if frame.click_target_box is not None and box_area(frame.click_target_box) <= 0.16:
        signals += 1
    if max(frame.click_confidence, frame.diff_score, analysis.motion_score) >= 0.5:
        signals += 1
    if analysis.click_detected or analysis.cursor_path_confidence >= 0.45:
        signals += 1
    return signals


def stable_recovery_scene(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
) -> bool:
    return analysis.motion_score <= 0.22 and frame.diff_score <= 0.24 and frame.click_confidence <= 0.34


def contains_explanation_cue(transcript_excerpt: str) -> bool:
    text = transcript_excerpt.lower()
    return any(phrase in text for phrase in EXPLANATION_WORDS)


def recovered_event_type(
    transcript_excerpt: str,
    label: str,
    scene_state: RecoverySceneState,
) -> SessionEventType:
    if scene_state != "action":
        return "focus"
    text = f"{transcript_excerpt} {label}".lower()
    if any(word in text for word in INPUT_WORDS):
        return "input"
    if any(word in text for word in CLICK_WORDS):
        return "click"
    if any(word in text for word in NAVIGATION_WORDS):
        return "navigation"
    return "focus"


def recovered_score(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    label: str,
    scene_state: RecoverySceneState,
) -> float:
    token_overlap = intent_overlap_score(label, intent_tokens(transcript_excerpt))
    frame_score = frame_recovery_score(frame)
    label_score = 0.0 if low_signal_label(label) else 0.18
    state_bonus = 0.12 if scene_state == "action" else 0.08 if scene_state == "result_state" else 0.06
    return round(min(frame_score * 0.62 + token_overlap * 0.24 + label_score + state_bonus, 1.0), 3)


def valid_recovered_event(
    label: str,
    transcript_excerpt: str,
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    score: float,
    scene_state: RecoverySceneState,
    strict: bool,
) -> bool:
    transcript_match = intent_overlap_score(label, intent_tokens(transcript_excerpt))
    compact_focus = compact_recovery_focus(frame, analysis)
    min_score = STRICT_RECOVERY_SCORE if strict else MIN_RECOVERY_SCORE
    if scene_state == "action":
        return valid_action_recovery(label, transcript_excerpt, analysis, frame, transcript_match, compact_focus, score, min_score)
    if strict and state_like_label(label):
        return False
    return valid_state_recovery(label, analysis, transcript_match, compact_focus, score, min_score)


def compact_recovery_focus(
    frame: FrameSignalRecord,
    analysis: VisualSceneAnalysisRecord,
) -> bool:
    focus_box = frame.click_target_box or frame.dominant_box or analysis.click_target_box or analysis.primary_focus_box
    return focus_box is not None and box_area(focus_box) <= 0.16


def valid_action_recovery(
    label: str,
    transcript_excerpt: str,
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    transcript_match: float,
    compact_focus: bool,
    score: float,
    min_score: float,
) -> bool:
    evidence_count = recovery_action_signal_count(analysis, transcript_excerpt, frame)
    resolution = resolve_scene_intent(transcript_excerpt, analysis.summary)
    if resolution.negative_tokens.intersection(set(normalize_label(label).split())) and transcript_match < 0.5:
        return False
    if not actionable_label(label) and transcript_match < 0.36:
        return False
    return transcript_match >= 0.2 and compact_focus and score >= min_score and evidence_count >= 3


def valid_state_recovery(
    label: str,
    analysis: VisualSceneAnalysisRecord,
    transcript_match: float,
    compact_focus: bool,
    score: float,
    min_score: float,
) -> bool:
    if not state_like_label(label) and not actionable_label(label):
        return False
    stable_scene = analysis.motion_score <= 0.22 and analysis.frame_diff_score <= 0.28
    return transcript_match >= 0.18 and compact_focus and stable_scene and score >= min_score


def build_recovered_event(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    label: str,
    transcript_excerpt: str,
    event_type: SessionEventType,
    scene_state: RecoverySceneState,
    score: float,
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord:
    focus_box = recovered_focus_box(analysis, frame)
    window = InteractionWindow(
        timestamp=round(frame.timestamp, 2),
        score=score,
        event_type=event_type,
        label=label,
        text=analysis.summary,
        focus_box=focus_box,
        transcript_excerpt=transcript_excerpt,
    )
    event = build_session_event(window, analysis.scene_number, viewport_width, viewport_height)
    event.metadata["scene_state"] = scene_state
    event.metadata["action_class"] = recovery_action_class(event_type, label, transcript_excerpt, analysis.summary, scene_state)
    return event


def recovered_focus_box(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
) -> FocusBox | None:
    return frame.click_target_box or frame.dominant_box or analysis.click_target_box or analysis.primary_focus_box or analysis.anchor_box


def recovery_action_class(
    event_type: SessionEventType,
    label: str,
    transcript_excerpt: str,
    summary: str,
    scene_state: RecoverySceneState,
) -> str:
    if scene_state == "result_state":
        return "result_state"
    if scene_state == "explanation_hold":
        return "explanatory_hold"
    return classify_action(event_type, label, transcript_excerpt, summary)
