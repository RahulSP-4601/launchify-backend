from __future__ import annotations

from app.models.projects import FocusBox, FrameSignalRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import box_center_delta, intent_overlap_score, intent_tokens, normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent


def focused_excerpt(
    excerpt: str,
    analysis: VisualSceneAnalysisRecord,
    index: int,
) -> str:
    if not excerpt:
        return analysis.summary
    resolution = resolve_scene_intent(excerpt, analysis.summary, frame_progress(index, len(analysis.frames)))
    return resolution.preferred_clause or excerpt


def valid_action_outcome(
    frames: list[FrameSignalRecord],
    index: int,
    label: str,
    focus_box: FocusBox | None,
    transcript_excerpt: str,
    source_excerpt: str,
) -> bool:
    if index + 1 >= len(frames):
        return True
    transcript_match = intent_overlap_score(label, intent_tokens(transcript_excerpt, source_excerpt))
    return transcript_match >= 0.45 or outcome_score(frames[index], frames[index + 1], focus_box) >= 0.18


def sequence_action_score(
    frames: list[FrameSignalRecord],
    index: int,
    focus_box: FocusBox | None,
) -> float:
    current = frames[index]
    previous = frames[index - 1] if index > 0 else None
    following = frames[index + 1] if index + 1 < len(frames) else None
    score = (
        cursor_alignment_score(current, focus_box, previous) * 0.46
        + focus_change_score(following, focus_box) * 0.24
        + max(current.diff_score, current.click_confidence) * 0.3
    )
    return round(min(score, 1.0), 3)


def outcome_score(
    current: FrameSignalRecord,
    following: FrameSignalRecord,
    focus_box: FocusBox | None,
) -> float:
    label_shift = label_set_delta(current, following)
    motion = max(following.diff_score, following.importance_score)
    if label_shift >= 0.22 or motion >= 0.34:
        return max(label_shift, motion)
    follow_focus = following.click_target_box or following.dominant_box
    return max(0.0, 0.24 - box_center_delta(focus_box, follow_focus))


def cursor_alignment_score(
    current: FrameSignalRecord,
    focus_box: FocusBox | None,
    previous: FrameSignalRecord | None,
) -> float:
    if current.cursor_box is None or focus_box is None:
        return 0.0
    proximity = max(0.0, 0.24 - box_center_delta(current.cursor_box, focus_box))
    movement = 0.0 if previous is None else max(0.0, box_center_delta(previous.cursor_box, current.cursor_box) - 0.01)
    return min(proximity * 3.0 + movement * 2.0, 1.0)


def focus_change_score(
    following: FrameSignalRecord | None,
    focus_box: FocusBox | None,
) -> float:
    if following is None or focus_box is None:
        return 0.0
    next_focus = following.click_target_box or following.dominant_box
    return max(0.0, 0.22 - box_center_delta(focus_box, next_focus)) * 3.0


def label_set_delta(left: FrameSignalRecord, right: FrameSignalRecord) -> float:
    left_labels = {normalize_label(element.label) for element in left.ui_elements if element.label.strip()}
    right_labels = {normalize_label(element.label) for element in right.ui_elements if element.label.strip()}
    if not left_labels and not right_labels:
        return 0.0
    overlap = len(left_labels & right_labels)
    union = max(len(left_labels | right_labels), 1)
    return round(1.0 - overlap / union, 3)


def frame_progress(index: int, total_frames: int) -> float:
    if total_frames <= 1:
        return 0.5
    return index / max(total_frames - 1, 1)
