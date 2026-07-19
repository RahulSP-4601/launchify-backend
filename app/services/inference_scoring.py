from __future__ import annotations

from app.models.projects import FrameSignalRecord
from app.services.inferred_recording_support import box_area, intent_tokens


def action_frame_bonus(frame: FrameSignalRecord) -> float:
    focus_box = frame.click_target_box or frame.cursor_box
    compact_focus = focus_box is not None and box_area(focus_box) <= 0.14
    if compact_focus and frame.click_confidence >= 0.55:
        return 0.1
    if compact_focus and frame.click_confidence >= 0.35:
        return 0.05
    return 0.0


def result_state_penalty(
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    source_excerpt: str,
    frame_label_set: set[str],
) -> float:
    intent = intent_tokens(transcript_excerpt, source_excerpt)
    focus_box = frame.click_target_box or frame.dominant_box
    if focus_box is None or box_area(focus_box) < 0.18:
        return 0.0
    if {"choose", "account"} <= frame_label_set and not {"choose", "account"} <= intent:
        return 0.28
    if any(token in frame_label_set for token in {"dashboard", "level", "levels"}) and frame.click_confidence <= 0.2:
        return 0.2
    return 0.08 if frame.diff_score >= 0.9 and frame.click_confidence <= 0.25 else 0.0
