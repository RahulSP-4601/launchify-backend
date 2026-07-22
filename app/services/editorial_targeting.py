from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox, FrameSignalRecord, LaunchScriptScene, TranscriptSegment, UiElementRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import fallback_intent_label, intent_overlap_score, intent_tokens, normalize_label
from app.services.inferred_target_ranking import RankedTarget, select_ranked_target
from app.services.structured_visual_candidates import structured_visual_candidates

ACTION_RESULT_PADDING = 0.82
ACTION_RESULT_MIN_GAP = 0.34


@dataclass(frozen=True)
class EditorialTargetDecision:
    label: str
    focus_box: FocusBox | None
    score: float
    clear_winner: bool
    evidence_count: int


@dataclass(frozen=True)
class ActionEnvelope:
    action_time: float | None
    response_time: float | None
    focus_start: float | None
    focus_end: float | None
    settle_end: float | None
    recommended_end: float
    completeness_score: float
    target_label: str
    target_box: FocusBox | None


def resolve_editorial_target(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord | None,
) -> EditorialTargetDecision | None:
    candidates = target_candidates(scene, transcript, analysis)
    if not candidates:
        return fallback_target(scene, analysis)
    ranked = select_ranked_target(candidates, transcript_text(transcript), scene_text(scene), compact_focus_box(analysis))
    if ranked is not None:
        return target_from_ranked(ranked, len(candidates), analysis)
    return fallback_target(scene, analysis)


def build_action_envelope(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord | None,
    decision: EditorialTargetDecision | None,
    start_time: float = 0.0,
    end_time: float | None = None,
) -> ActionEnvelope:
    scene_start, scene_end = scene_window(scene, start_time, end_time)
    action_time = choose_action_time(scene_start, scene_end, analysis, decision)
    response_time = choose_response_time(scene_end, analysis, action_time, decision)
    focus_start = bounded_time(scene_start, scene_end, action_time - pre_action_seconds(scene)) if action_time is not None else None
    focus_end = bounded_time(scene_start, scene_end, max(action_time, (response_time or action_time) - 0.18)) if action_time is not None else None
    settle_end = bounded_settle_end(scene_end, analysis, response_time or action_time)
    completeness = completeness_score(scene, transcript, analysis, decision, action_time, response_time)
    return ActionEnvelope(
        action_time=action_time,
        response_time=response_time,
        focus_start=focus_start,
        focus_end=focus_end,
        settle_end=settle_end,
        recommended_end=max(scene_end, settle_end),
        completeness_score=completeness,
        target_label=decision.label if decision is not None else fallback_intent_label(transcript_text(transcript), scene_text(scene)),
        target_box=decision.focus_box if decision is not None else compact_focus_box(analysis),
    )


def target_candidates(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord | None,
) -> list[tuple[str, FocusBox | None, float]]:
    if analysis is None:
        return []
    candidates = frame_candidates(analysis, transcript_text(transcript), scene.source_excerpt)
    candidates.extend(label_candidates(analysis))
    deduped: dict[tuple[str, str], tuple[str, FocusBox | None, float]] = {}
    for label, box, weight in candidates:
        key = (normalize_label(label), box_signature(box))
        current = deduped.get(key)
        if current is None or weight > current[2]:
            deduped[key] = (label, box, weight)
    return list(deduped.values())


