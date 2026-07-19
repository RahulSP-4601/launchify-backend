from __future__ import annotations

from app.models.projects import FocusBox, FrameSignalRecord
from app.services.inferred_recording_support import (
    actionable_label,
    box_area,
    box_center_delta,
    fallback_intent_label,
    intent_overlap_score,
    intent_tokens,
    label_quality_score,
    low_signal_label,
    normalize_label,
    state_like_label,
)
from app.services.ui_structure_insights import compact_action_target, frame_local_labels, prefers_state_event, structure_state_label
from app.services.inferred_target_ranking import candidate_role, select_ranked_target
from app.services.structured_visual_candidates import structured_visual_candidates


def inferred_target_selection(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
    has_click_signal: bool = True,
) -> tuple[str, FocusBox | None] | None:
    local_labels = frame_local_labels(frame, visible_labels)
    if prefers_state_event(frame, local_labels) and (not has_click_signal or not compact_action_target(frame)):
        stable = structure_state_label(frame, local_labels) or stable_scene_label(frame, local_labels)
        if stable:
            return stable, focus_box
    if not has_click_signal:
        stable = stable_scene_label(frame, local_labels)
        if stable:
            return stable, focus_box
    ranked_target = select_ranked_target(
        unique_label_candidates(label_candidates(frame, local_labels, transcript_excerpt, source_excerpt)),
        transcript_excerpt,
        source_excerpt,
        focus_box,
    )
    if ranked_target is not None:
        return ranked_target.label, ranked_target.focus_box
    label = inferred_label(frame, local_labels, transcript_excerpt, source_excerpt, focus_box, has_click_signal)
    if not label or low_signal_label(label):
        return None
    role = candidate_role(label, transcript_excerpt, source_excerpt)
    if role in {"state_only", "supporting_context", "ambiguous"}:
        return None
    return label, focus_box


def inferred_label(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
    has_click_signal: bool,
) -> str:
    preferred = ranked_candidate_labels(frame, visible_labels, transcript_excerpt, source_excerpt, focus_box)
    fallback = fallback_intent_label(transcript_excerpt, source_excerpt)
    if not preferred:
        return fallback
    lead = preferred[0]
    if low_signal_label(lead):
        return fallback or lead
    fallback_supported = frame_supports_fallback_label(frame, visible_labels, fallback)
    if state_like_label(lead) and fallback and actionable_label(fallback) and fallback_supported:
        return fallback
    lead_intent = intent_overlap_score(lead, intent_tokens(transcript_excerpt, source_excerpt))
    if fallback and fallback_supported and lead_intent < 0.26:
        return fallback
    return lead


def stable_scene_label(
    frame: FrameSignalRecord,
    visible_labels: list[str],
) -> str:
    candidates = [
        (element.label.strip(), element.role)
        for element in frame.ui_elements
        if element.label.strip() and not auth_context_state_label(element.label)
    ]
    candidates.extend((label.strip(), "visible") for label in visible_labels if label.strip() and not auth_context_state_label(label))
    ranked = sorted(candidates, key=lambda item: stable_scene_rank(item[0], item[1]), reverse=True)
    return ranked[0][0] if ranked and stable_scene_rank(ranked[0][0], ranked[0][1]) >= 0.55 else ""


def stable_scene_rank(label: str, role: str) -> float:
    normalized = normalize_label(label)
    if not normalized or low_signal_label(label):
        return 0.0
    role_bonus = 0.34 if role in {"header", "heading", "instruction", "text"} else 0.12 if role == "visible" else 0.08
    state_bonus = 0.3 if any(phrase in normalized for phrase in ("select a course", "pick your", "choose your", "before you start")) else 0.0
    entity_bonus = 0.12 if normalized in {"japanese", "english", "german", "spanish", "french"} else 0.0
    return round(role_bonus + state_bonus + entity_bonus + label_quality_score(label) * 0.24, 3)


def auth_context_state_label(label: str) -> bool:
    tokens = set(normalize_label(label).split())
    return bool(tokens & {"google", "login", "log", "account", "sign"}) and "course" not in tokens


