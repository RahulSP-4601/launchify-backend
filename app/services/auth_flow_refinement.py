from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import LaunchScriptScene, SessionEventRecord, SessionTargetRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import classify_action
from app.services.inferred_recording_support import dedupe_events, low_signal_label, normalize_label
from app.services.scene_intent_resolver import IntentKind, SceneIntentResolution, resolve_scene_intent

AUTH_INTENTS = frozenset({"auth", "account_existing", "account_create"})
AUTH_TOKENS = frozenset({"account", "create", "existing", "google", "log", "login", "sign", "signup"})


@dataclass(frozen=True)
class AuthCandidate:
    label: str
    source_weight: float


def refine_auth_flow_events(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    resolutions = scene_resolutions(scenes)
    refined = [
        refine_auth_event(event, analyses_by_scene.get(scene_number(event)), resolutions.get(scene_number(event)), next_auth_resolution(scene_number(event), resolutions))
        for event in events
    ]
    return collapse_duplicate_auth_events(dedupe_events(sorted(refined, key=lambda item: item.timestamp)))


def scene_resolutions(scenes: list[LaunchScriptScene]) -> dict[int, SceneIntentResolution]:
    return {
        scene.scene_number: resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
        for scene in scenes
    }


def next_auth_resolution(
    current_scene_number: int,
    resolutions: dict[int, SceneIntentResolution],
) -> SceneIntentResolution | None:
    for scene_number in sorted(number for number in resolutions if number > current_scene_number):
        resolution = resolutions[scene_number]
        if resolution.intent in AUTH_INTENTS:
            return resolution
        if resolution.intent in {"course", "result"}:
            break
    return None


def refine_auth_event(
    event: SessionEventRecord,
    analysis: VisualSceneAnalysisRecord | None,
    resolution: SceneIntentResolution | None,
    next_resolution: SceneIntentResolution | None,
) -> SessionEventRecord:
    if analysis is None or resolution is None:
        return event
    if resolution.intent not in AUTH_INTENTS and (next_resolution is None or next_resolution.intent not in AUTH_INTENTS):
        return event
    candidate = preferred_auth_candidate(analysis, resolution, next_resolution, event)
    if candidate is None:
        return event
    normalized_current = normalize_label(event.target.label)
    normalized_candidate = normalize_label(candidate.label)
    if normalized_current == normalized_candidate:
        return event
    if not should_replace_auth_label(event.target.label, candidate.label, resolution, next_resolution):
        return event
    return relabeled_event(event, candidate.label)


def preferred_auth_candidate(
    analysis: VisualSceneAnalysisRecord,
    resolution: SceneIntentResolution,
    next_resolution: SceneIntentResolution | None,
    event: SessionEventRecord,
) -> AuthCandidate | None:
    candidates = auth_candidates(analysis)
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda candidate: auth_candidate_rank(candidate, resolution, next_resolution, event),
        reverse=True,
    )
    return ranked[0] if ranked else None


def auth_candidates(analysis: VisualSceneAnalysisRecord) -> list[AuthCandidate]:
    candidates: dict[str, AuthCandidate] = {}
    for label in analysis.visible_labels:
        add_auth_candidate(candidates, label, 0.62)
    for frame in analysis.frames:
        for element in frame.ui_elements:
            add_auth_candidate(candidates, element.label, 1.0)
    return list(candidates.values())


def add_auth_candidate(
    candidates: dict[str, AuthCandidate],
    label: str,
    source_weight: float,
) -> None:
    normalized = normalize_label(label)
    if not normalized or low_signal_label(label):
        return
    if not set(normalized.split()) & AUTH_TOKENS:
        return
    current = candidates.get(normalized)
    if current is None or source_weight > current.source_weight:
        candidates[normalized] = AuthCandidate(label=label, source_weight=source_weight)


