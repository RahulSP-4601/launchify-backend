from __future__ import annotations

from app.models.projects import FrameSignalRecord, UiElementRecord, VisualSceneAnalysisRecord
from app.services.generic_target_labeling import title_case_phrase
from app.services.inferred_recording_support import intent_overlap_score, low_signal_label, normalize_label, state_like_label
from app.services.scene_intent_resolver import SceneIntentResolution

ACTION_VERBS = frozenset({"continue", "open", "select", "start", "view"})


def contextual_target_label(
    current_label: str,
    analysis: VisualSceneAnalysisRecord | None,
    resolution: SceneIntentResolution,
    action_timestamp: float | None = None,
) -> str:
    if analysis is None:
        return ""
    if not has_focus_intent(resolution):
        return ""
    entity = best_matching_entity_label(analysis, resolution, action_timestamp)
    if not entity:
        return ""
    verb = action_verb(current_label)
    if verb:
        return title_case_phrase(f"{verb} {entity}")
    return title_case_phrase(entity)


def exact_visual_target_label(
    analysis: VisualSceneAnalysisRecord | None,
    action_timestamp: float | None,
    entity: str,
) -> str:
    if analysis is None or not entity:
        return ""
    frame_pool = relevant_frames(analysis, action_timestamp)
    ranked = sorted(
        (
            element
            for frame in frame_pool
            for element in frame.ui_elements
            if exact_target_candidate(element.label, entity)
        ),
        key=lambda element: exact_target_rank(element.label, entity),
        reverse=True,
    )
    return ranked[0].label.strip() if ranked else ""


def best_matching_entity_label(
    analysis: VisualSceneAnalysisRecord,
    resolution: SceneIntentResolution,
    action_timestamp: float | None = None,
) -> str:
    if not has_focus_intent(resolution):
        return ""
    frame_pool = relevant_frames(analysis, action_timestamp)
    ranked = sorted(
        (
            (frame, element)
            for frame in frame_pool
            for element in frame.ui_elements
            if valid_entity_label(element)
        ),
        key=lambda item: entity_rank(item[1], resolution, item[0].timestamp, action_timestamp),
        reverse=True,
    )
    if not ranked:
        return ""
    top_frame, top = ranked[0]
    top_score = entity_rank(top, resolution, top_frame.timestamp, action_timestamp)[0]
    return top.label if top_score >= 0.34 else ""


def entity_rank(
    element: UiElementRecord,
    resolution: SceneIntentResolution,
    frame_timestamp: float,
    action_timestamp: float | None,
) -> tuple[float, float, float, float]:
    focus_tokens = resolution.focus_tokens or set(normalize_label(resolution.focus_phrase).split())
    return (
        intent_overlap_score(element.label, focus_tokens),
        1.0 if normalize_label(element.label) == normalize_label(resolution.focus_phrase) else 0.0,
        action_frame_relevance(frame_timestamp, action_timestamp),
        len(normalize_label(element.label).split()) / 6.0,
    )


def relevant_frames(
    analysis: VisualSceneAnalysisRecord,
    action_timestamp: float | None,
) -> list[FrameSignalRecord]:
    if not analysis.frames:
        return []
    if action_timestamp is None:
        cutoff = analysis.start + (analysis.end - analysis.start) * 0.6
        early_frames = [frame for frame in analysis.frames if frame.timestamp <= cutoff]
        return early_frames or analysis.frames
    nearby = [
        frame
        for frame in analysis.frames
        if action_timestamp - 1.0 <= frame.timestamp <= action_timestamp + 0.35
    ]
    if nearby:
        return nearby
    preceding = [frame for frame in analysis.frames if frame.timestamp <= action_timestamp + 0.1]
    return preceding[-2:] or analysis.frames[:2] or analysis.frames


def action_frame_relevance(frame_timestamp: float, action_timestamp: float | None) -> float:
    if action_timestamp is None:
        return 0.0
    delta = frame_timestamp - action_timestamp
    if delta <= 0:
        return max(0.0, 1.0 - abs(delta) / 1.2)
    return max(0.0, 0.5 - delta / 0.7)


def valid_entity_label(element: UiElementRecord) -> bool:
    normalized = normalize_label(element.label)
    if not normalized or low_signal_label(element.label) or state_like_label(element.label):
        return False
    if action_verb(element.label):
        return False
    if len(normalized.split()) > 6:
        return False
    return True


def action_verb(label: str) -> str:
    tokens = normalize_label(label).split()
    return next((token for token in tokens if token in ACTION_VERBS), "")


def has_focus_intent(resolution: SceneIntentResolution) -> bool:
    return bool(resolution.focus_phrase.strip() or resolution.focus_tokens)


def exact_target_candidate(label: str, entity: str) -> bool:
    normalized = normalize_label(label)
    if not normalized or low_signal_label(label):
        return False
    tokens = set(normalized.split())
    if entity in {"japan", "japanese"}:
        return "japanese" in tokens
    return entity in tokens


def exact_target_rank(label: str, entity: str) -> tuple[float, float]:
    tokens = set(normalize_label(label).split())
    entity_match = 1.0 if (entity in {"japan", "japanese"} and "japanese" in tokens) or entity in tokens else 0.0
    exactness = 1.0 if len(tokens) == 1 else 0.6 if "course" in tokens else 0.4
    return (entity_match, exactness)