def frame_supports_fallback_label(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    fallback: str,
) -> bool:
    if not fallback:
        return False
    normalized_fallback = normalize_label(fallback)
    if not normalized_fallback:
        return False
    candidates = label_candidates(frame, visible_labels, fallback, "")
    candidate_labels = [label for label, _box, _weight in candidates if label.strip()]
    if normalized_fallback in {normalize_label(label) for label in candidate_labels}:
        return True
    overlaps = [intent_overlap_score(label, intent_tokens(fallback, "")) for label in candidate_labels]
    return max(overlaps, default=0.0) >= 0.58


def ranked_candidate_labels(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
) -> list[str]:
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    labels = unique_label_candidates(label_candidates(frame, visible_labels, transcript_excerpt, source_excerpt))
    unique = [candidate for candidate in labels if candidate[0] and candidate[0].strip() and not low_signal_label(candidate[0])]
    ranked = sorted(unique, key=lambda candidate: label_rank(candidate, tokens, frame, focus_box), reverse=True)
    return [label for label, _box, _source_weight in ranked]


def label_candidates(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[tuple[str, FocusBox | None, float]]:
    candidates: list[tuple[str, FocusBox | None, float]] = [
        (candidate.label, candidate.box, candidate.source_weight)
        for candidate in structured_visual_candidates(frame, transcript_excerpt, source_excerpt)
    ]
    candidates.extend(label_candidates_for_context(frame, visible_labels))
    return candidates


def label_candidates_for_context(
    frame: FrameSignalRecord,
    visible_labels: list[str],
) -> list[tuple[str, FocusBox | None, float]]:
    candidates: list[tuple[str, FocusBox | None, float]] = [
        (element.label, element.box, ui_source_weight(element.label, element.box, frame))
        for element in frame.ui_elements
        if trusted_candidate_label(element.label)
    ]
    candidates.extend((label, None, 0.34) for label in frame.ocr_labels if trusted_ocr_label(label))
    candidates.extend((label, None, 0.24) for label in visible_labels if trusted_candidate_label(label))
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
    focus_anchor = focus_box or frame.click_target_box or frame.cursor_box
    cursor_anchor = frame.cursor_box or focus_anchor
    focus_delta = box_center_delta(focus_anchor, candidate_box or focus_anchor)
    cursor_delta = box_center_delta(cursor_anchor, candidate_box or cursor_anchor)
    proximity = candidate_proximity(candidate_box, focus_delta, cursor_delta)
    box_compactness = 0.0 if candidate_box is None else max(0.0, 0.16 - box_area(candidate_box))
    return (
        intent_overlap_score(label, tokens),
        label_quality_score(label),
        source_weight + proximity,
        box_compactness,
    )


def candidate_proximity(
    candidate_box: FocusBox | None,
    focus_delta: float,
    cursor_delta: float,
) -> float:
    if candidate_box is None:
        return 0.0
    focus_proximity = max(0.0, 1.0 - focus_delta * 3.0)
    cursor_proximity = max(0.0, 1.0 - cursor_delta * 4.0)
    return round(focus_proximity * 0.55 + cursor_proximity * 0.45, 3)


def trusted_candidate_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not normalized or low_signal_label(label):
        return False
    tokens = normalized.split()
    if len(tokens) == 1 and tokens[0] in {"free", "learn", "start", "open"}:
        return False
    return True


def trusted_ocr_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not trusted_candidate_label(label):
        return False
    tokens = normalized.split()
    if len(tokens) <= 2 and not any(len(token) >= 5 for token in tokens):
        return False
    return True


def ui_source_weight(
    label: str,
    box: FocusBox | None,
    frame: FrameSignalRecord,
) -> float:
    weight = 1.0
    if box is not None:
        if box_area(box) <= 0.14:
            weight += 0.08
        if box_center_delta(frame.cursor_box or frame.click_target_box, box) <= 0.14:
            weight += 0.12
    if " " in normalize_label(label):
        weight += 0.04
    return round(weight, 3)
