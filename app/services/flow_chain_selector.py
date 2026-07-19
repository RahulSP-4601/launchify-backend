from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from app.models.projects import LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_type_classifier import SceneType, classify_scene_type
from app.services.semantic_event_normalizer import SemanticEvent, semantic_event
from app.services.scene_intent_resolver import IntentKind, resolve_scene_intent

TARGET_SCENE_TYPES = frozenset({"auth_provider", "account_picker", "course_catalog"})


@dataclass(frozen=True)
class FlowCluster:
    scene_numbers: list[int]
    scene_type: SceneType


def scene_number(event: SessionEventRecord) -> int:
    return int(event.metadata.get("scene_number", "0") or 0)


def select_flow_chains(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    if not events:
        return []
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    clusters = flow_clusters(scenes, analyses_by_scene)
    selected_keys: set[tuple[int, float, str]] = set()
    for cluster in clusters:
        cluster_events = [event for event in events if scene_number(event) in cluster.scene_numbers]
        if not cluster_events:
            continue
        for event in best_cluster_chain(cluster, cluster_events, scenes_by_number, analyses_by_scene):
            selected_keys.add(event_key(event))
    passthrough = [event for event in events if scene_number(event) not in clustered_scene_numbers(clusters)]
    selected = [event for event in events if event_key(event) in selected_keys]
    combined = {event_key(event): event for event in [*selected, *passthrough]}
    return sorted(combined.values(), key=lambda item: item.timestamp)


def flow_clusters(
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[FlowCluster]:
    clusters: list[FlowCluster] = []
    pending: list[int] = []
    pending_type: SceneType | None = None
    for scene in scenes:
        scene_type = classify_scene_type(scene, analyses_by_scene.get(scene.scene_number))
        if scene_type not in TARGET_SCENE_TYPES:
            if pending and pending_type is not None:
                clusters.append(FlowCluster(scene_numbers=pending, scene_type=pending_type))
            pending = []
            pending_type = None
            continue
        normalized_type = "auth_provider" if scene_type in {"auth_provider", "account_picker"} else scene_type
        if pending and pending_type == normalized_type and scene.scene_number - pending[-1] <= 1:
            pending.append(scene.scene_number)
            continue
        if pending and pending_type is not None:
            clusters.append(FlowCluster(scene_numbers=pending, scene_type=pending_type))
        pending = [scene.scene_number]
        pending_type = normalized_type
    if pending and pending_type is not None:
        clusters.append(FlowCluster(scene_numbers=pending, scene_type=pending_type))
    return clusters


def clustered_scene_numbers(clusters: list[FlowCluster]) -> set[int]:
    return {scene_number for cluster in clusters for scene_number in cluster.scene_numbers}


def best_cluster_chain(
    cluster: FlowCluster,
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    choices = cluster_choice_variants(cluster, events)
    chains = candidate_chains(choices)
    expected = cluster_expectation(cluster, scenes_by_number, analyses_by_scene)
    ranked = sorted(
        chains,
        key=lambda chain: chain_score(chain, cluster.scene_type, expected, scenes_by_number, analyses_by_scene),
        reverse=True,
    )
    return ranked[0] if ranked else []


def cluster_choice_variants(
    cluster: FlowCluster,
    events: list[SessionEventRecord],
) -> list[list[list[SessionEventRecord]]]:
    by_scene: dict[int, list[SessionEventRecord]] = {scene_id: [] for scene_id in cluster.scene_numbers}
    for event in events:
        by_scene.setdefault(scene_number(event), []).append(event)
    choices: list[list[list[SessionEventRecord]]] = []
    for scene_id in cluster.scene_numbers:
        scene_events = scene_choice_candidates(cluster.scene_type, by_scene.get(scene_id, []))
        choices.append(scene_choice_variants(cluster.scene_type, scene_events))
    return choices


def scene_choice_variants(
    scene_type: SceneType,
    events: list[SessionEventRecord],
) -> list[list[SessionEventRecord]]:
    variants: list[list[SessionEventRecord]] = [[]]
    variants.extend([[event] for event in events])
    bundled = bundled_scene_events(scene_type, events)
    if bundled:
        variants.append(bundled)
    return variants


def bundled_scene_events(
    scene_type: SceneType,
    events: list[SessionEventRecord],
) -> list[SessionEventRecord]:
    semantics = {event_key(event): semantic_for_choice(event) for event in events}
    ranked = sorted(events, key=lambda item: item.timestamp)
    if scene_type == "auth_provider":
        login = next((event for event in ranked if semantics[event_key(event)].semantic_action == "auth_login_google"), None)
        picker = next((event for event in ranked if semantics[event_key(event)].semantic_action == "auth_choose_account"), None)
        if login is not None and picker is not None and picker.timestamp > login.timestamp:
            return [login, picker]
    if scene_type == "course_catalog":
        state = next((event for event in ranked if semantics[event_key(event)].scene_type == "course_catalog" and event.type == "focus"), None)
        action = next((event for event in ranked if semantics[event_key(event)].entity and event.type == "click"), None)
        if state is not None and action is not None and action.timestamp > state.timestamp:
            return [state, action]
    return []


def scene_choice_candidates(
    scene_type: SceneType,
    events: list[SessionEventRecord],
) -> list[SessionEventRecord]:
    ranked = sorted(events, key=event_local_score, reverse=True)
    limit = 5 if scene_type == "auth_provider" else 4 if scene_type == "course_catalog" else 3
    selected = ranked[:limit]
    semantic_map = {event_key(event): semantic_for_choice(event) for event in events}
    if scene_type == "auth_provider":
        return ensure_auth_branch_coverage(selected, ranked, semantic_map, limit)
    if scene_type == "course_catalog":
        return ensure_course_specificity(selected, ranked, semantic_map, limit)
    return selected


def ensure_auth_branch_coverage(
    selected: list[SessionEventRecord],
    ranked: list[SessionEventRecord],
    semantic_map: dict[tuple[int, float, str], SemanticEvent],
    limit: int,
) -> list[SessionEventRecord]:
    branches = {semantic_map[event_key(event)].branch for event in selected}
    if "existing" in branches and "create" in branches:
        return selected
    for event in ranked[len(selected):]:
        branch = semantic_map[event_key(event)].branch
        if branch in {"existing", "create"} and branch not in branches:
            selected.append(event)
            branches.add(branch)
        if len(selected) >= limit or {"existing", "create"} <= branches:
            break
    return selected[:limit]


def ensure_course_specificity(
    selected: list[SessionEventRecord],
    ranked: list[SessionEventRecord],
    semantic_map: dict[tuple[int, float, str], SemanticEvent],
    limit: int,
) -> list[SessionEventRecord]:
    if any(semantic_map[event_key(event)].entity for event in selected):
        return selected
    for event in ranked[len(selected):]:
        if semantic_map[event_key(event)].entity:
            selected.append(event)
            break
    return selected[:limit]


def semantic_for_choice(event: SessionEventRecord) -> SemanticEvent:
    label = normalize_label(event.target.label or event.target.text)
    transcript = normalize_label(event.metadata.get("transcript_excerpt", ""))
    from app.services.semantic_event_normalizer import semantic_action, semantic_branch, semantic_entity

    label_tokens = set(label.split())
    transcript_tokens = set(transcript.split())
    scene_type: SceneType = (
        "auth_provider" if {"account", "google", "log", "login", "sign", "signup", "create"} & label_tokens else "course_catalog"
    )
    return SemanticEvent(
        semantic_action=semantic_action(label_tokens, transcript_tokens, scene_type),
        entity=semantic_entity(label_tokens, transcript_tokens),
        branch=semantic_branch(label_tokens, transcript_tokens),
        score=event_local_score(event),
        scene_type=scene_type,
    )


def candidate_chains(
    choices: list[list[list[SessionEventRecord]]],
) -> list[list[SessionEventRecord]]:
    chains: list[list[SessionEventRecord]] = []
    for combination in product(*choices):
        chain = sorted((event for bundle in combination for event in bundle), key=lambda item: item.timestamp)
        if chain:
            chains.append(chain)
    return chains


def chain_score(
    chain: list[SessionEventRecord],
    scene_type: SceneType,
    expected: str,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> tuple[float, float, float, float]:
    semantics = [semantic_for_event(event, scenes_by_number, analyses_by_scene) for event in chain]
    action_score = sum(item.score for item in semantics)
    consistency = transition_consistency(semantics, scene_type, expected)
    specificity = sum(event_specificity(item, expected) for item in semantics)
    coverage = chain_coverage_score(chain)
    return round(action_score + consistency + specificity + coverage, 3), float(len(chain)), coverage, -chain[0].timestamp


def chain_coverage_score(chain: list[SessionEventRecord]) -> float:
    if len(chain) <= 1:
        return -0.45
    timestamps = sorted(event.timestamp for event in chain)
    spread = timestamps[-1] - timestamps[0]
    return round(min(len(chain) * 0.18 + spread / 12.0, 0.9), 3)


def transition_consistency(
    semantics: list[SemanticEvent],
    scene_type: SceneType,
    expected: str,
) -> float:
    if scene_type == "auth_provider":
        return auth_chain_consistency(semantics, expected)
    if scene_type == "course_catalog":
        return course_chain_consistency(semantics, expected)
    return 0.0


def auth_chain_consistency(semantics: list[SemanticEvent], expected: str) -> float:
    actions = [item.semantic_action for item in semantics]
    branches = [item.branch for item in semantics]
    score = 0.0
    if "auth_login_google" in actions:
        score += 1.2
    if "auth_choose_account" in actions:
        score += 1.5
    if "auth_login_google" in actions and "auth_choose_account" in actions:
        score += 2.4
    if "auth_signup_google" in actions or "auth_create_account" in actions:
        score -= 1.2
    if "existing" in branches and "create" in branches:
        score -= 3.2
    if actions and actions[-1] == "auth_choose_account":
        score += 0.9
    if expected == "account_existing":
        score += expected_existing_score(actions, branches)
    if expected == "account_create":
        score += expected_create_score(actions, branches)
    return score


def expected_existing_score(actions: list[str], branches: list[str]) -> float:
    score = 0.0
    if "auth_choose_account" in actions:
        score += 2.6
    if "auth_login_google" in actions:
        score += 1.7
    if "auth_signup_google" in actions or "auth_create_account" in actions:
        score -= 3.8
    if "create" in branches:
        score -= 2.4
    if not any(action in {"auth_login_google", "auth_choose_account"} for action in actions):
        score -= 2.0
    return score


def expected_create_score(actions: list[str], branches: list[str]) -> float:
    score = 0.0
    if "auth_signup_google" in actions or "auth_create_account" in actions:
        score += 1.5
    if "auth_choose_account" in actions:
        score -= 2.2
    if "existing" in branches:
        score -= 1.8
    return score


def course_chain_consistency(semantics: list[SemanticEvent], expected: str) -> float:
    score = 0.0
    if any(item.entity for item in semantics):
        score += 1.0
    if any(item.semantic_action == "course_open" for item in semantics):
        score += 0.8
    if any(item.semantic_action == "course_select" and item.entity for item in semantics):
        score += 1.0
    if any(item.semantic_action == "course_select" and not item.entity for item in semantics):
        score -= 1.1
    if expected and expected != "generic":
        score += expected_course_score(semantics, expected)
    return score


def expected_course_score(semantics: list[SemanticEvent], expected: str) -> float:
    score = 0.0
    expected_entity = expected.replace("course_", "")
    if any(item.entity == expected_entity for item in semantics):
        score += 1.6
    if any(item.entity and item.entity != expected_entity for item in semantics):
        score -= 1.2
    if any(item.semantic_action == "course_open" and item.entity == expected_entity for item in semantics):
        score += 0.9
    return score


def event_specificity(item: SemanticEvent, expected: str) -> float:
    score = 0.0
    if item.entity:
        score += 0.35
    if item.semantic_action in {"auth_login_google", "auth_choose_account", "course_open"}:
        score += 0.4
    if item.semantic_action in {"auth_signup_google", "auth_create_account"}:
        score -= 0.3
    if expected == "account_existing" and item.branch == "create":
        score -= 0.8
    if expected.startswith("course_") and item.entity and expected.replace("course_", "") != item.entity:
        score -= 0.6
    return score


def semantic_for_event(
    event: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> SemanticEvent:
    return semantic_event(
        event,
        scenes_by_number.get(scene_number(event)),
        analyses_by_scene.get(scene_number(event)),
    )


def event_local_score(event: SessionEventRecord) -> float:
    try:
        return float(event.metadata.get("score", "0"))
    except ValueError:
        return 0.0


def event_key(event: SessionEventRecord) -> tuple[int, float, str]:
    return scene_number(event), event.timestamp, normalize_label(event.target.label)


def cluster_expectation(
    cluster: FlowCluster,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    if cluster.scene_type == "auth_provider":
        return auth_cluster_expectation(cluster.scene_numbers, scenes_by_number, analyses_by_scene)
    if cluster.scene_type == "course_catalog":
        return course_cluster_expectation(cluster.scene_numbers, scenes_by_number, analyses_by_scene)
    return "generic"


def auth_cluster_expectation(
    scene_numbers: list[int],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    expectation = "generic"
    for scene_id in scene_numbers:
        scene = scenes_by_number.get(scene_id)
        if scene is None:
            continue
        resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
        labels = visible_labels(analyses_by_scene.get(scene_id))
        if resolution.intent == "account_existing" or "choose account" in labels:
            return "account_existing"
        if resolution.intent == "account_create":
            expectation = "account_create"
    return expectation


def course_cluster_expectation(
    scene_numbers: list[int],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    entity_scores: dict[str, float] = {}
    for scene_id in scene_numbers:
        scene = scenes_by_number.get(scene_id)
        if scene is None:
            continue
        resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
        for token in resolution.focus_tokens:
            entity_scores[token] = entity_scores.get(token, 0.0) + 1.0
        for label in visible_labels(analyses_by_scene.get(scene_id)).split():
            if label in {"japanese", "japan"}:
                entity_scores["japanese"] = entity_scores.get("japanese", 0.0) + 0.8
    if not entity_scores:
        return "generic"
    best = max(entity_scores.items(), key=lambda item: item[1])[0]
    if best == "japan":
        best = "japanese"
    return f"course_{best}"


def visible_labels(analysis: VisualSceneAnalysisRecord | None) -> str:
    if analysis is None:
        return ""
    labels = [normalize_label(label) for label in analysis.visible_labels if label.strip()]
    return " ".join(labels)
