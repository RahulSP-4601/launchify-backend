from __future__ import annotations

from dataclasses import dataclass

from app.services.canonical_consistency import branch_family, result_state_conflict
from app.services.inferred_recording_support import normalize_label
from app.services.stable_state_reconstruction import EpisodeStateBundle, StateFingerprint


@dataclass(frozen=True)
class ResultLink:
    screen_before: str
    screen_after: str
    result_label: str
    confidence: float


def infer_result_link(
    bundle: EpisodeStateBundle,
    next_bundle: EpisodeStateBundle | None = None,
) -> ResultLink:
    before = bundle.before_state
    after_state = best_after_state(bundle, next_bundle)
    return ResultLink(
        screen_before=state_name(before),
        screen_after=state_name(after_state),
        result_label=state_label(after_state),
        confidence=result_confidence(bundle, after_state, next_bundle),
    )


def best_after_state(
    bundle: EpisodeStateBundle,
    next_bundle: EpisodeStateBundle | None,
) -> StateFingerprint | None:
    current = bundle.result_state
    if strong_action_followed_by_progress(bundle, next_bundle):
        if next_bundle is not None:
            candidate = next_bundle.before_state or next_bundle.action_state
            if candidate is not None:
                return branch_compatible_after_state(bundle, candidate, current)
    if current is not None and current.stability_score >= 0.64:
        return current
    bridged = bridged_after_state(bundle, next_bundle)
    if bridged is not None:
        return bridged
    return bundle.immediate_state or current or bundle.action_state


def bridged_after_state(
    bundle: EpisodeStateBundle,
    next_bundle: EpisodeStateBundle | None,
) -> StateFingerprint | None:
    if next_bundle is None:
        return None
    current = bundle.result_state
    if current is None:
        return next_bundle.before_state or next_bundle.action_state
    if state_name(current) != "generic":
        return current
    candidate = next_bundle.before_state or next_bundle.action_state
    if candidate is None:
        return current
    if candidate.stability_score >= current.stability_score + 0.08:
        return branch_compatible_after_state(bundle, candidate, current)
    return current


def branch_compatible_after_state(
    bundle: EpisodeStateBundle,
    preferred: StateFingerprint | None,
    fallback: StateFingerprint | None,
) -> StateFingerprint | None:
    action_target = "" if bundle.action_state is None else bundle.action_state.target_label
    locked = retained_intermediate_auth_state(action_target, preferred, fallback)
    if locked is not None:
        return locked
    if preferred is not None and not result_state_conflict(action_target, preferred.friendly_label, state_name(preferred)):
        return preferred
    if fallback is not None and not result_state_conflict(action_target, fallback.friendly_label, state_name(fallback)):
        return fallback
    return preferred or fallback


def retained_intermediate_auth_state(
    action_target: str,
    preferred: StateFingerprint | None,
    fallback: StateFingerprint | None,
) -> StateFingerprint | None:
    target_key = normalize_label(action_target)
    if branch_family(target_key) != "existing":
        return None
    if fallback is None:
        return None
    fallback_state = state_name(fallback)
    preferred_state = state_name(preferred)
    if fallback_state != "account_picker":
        return None
    if preferred_state in {"course_catalog", "difficulty_picker"}:
        return fallback
    return None


def strong_action_followed_by_progress(
    bundle: EpisodeStateBundle,
    next_bundle: EpisodeStateBundle | None,
) -> bool:
    if next_bundle is None or bundle.action_state is None or next_bundle.before_state is None:
        return False
    current_after = state_name(bundle.result_state)
    next_before = state_name(next_bundle.before_state)
    if next_before in {"unknown", "generic"} or current_after == next_before:
        return False
    return bundle.action_state.stability_score >= 0.72


def result_confidence(
    bundle: EpisodeStateBundle,
    after_state: StateFingerprint | None,
    next_bundle: EpisodeStateBundle | None,
) -> float:
    if after_state is None:
        return 0.34
    score = after_state.stability_score
    if bundle.result_state is not None and after_state.timestamp == bundle.result_state.timestamp:
        score += 0.12
    if next_bundle is not None and next_bundle.before_state is not None:
        if state_name(next_bundle.before_state) == state_name(after_state):
            score += 0.12
    if state_name(after_state) not in {"generic", "unknown"}:
        score += 0.08
    return round(min(score, 1.0), 3)


def state_name(state: StateFingerprint | None) -> str:
    if state is None:
        return "unknown"
    if state.structure == "dashboard":
        return "course_catalog"
    if state.structure == "picker":
        return "account_picker" if "account" in state.friendly_label.lower() else "difficulty_picker"
    if state.structure == "result":
        return "result_state"
    return state.structure


def state_label(state: StateFingerprint | None) -> str:
    return "" if state is None else state.friendly_label
