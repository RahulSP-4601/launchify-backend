from __future__ import annotations

from typing import Sequence

from app.models.projects import FocusBox, FrameSignalRecord, SessionEventRecord, SessionEventType, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import (
    InteractionWindow,
    actionable_label,
    build_session_event,
    fallback_intent_label,
    intent_overlap_score,
    intent_tokens,
    low_signal_label,
    state_like_label,
)
from app.services.walkthrough_guardrails import meaningful_event_count, sparse_action_count

MIN_RECOVERY_SCORE = 0.4
MAX_RECOVERY_EVENTS = 8
CLICK_WORDS = frozenset({"click", "tap", "press", "select", "open", "start", "login", "log in"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "course", "continue", "next"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password"})


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
) -> list[SessionEventRecord]:
    recovered: list[SessionEventRecord] = []
    for analysis in sorted(analyses, key=lambda item: item.start):
        event = recovered_event(analysis, transcript, viewport_width, viewport_height)
        if event is not None:
            recovered.append(event)
    return recovered[:MAX_RECOVERY_EVENTS]


def recovered_event(
    analysis: VisualSceneAnalysisRecord,
    transcript: Sequence[TranscriptSegment],
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord | None:
    transcript_excerpt = transcript_window(transcript, analysis.start, analysis.end)
    if not transcript_excerpt and not analysis.visible_labels:
        return None
    label = recovered_label(analysis, transcript_excerpt)
    if not label:
        return None
    frame = best_recovery_frame(analysis)
    event_type = recovered_event_type(transcript_excerpt, label)
    score = recovered_score(analysis, frame, transcript_excerpt, label)
    if score < MIN_RECOVERY_SCORE or not valid_recovered_event(label, transcript_excerpt, analysis, frame, score):
        return None
    focus_box = recovered_focus_box(analysis, frame)
    window = InteractionWindow(
        timestamp=round(best_recovery_timestamp(analysis, frame), 2),
        score=score,
        event_type=event_type,
        label=label,
        text=analysis.summary,
        focus_box=focus_box,
        transcript_excerpt=transcript_excerpt,
    )
    return build_session_event(window, analysis.scene_number, viewport_width, viewport_height)


def transcript_window(
    transcript: Sequence[TranscriptSegment],
    start: float,
    end: float,
) -> str:
    parts = [segment.text.strip() for segment in transcript if segment.end >= start and segment.start <= end and segment.text.strip()]
    return " ".join(parts)


def recovered_label(analysis: VisualSceneAnalysisRecord, transcript_excerpt: str) -> str:
    labels = [label.strip() for label in analysis.visible_labels if label and label.strip()]
    labels.extend(element.label.strip() for frame in analysis.frames for element in frame.ui_elements if element.label.strip())
    labels.extend(label.strip() for frame in analysis.frames for label in frame.ocr_labels if label.strip())
    tokens = intent_tokens(transcript_excerpt)
    ranked = sorted(
        dict.fromkeys(labels),
        key=lambda label: (
            intent_overlap_score(label, tokens),
            0.0 if low_signal_label(label) else 1.0,
            len(label),
        ),
        reverse=True,
    )
    if ranked:
        preferred = ranked[0]
        fallback = fallback_intent_label(transcript_excerpt, analysis.summary)
        if state_like_label(preferred) and fallback and actionable_label(fallback):
            return fallback
        return preferred
    return fallback_intent_label(transcript_excerpt, analysis.summary)


def best_recovery_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    if not analysis.frames:
        return None
    return max(
        analysis.frames,
        key=lambda frame: frame.click_confidence * 0.4 + frame.importance_score * 0.32 + frame.diff_score * 0.28,
    )


def best_recovery_timestamp(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord | None,
) -> float:
    if frame is not None:
        return frame.timestamp
    return analysis.start + max((analysis.end - analysis.start) * 0.5, 0.0)


def recovered_focus_box(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord | None,
) -> FocusBox | None:
    if frame is not None:
        return frame.click_target_box or frame.dominant_box or frame.cursor_box
    return analysis.click_target_box or analysis.primary_focus_box or analysis.anchor_box or analysis.cursor_box


def recovered_event_type(transcript_excerpt: str, label: str) -> SessionEventType:
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
    frame: FrameSignalRecord | None,
    transcript_excerpt: str,
    label: str,
) -> float:
    token_overlap = intent_overlap_score(label, intent_tokens(transcript_excerpt))
    frame_score = 0.0
    if frame is not None:
        frame_score = frame.click_confidence * 0.38 + frame.importance_score * 0.22 + frame.diff_score * 0.2
    label_score = 0.0 if low_signal_label(label) else 0.2
    return round(min(frame_score + token_overlap * 0.32 + label_score, 1.0), 3)


def valid_recovered_event(
    label: str,
    transcript_excerpt: str,
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord | None,
    score: float,
) -> bool:
    transcript_match = intent_overlap_score(label, intent_tokens(transcript_excerpt))
    visual_strength = analysis.confidence
    compact_focus = False
    if frame is not None:
        visual_strength = max(visual_strength, frame.click_confidence, frame.importance_score, frame.diff_score)
        focus_box = frame.click_target_box or frame.dominant_box or frame.cursor_box
        compact_focus = focus_box is not None and focus_box.width * focus_box.height <= 0.14
    if state_like_label(label):
        return transcript_match >= 0.42 and visual_strength >= 0.56 and compact_focus and score >= 0.5
    if not actionable_label(label):
        return transcript_match >= 0.36 and visual_strength >= 0.54 and compact_focus and score >= 0.48
    evidence_count = 0
    if transcript_match >= 0.22:
        evidence_count += 1
    if visual_strength >= 0.62:
        evidence_count += 1
    if compact_focus:
        evidence_count += 1
    if any(word in transcript_excerpt.lower() for word in CLICK_WORDS | INPUT_WORDS | NAVIGATION_WORDS):
        evidence_count += 1
    return evidence_count >= 2