def auth_candidate_rank(
    candidate: AuthCandidate,
    resolution: SceneIntentResolution,
    next_resolution: SceneIntentResolution | None,
    event: SessionEventRecord,
) -> tuple[float, float, float, float]:
    tokens = set(normalize_label(candidate.label).split())
    branch_score = branch_alignment_score(tokens, resolution, next_resolution)
    generic_penalty = 0.0 if tokens != {"account"} else -1.0
    current_overlap = len(tokens & set(normalize_label(event.target.label).split()))
    return (
        branch_score,
        candidate.source_weight,
        current_overlap,
        generic_penalty,
    )


def branch_alignment_score(
    tokens: set[str],
    resolution: SceneIntentResolution,
    next_resolution: SceneIntentResolution | None,
) -> float:
    target_intent = dominant_auth_intent(resolution, next_resolution)
    if target_intent == "account_existing":
        return existing_branch_score(tokens)
    if target_intent == "account_create":
        return create_branch_score(tokens)
    return auth_generic_score(tokens)


def dominant_auth_intent(
    resolution: SceneIntentResolution,
    next_resolution: SceneIntentResolution | None,
) -> IntentKind:
    if next_resolution is not None and next_resolution.intent in {"account_existing", "account_create"}:
        return next_resolution.intent
    return resolution.intent


def existing_branch_score(tokens: set[str]) -> float:
    score = 0.0
    if {"log", "login"} & tokens or "google" in tokens:
        score += 1.0
    if "existing" in tokens or "choose" in tokens:
        score += 0.9
    if {"sign", "signup", "create"} & tokens:
        score -= 1.2
    return score


def create_branch_score(tokens: set[str]) -> float:
    score = 0.0
    if {"sign", "signup", "create"} & tokens:
        score += 1.0
    if "account" in tokens:
        score += 0.3
    if {"existing", "choose"} & tokens or {"log", "login"} & tokens:
        score -= 0.9
    return score


def auth_generic_score(tokens: set[str]) -> float:
    score = 0.0
    if {"google", "log", "login", "sign", "account"} & tokens:
        score += 0.5
    if {"choose", "existing"} & tokens:
        score += 0.3
    return score


def should_replace_auth_label(
    current_label: str,
    candidate_label: str,
    resolution: SceneIntentResolution,
    next_resolution: SceneIntentResolution | None,
) -> bool:
    current_tokens = set(normalize_label(current_label).split())
    candidate_tokens = set(normalize_label(candidate_label).split())
    if current_tokens == {"account"} and candidate_tokens:
        return True
    if dominant_auth_intent(resolution, next_resolution) == "account_existing":
        if {"sign", "signup", "create"} & current_tokens and not {"sign", "signup", "create"} & candidate_tokens:
            return True
    if low_signal_label(current_label) and not low_signal_label(candidate_label):
        return True
    return False


def relabeled_event(
    event: SessionEventRecord,
    label: str,
) -> SessionEventRecord:
    target = event.target.model_copy(update={"label": label, "text": label})
    metadata = dict(event.metadata)
    metadata["action_class"] = classify_action(event.type, label, metadata.get("transcript_excerpt", ""), label)
    return event.model_copy(update={"target": target, "metadata": metadata})


def collapse_duplicate_auth_events(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    collapsed: list[SessionEventRecord] = []
    for event in events:
        if should_drop_auth_duplicate(event, collapsed):
            continue
        collapsed.append(event)
    return collapsed


def should_drop_auth_duplicate(
    event: SessionEventRecord,
    selected: list[SessionEventRecord],
) -> bool:
    if event.metadata.get("action_class") != "auth_action":
        return False
    return any(
        existing.metadata.get("scene_number") == event.metadata.get("scene_number")
        and abs(existing.timestamp - event.timestamp) <= 2.6
        and normalize_label(existing.target.label) == normalize_label(event.target.label)
        for existing in selected
    )


def scene_number(event: SessionEventRecord) -> int:
    return int(event.metadata.get("scene_number", "0") or 0)
