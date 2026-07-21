from __future__ import annotations
from dataclasses import dataclass
from typing import cast
from app.models.projects import (
    FocusBox,
    LaunchScriptRecord,
    SessionEventRecord,
    SessionEventType,
    SessionTargetRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.action_classifier import classify_action
from app.services.auth_fact_resolution import resolved_raw_target
from app.services.canonical_consistency import auth_mapping_conflict, result_state_conflict
from app.services.canonical_fact_scoring import auth_result_label_conflict, fact_penalty, label_priority
from app.services.inferred_recording_support import normalize_label
from app.services.interaction_episode_builder import build_interaction_episodes
from app.services.evidence_timeline import build_evidence_timeline, evidence_payload; from app.services.extraction_artifacts import artifact_payload
from app.services.result_linking import ResultLink, infer_result_link; from app.services.selection_fact_resolution import resolved_selection_target_label, resolved_selection_targets_with_followup
from app.services.sequence_decoder import select_best_sequence
from app.services.stable_state_reconstruction import EpisodeStateBundle, StateFingerprint, reconstruct_episode_states
@dataclass(frozen=True)
class CanonicalFact:
    scene_number: int
    timestamp: float
    event_type: SessionEventType
    canonical_label: str
    raw_target_label: str
    screen_before: str
    screen_after: str
    action_class: str
    focus_box: FocusBox | None
    confidence: float
    confidence_breakdown: dict[str, float]
def build_canonical_facts(
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[CanonicalFact]:
    del launch_script
    timeline = build_evidence_timeline(transcript, analyses_by_scene)
    episodes = build_interaction_episodes(timeline)
    bundles = reconstruct_episode_states(episodes, analyses_by_scene)
    return decoded_facts(bundles)
def canonical_artifacts(
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> dict[str, object]:
    del launch_script
    timeline = build_evidence_timeline(transcript, analyses_by_scene)
    bundles = reconstruct_episode_states(build_interaction_episodes(timeline), analyses_by_scene)
    facts = decoded_facts(bundles)
    return artifact_payload(evidence_payload(timeline), canonical_fact_payloads(facts))
def canonical_events_from_facts(
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
) -> list[SessionEventRecord]:
    events = [
        event_from_fact(fact, viewport_width, viewport_height)
        for fact in build_canonical_facts(launch_script, transcript, analyses_by_scene)
    ]
    return [event for event in events if event is not None]
def fact_from_bundle(bundle: EpisodeStateBundle) -> CanonicalFact | None:
    return fact_from_bundle_with_link(bundle, infer_result_link(bundle))


def fact_from_bundle_with_link(
    bundle: EpisodeStateBundle,
    link: ResultLink,
) -> CanonicalFact | None:
    action_state = bundle.action_state
    result_state = bundle.result_state
    if action_state is None and result_state is None:
        return None
    raw_target = preferred_raw_target(action_state, result_state)
    resolved_target = resolved_raw_target(bundle, link, raw_target)
    canonical = canonical_label(bundle, resolved_target, link.screen_after, link.result_label)
    action_class = classify_action(fact_event_type(bundle, raw_target), canonical, resolved_target, canonical)
    resolved_target = resolved_selection_target_label(
        raw_target_label=resolved_target,
        canonical_label=canonical,
        action_class=action_class,
        screen_after=link.screen_after,
        result_label=link.result_label,
    )
    if not keepable_fact(bundle, link, canonical, raw_target, resolved_target):
        return None
    screen_before = normalized_transition_state(link.screen_before, canonical, is_before=True)
    screen_after = normalized_transition_state(link.screen_after, canonical, is_before=False)
    event_type = fact_event_type(bundle, raw_target)
    confidence = fact_confidence(bundle, canonical, resolved_target, link)
    return CanonicalFact(
        scene_number=bundle.episode.scene_number,
        timestamp=round(bundle.episode.anchor_timestamp, 2),
        event_type=event_type,
        canonical_label=canonical,
        raw_target_label=resolved_target,
        screen_before=screen_before,
        screen_after=screen_after,
        action_class=action_class,
        focus_box=preferred_focus_box(bundle),
        confidence=confidence["overall"],
        confidence_breakdown=confidence,
    )


def preferred_raw_target(
    action_state: StateFingerprint | None,
    result_state: StateFingerprint | None,
) -> str:
    if action_state is not None and action_state.target_label:
        return action_state.target_label
    if result_state is not None and result_state.target_label:
        return result_state.target_label
    if result_state is not None:
        return result_state.friendly_label
    return ""

def canonical_label(
    bundle: EpisodeStateBundle,
    raw_target: str,
    screen_after: str,
    result_label: str,
) -> str:
    del bundle
    raw_key = normalize_label(raw_target)
    if "google login" in raw_key:
        return "Google Login"
    if (
        "continue with google" in raw_key
        or "log in with google" in raw_key
        or "login with google" in raw_key
        or "sign up with google" in raw_key
    ):
        return "Continue With Google"
    if screen_after == "course_catalog":
        return "Select A Course"
    if result_label.startswith("Pick your"):
        return result_label
    if raw_key in {"japanese", "open course"} and screen_after in {"difficulty_picker", "result_state"}:
        return "Select A Course"
    if result_label and auth_result_label_conflict(raw_key, result_label):
        return raw_target or result_label
    if result_label:
        return result_label
    return raw_target or "Product Interaction"

def fact_event_type(bundle: EpisodeStateBundle, raw_target: str) -> SessionEventType:
    raw_key = normalize_label(raw_target)
    if should_emit_focus(bundle, raw_key):
        return "focus"
    if bundle.action_state is not None and raw_key:
        return "click"
    if bundle.result_state is not None:
        return "focus"
    return "custom"

def fact_confidence(
    bundle: EpisodeStateBundle,
    canonical: str,
    raw_target: str,
    link: ResultLink,
) -> dict[str, float]:
    action_score = bundle.action_state.stability_score if bundle.action_state is not None else 0.48
    result_score = link.confidence
    target_score = 0.84 if raw_target else 0.38
    canonical_score = 0.92 if canonical in {"Google Login", "Continue With Google", "Select A Course"} or canonical.startswith("Pick your") else 0.72
    overall = min((action_score * 0.28) + (result_score * 0.28) + (target_score * 0.2) + (canonical_score * 0.24), 1.0)
    return {
        "event_exists": round(max(action_score, result_score), 3),
        "action_type": round(action_score, 3),
        "target_element": round(target_score, 3),
        "screen_before": round(state_score(bundle.before_state), 3),
        "screen_after": round(state_score(bundle.result_state), 3),
        "canonical_step": round(canonical_score, 3),
        "overall": round(overall, 3),
    }

def decoded_facts(bundles: list[EpisodeStateBundle]) -> list[CanonicalFact]:
    candidates_by_step = build_fact_candidates(bundles)
    selected = select_best_sequence(
        candidates_by_step,
        candidate_score=lambda item: item.confidence,
        candidate_branch=fact_branch,
        candidate_after=lambda item: item.screen_after,
        candidate_label=lambda item: item.canonical_label,
        candidate_penalty=lambda item: fact_penalty(item.raw_target_label, item.canonical_label, item.screen_after),
    )
    pruned = prune_selected_facts(selected)
    return collapse_exported_facts(cast(list[CanonicalFact], resolved_selection_targets_with_followup(pruned)))

def build_fact_candidates(bundles: list[EpisodeStateBundle]) -> list[list[CanonicalFact]]:
    candidates: list[list[CanonicalFact]] = []
    for index, bundle in enumerate(bundles):
        next_bundle = bundles[index + 1] if index + 1 < len(bundles) else None
        links = candidate_links(bundle, next_bundle)
        facts = [fact for fact in (fact_from_bundle_with_link(bundle, link) for link in links) if fact is not None]
        if facts:
            candidates.append(rank_distinct_facts(facts))
    return candidates

def candidate_links(bundle: EpisodeStateBundle, next_bundle: EpisodeStateBundle | None) -> list[ResultLink]:
    primary = infer_result_link(bundle, next_bundle)
    if next_bundle is None or next_bundle.before_state is None:
        return [primary]
    bridged = infer_result_link(bundle, None)
    alternatives = [primary, bridged]
    return distinct_links(alternatives)


def distinct_links(links: list[ResultLink]) -> list[ResultLink]:
    distinct: list[ResultLink] = []
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        key = (link.screen_before, link.screen_after, link.result_label)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(link)
    return distinct


def rank_distinct_facts(facts: list[CanonicalFact]) -> list[CanonicalFact]:
    ranked = sorted(facts, key=lambda item: (item.confidence, label_priority(item.canonical_label)), reverse=True)
    distinct: list[CanonicalFact] = []
    seen: set[str] = set()
    for fact in ranked:
        key = normalize_label(fact.canonical_label)
        if key in seen:
            continue
        seen.add(key)
        distinct.append(fact)
    return distinct[:3]


def keepable_fact(
    bundle: EpisodeStateBundle,
    link: ResultLink,
    canonical: str,
    raw_target: str,
    resolved_target: str,
) -> bool:
    if not canonical.strip():
        return False
    if weak_same_state_fact(bundle, link, resolved_target):
        return False
    if stale_auth_fact(link, canonical):
        return False
    if auth_mapping_conflict(resolved_target, canonical, link.result_label):
        return False
    if result_state_conflict(resolved_target, link.result_label, link.screen_after):
        return False
    return True


def weak_same_state_fact(
    bundle: EpisodeStateBundle,
    link: ResultLink,
    raw_target: str,
) -> bool:
    if link.screen_before != link.screen_after:
        return False
    if link.screen_after not in {"account_picker", "course_catalog", "result_state", "difficulty_picker"}:
        return False
    action_strength = 0.0 if bundle.action_state is None else bundle.action_state.stability_score
    compact_target = target_compactness(raw_target) >= 0.75
    return action_strength < 0.78 or not compact_target


def stale_auth_fact(link: ResultLink, canonical: str) -> bool:
    return link.screen_after == "account_picker" and normalize_label(canonical) == "choose an account"


def should_emit_focus(bundle: EpisodeStateBundle, raw_key: str) -> bool:
    if bundle.result_state is None:
        return False
    result_state = state_name(bundle.result_state)
    if result_state in {"result_state", "difficulty_picker"} and bundle.action_state is not None:
        return bundle.action_state.stability_score < 0.82 or not compact_result_target(raw_key)
    return False


def compact_result_target(raw_key: str) -> bool:
    tokens = raw_key.split()
    return len(tokens) <= 3 and not any(token in raw_key for token in ("pick", "level", "learning"))


def fact_branch(fact: CanonicalFact) -> str:
    label = normalize_label(fact.canonical_label or fact.raw_target_label)
    if any(token in label for token in ("sign up", "signup", "create account")):
        return "create"
    if any(token in label for token in ("log in", "login", "google", "account")):
        return "existing"
    return "generic"


def prune_selected_facts(facts: list[CanonicalFact]) -> list[CanonicalFact]:
    selected: list[CanonicalFact] = []
    for fact in sorted(facts, key=lambda item: item.timestamp):
        if regressive_auth_fact(selected, fact):
            continue
        if duplicate_selected_fact(selected[-1], fact) if selected else False:
            if fact.confidence > selected[-1].confidence:
                selected[-1] = fact
            continue
        selected.append(fact)
    return selected


def duplicate_selected_fact(left: CanonicalFact, right: CanonicalFact) -> bool:
    if normalize_label(left.canonical_label) != normalize_label(right.canonical_label):
        return False
    if left.screen_after == right.screen_after and abs(left.timestamp - right.timestamp) <= 4.2:
        return True
    return left.scene_number == right.scene_number and abs(left.timestamp - right.timestamp) <= 2.2


def target_compactness(label: str) -> float:
    normalized = normalize_label(label)
    tokens = normalized.split()
    if not tokens:
        return 0.0
    return 1.0 if len(tokens) <= 3 else 0.7 if len(tokens) <= 5 else 0.35


def regressive_auth_fact(
    selected: list[CanonicalFact],
    candidate: CanonicalFact,
) -> bool:
    if not selected:
        return False
    candidate_label = normalize_label(candidate.canonical_label)
    if candidate_label not in {"continue with google", "choose an account", "google login"}:
        return False
    return any(normalize_label(item.canonical_label) == "select a course" for item in selected)


def normalized_transition_state(state: str, canonical: str, *, is_before: bool) -> str:
    if state not in {"generic", "unknown", "result_state"}:
        return state
    label = normalize_label(canonical)
    if label == "google login":
        return "landing" if is_before else "auth_provider"
    if label == "continue with google":
        return "auth_provider" if is_before else "account_picker"
    if label == "select a course":
        return "course_catalog"
    if label.startswith("pick your"):
        return "difficulty_picker"
    return state


def collapse_exported_facts(facts: list[CanonicalFact]) -> list[CanonicalFact]:
    if not facts:
        return []
    collapsed: list[CanonicalFact] = []
    for index, fact in enumerate(sorted(facts, key=lambda item: item.timestamp)):
        next_fact = facts[index + 1] if index + 1 < len(facts) else None
        if suppress_intermediate_fact(fact, next_fact):
            continue
        if collapse_with_previous(collapsed, fact):
            continue
        collapsed.append(fact)
    return collapsed


def suppress_intermediate_fact(
    fact: CanonicalFact,
    next_fact: CanonicalFact | None,
) -> bool:
    if next_fact is None:
        return False
    label = normalize_label(fact.canonical_label)
    next_label = normalize_label(next_fact.canonical_label)
    if label in {"japanese", "open course"} and next_label.startswith("pick your"):
        return abs(next_fact.timestamp - fact.timestamp) <= 5.0
    return False


def collapse_with_previous(
    collapsed: list[CanonicalFact],
    fact: CanonicalFact,
) -> bool:
    if not collapsed:
        return False
    previous = collapsed[-1]
    same_after = previous.screen_after == fact.screen_after
    same_label = normalize_label(previous.canonical_label) == normalize_label(fact.canonical_label)
    if same_label and abs(fact.timestamp - previous.timestamp) <= 6.0:
        if fact.confidence > previous.confidence:
            collapsed[-1] = fact
        return True
    if same_after and fact.screen_after in {"difficulty_picker", "result_state"} and abs(fact.timestamp - previous.timestamp) <= 6.0:
        if fact.confidence > previous.confidence:
            collapsed[-1] = fact
        return True
    return False


def state_name(state: StateFingerprint | None) -> str:
    if state is None:
        return "unknown"
    mapping = {
        "dashboard": "course_catalog",
        "picker": "account_picker" if "account" in normalize_label(state.friendly_label) else "difficulty_picker",
        "result": "result_state",
        "generic": "generic",
    }
    return mapping.get(state.structure, state.structure)


def state_score(state: StateFingerprint | None) -> float:
    return state.stability_score if state is not None else 0.36


def preferred_focus_box(bundle: EpisodeStateBundle) -> FocusBox | None:
    for state in (bundle.action_state, bundle.result_state, bundle.before_state, bundle.immediate_state):
        if state is not None and state.focus_box is not None:
            return state.focus_box
    return None


def event_from_fact(
    fact: CanonicalFact,
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord | None:
    x, y, width, height = denormalize_box(fact.focus_box, viewport_width, viewport_height)
    metadata = {
        "inferred": "true",
        "scene_number": str(fact.scene_number),
        "synthetic_selector": f"[data-launchify-scene='{fact.scene_number}']",
        "score": f"{fact.confidence:.2f}",
        "action_class": fact.action_class,
        "screen_before": fact.screen_before,
        "screen_after": fact.screen_after,
        "raw_target_label": fact.raw_target_label,
        "canonical_label": fact.canonical_label,
        "confidence_event_exists": f"{fact.confidence_breakdown['event_exists']:.2f}",
        "confidence_target": f"{fact.confidence_breakdown['target_element']:.2f}",
        "confidence_after": f"{fact.confidence_breakdown['screen_after']:.2f}",
    }
    if fact.event_type == "focus":
        metadata["scene_state"] = "result_state"
        metadata["result_label"] = fact.canonical_label
    return SessionEventRecord(
        type=fact.event_type,
        timestamp=fact.timestamp,
        x=x,
        y=y,
        target=SessionTargetRecord(
            selector="",
            label=fact.canonical_label,
            text=fact.raw_target_label or fact.canonical_label,
            role="control",
            bbox_x=x - width / 2 if x is not None and width is not None else None,
            bbox_y=y - height / 2 if y is not None and height is not None else None,
            bbox_width=width,
            bbox_height=height,
        ),
        metadata=metadata,
    )


def canonical_fact_payloads(facts: list[CanonicalFact]) -> list[dict[str, object]]:
    return [
        {
            "scene_number": fact.scene_number,
            "timestamp": fact.timestamp,
            "event_type": fact.event_type,
            "canonical_label": fact.canonical_label,
            "raw_target_label": fact.raw_target_label,
            "screen_before": fact.screen_before,
            "screen_after": fact.screen_after,
            "action_class": fact.action_class,
            "focus_box": None if fact.focus_box is None else fact.focus_box.model_dump(mode="json"),
            "confidence": fact.confidence,
            "confidence_breakdown": fact.confidence_breakdown,
        }
        for fact in facts
    ]


def denormalize_box(
    focus_box: FocusBox | None,
    viewport_width: int,
    viewport_height: int,
) -> tuple[float | None, float | None, float | None, float | None]:
    if focus_box is None:
        return None, None, None, None
    width = round(focus_box.width * viewport_width, 2)
    height = round(focus_box.height * viewport_height, 2)
    x = round((focus_box.x + focus_box.width / 2) * viewport_width, 2)
    y = round((focus_box.y + focus_box.height / 2) * viewport_height, 2)
    return x, y, width, height