def frame_candidates(
    analysis: VisualSceneAnalysisRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[tuple[str, FocusBox | None, float]]:
    candidates: list[tuple[str, FocusBox | None, float]] = []
    for frame in ranked_frames(analysis):
        for candidate in structured_visual_candidates(frame, transcript_excerpt, source_excerpt):
            candidates.append((candidate.label, compact_box(candidate.box), candidate.source_weight + frame_weight(frame)))
        candidates.extend(element_candidates(frame, transcript_excerpt, source_excerpt))
    return candidates


def element_candidates(
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[tuple[str, FocusBox | None, float]]:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    ranked = sorted(frame.ui_elements, key=lambda item: element_weight(item, frame, tokens), reverse=True)
    return [(item.label, compact_box(item.box), element_weight(item, frame, tokens)) for item in ranked[:4] if item.label.strip()]


def label_candidates(analysis: VisualSceneAnalysisRecord) -> list[tuple[str, FocusBox | None, float]]:
    box = compact_focus_box(analysis)
    return [(label, box, 0.48) for label in analysis.visible_labels if label.strip()]


def target_from_ranked(
    ranked: RankedTarget,
    evidence_count: int,
    analysis: VisualSceneAnalysisRecord | None,
) -> EditorialTargetDecision:
    focus_box = ranked.focus_box or compact_focus_box(analysis)
    return EditorialTargetDecision(ranked.label, focus_box, ranked.score, ranked.clear_winner, evidence_count)


def fallback_target(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
) -> EditorialTargetDecision | None:
    label = fallback_intent_label(scene.spoken_line, scene.source_excerpt) or scene.specific_target_label or scene.on_screen_text
    if not label.strip():
        return None
    return EditorialTargetDecision(label.strip(), compact_focus_box(analysis), 0.32, False, 0)


def ranked_frames(analysis: VisualSceneAnalysisRecord) -> list[FrameSignalRecord]:
    return sorted(analysis.frames, key=frame_rank, reverse=True)[:5]


def frame_rank(frame: FrameSignalRecord) -> float:
    return frame.importance_score * 0.34 + frame.click_confidence * 0.3 + frame.diff_score * 0.2 + frame.ocr_confidence * 0.16


def frame_weight(frame: FrameSignalRecord) -> float:
    return round(0.36 + frame.click_confidence * 0.24 + frame.importance_score * 0.18 + frame.diff_score * 0.12, 3)


def element_weight(item: UiElementRecord, frame: FrameSignalRecord, tokens: set[str]) -> float:
    overlap = intent_overlap_score(item.label, tokens)
    area_bonus = 0.16 if compact_box(item.box) is not None else 0.0
    click_bonus = 0.18 if frame.click_target_box is not None else 0.08 if frame.cursor_box is not None else 0.0
    return round(item.confidence * 0.44 + overlap * 0.28 + click_bonus + area_bonus, 3)


def choose_action_time(
    scene_start: float,
    scene_end: float,
    analysis: VisualSceneAnalysisRecord | None,
    decision: EditorialTargetDecision | None,
) -> float | None:
    if analysis is None or not analysis.frames:
        return None
    ranked = sorted(analysis.frames, key=lambda frame: action_frame_score(frame, decision), reverse=True)
    if not ranked:
        return None
    return bounded_time(scene_start, scene_end, ranked[0].timestamp)


def choose_response_time(
    scene_end: float,
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
    decision: EditorialTargetDecision | None,
) -> float | None:
    if analysis is None or action_time is None:
        return None
    followups = [frame for frame in analysis.frames if frame.timestamp >= action_time + ACTION_RESULT_MIN_GAP]
    if not followups:
        return None
    ranked = sorted(followups, key=lambda frame: response_frame_score(frame, decision), reverse=True)
    if not ranked or response_frame_score(ranked[0], decision) < 0.18:
        return None
    return bounded_time(action_time, scene_end, ranked[0].timestamp)


def action_frame_score(frame: FrameSignalRecord, decision: EditorialTargetDecision | None) -> float:
    return frame_rank(frame) + label_match_bonus(frame, decision) + target_box_bonus(frame, decision)


def response_frame_score(frame: FrameSignalRecord, decision: EditorialTargetDecision | None) -> float:
    return frame.diff_score * 0.42 + frame.importance_score * 0.22 + frame.ocr_confidence * 0.18 + response_label_bonus(frame, decision)


def label_match_bonus(frame: FrameSignalRecord, decision: EditorialTargetDecision | None) -> float:
    if decision is None:
        return 0.0
    labels = [element.label for element in frame.ui_elements] + list(frame.ocr_labels)
    return max((intent_overlap_score(label, intent_tokens(decision.label)) for label in labels if label.strip()), default=0.0) * 0.26


def response_label_bonus(frame: FrameSignalRecord, decision: EditorialTargetDecision | None) -> float:
    if decision is None:
        return 0.0
    tokens = intent_tokens(decision.label)
    labels = [element.label for element in frame.ui_elements] + list(frame.ocr_labels)
    overlap = max((intent_overlap_score(label, tokens) for label in labels if label.strip()), default=0.0)
    return max(0.0, 0.16 - overlap * 0.08)


def target_box_bonus(frame: FrameSignalRecord, decision: EditorialTargetDecision | None) -> float:
    if decision is None or decision.focus_box is None:
        return 0.0
    box = frame.click_target_box or frame.cursor_box or frame.dominant_box
    if box is None:
        return 0.0
    return max(0.0, 0.18 - box_distance(box, decision.focus_box))


def bounded_settle_end(scene_end: float, analysis: VisualSceneAnalysisRecord | None, anchor: float | None) -> float:
    base = anchor if anchor is not None else scene_end
    limit = analysis.end if analysis is not None else scene_end
    return round(min(limit, max(scene_end, base + ACTION_RESULT_PADDING)), 2)


def completeness_score(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord | None,
    decision: EditorialTargetDecision | None,
    action_time: float | None,
    response_time: float | None,
) -> float:
    transcript_bonus = min(len(transcript_text(transcript).split()) / 24.0, 0.18)
    visual_bonus = (analysis.confidence * 0.24) if analysis is not None else 0.0
    target_bonus = min((decision.score if decision is not None else 0.0) * 0.28, 0.28)
    action_bonus = 0.18 if action_time is not None else 0.0
    response_bonus = 0.18 if response_time is not None else 0.0
    return round(min(1.0, 0.12 + transcript_bonus + visual_bonus + target_bonus + action_bonus + response_bonus), 3)


def scene_text(scene: LaunchScriptScene) -> str:
    return " ".join(part.strip() for part in (scene.purpose, scene.spoken_line, scene.on_screen_text, scene.source_excerpt) if part.strip())


def transcript_text(transcript: list[TranscriptSegment]) -> str:
    return " ".join(segment.text for segment in transcript if segment.text.strip())


def compact_focus_box(analysis: VisualSceneAnalysisRecord | None) -> FocusBox | None:
    if analysis is None:
        return None
    return compact_box(analysis.click_target_box) or compact_box(analysis.anchor_box) or compact_box(analysis.primary_focus_box) or compact_box(analysis.cursor_box)


def compact_box(box: FocusBox | None) -> FocusBox | None:
    if box is None:
        return None
    if box.width * box.height <= 0.18:
        return box
    width = min(max(box.width * 0.42, 0.1), 0.26)
    height = min(max(box.height * 0.34, 0.08), 0.22)
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    return FocusBox(x=round(clamp(center_x - width / 2, 0.0, 1.0 - width), 4), y=round(clamp(center_y - height / 2, 0.0, 1.0 - height), 4), width=round(width, 4), height=round(height, 4))


def pre_action_seconds(scene: LaunchScriptScene) -> float:
    if scene.estimated_duration_seconds >= 7.0:
        return 1.0
    if scene.estimated_duration_seconds >= 4.0:
        return 0.74
    return 0.52


def scene_window(scene: LaunchScriptScene, start_time: float, end_time: float | None) -> tuple[float, float]:
    end = end_time if end_time is not None else start_time + max(scene.estimated_duration_seconds, 0.8)
    return round(max(start_time, 0.0), 2), round(max(end, start_time + 0.8), 2)


def bounded_time(scene_start: float, scene_end: float, value: float) -> float:
    return round(min(scene_end, max(scene_start, value)), 2)


def box_distance(left: FocusBox, right: FocusBox) -> float:
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)


def box_signature(box: FocusBox | None) -> str:
    if box is None:
        return "none"
    return f"{box.x:.3f}:{box.y:.3f}:{box.width:.3f}:{box.height:.3f}"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
