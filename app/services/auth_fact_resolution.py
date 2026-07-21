from __future__ import annotations

from app.services.canonical_consistency import branch_family
from app.services.inferred_recording_support import normalize_label
from app.services.result_linking import ResultLink
from app.services.stable_state_reconstruction import EpisodeStateBundle


def resolved_raw_target(
    bundle: EpisodeStateBundle,
    link: ResultLink,
    raw_target: str,
) -> str:
    if not auth_resolution_needed(raw_target, link):
        return raw_target
    branch = dominant_auth_branch(bundle, link, raw_target)
    if branch == "existing":
        return existing_auth_target(raw_target, link)
    if branch == "create":
        return create_auth_target(raw_target, link)
    return raw_target


def auth_resolution_needed(raw_target: str, link: ResultLink) -> bool:
    return any(
        branch_family(candidate) != "generic"
        for candidate in (raw_target, link.result_label, link.screen_before, link.screen_after)
    ) or link.screen_after in {"account_picker", "auth_provider"}


def dominant_auth_branch(
    bundle: EpisodeStateBundle,
    link: ResultLink,
    raw_target: str,
) -> str:
    scores = {"existing": 0.0, "create": 0.0}
    add_branch_score(scores, branch_family(raw_target), 0.9)
    add_branch_score(scores, branch_family(link.result_label), 1.0)
    add_branch_score(scores, branch_family(" ".join(evidence_labels(bundle))), 1.1)
    add_branch_score(scores, transcript_branch(bundle), 1.15)
    if link.screen_after == "account_picker":
        scores["existing"] += 1.2
    if link.screen_before == "auth_provider" and link.screen_after == "account_picker":
        scores["existing"] += 0.7
    if link.screen_after == "course_catalog" and prior_auth_surface(bundle):
        scores["existing"] += 0.45
    if scores["existing"] == scores["create"] == 0.0:
        return "generic"
    return "existing" if scores["existing"] >= scores["create"] + 0.25 else "create"


def add_branch_score(scores: dict[str, float], branch: str, weight: float) -> None:
    if branch in scores:
        scores[branch] += weight


def evidence_labels(bundle: EpisodeStateBundle) -> list[str]:
    labels = [signal.label for signal in bundle.episode.evidence if signal.label.strip()]
    for state in (bundle.before_state, bundle.action_state, bundle.immediate_state, bundle.result_state):
        if state is None:
            continue
        if state.target_label.strip():
            labels.append(state.target_label)
        if state.friendly_label.strip():
            labels.append(state.friendly_label)
    return labels


def transcript_branch(bundle: EpisodeStateBundle) -> str:
    transcript_text = " ".join(
        signal.details.get("excerpt", "")
        for signal in bundle.episode.evidence
        if signal.source == "transcript"
    )
    return branch_family(transcript_text)


def prior_auth_surface(bundle: EpisodeStateBundle) -> bool:
    labels = " ".join(evidence_labels(bundle))
    normalized = normalize_label(labels)
    return any(token in normalized for token in ("google", "login", "log in", "sign in", "account"))


def existing_auth_target(raw_target: str, link: ResultLink) -> str:
    normalized = normalize_label(raw_target)
    if "google login" in normalized:
        return "Google Login"
    if "google" in normalized:
        return "Continue With Google"
    if normalize_label(link.result_label) == "choose an account":
        return "Continue With Google"
    return raw_target or "Continue With Google"


def create_auth_target(raw_target: str, link: ResultLink) -> str:
    if "google" in normalize_label(raw_target):
        return "Sign up with Google"
    if branch_family(link.result_label) == "create":
        return link.result_label
    return raw_target
