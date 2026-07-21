from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import event_action_class
from app.services.auth_flow_refinement import refine_auth_flow_events
from app.services.course_flow_refinement import prune_course_cluster
from app.services.generic_target_labeling import promoted_target_label, should_promote_generic_label
from app.services.inferred_recording_support import normalize_label
from app.services.semantic_event_normalizer import SemanticEvent, semantic_event
from app.services.scene_intent_resolver import resolve_scene_intent
from app.services.visual_target_context import contextual_target_label, exact_visual_target_label

AUTH_BRANCH_CLASSES = frozenset({"auth_action", "button_click"})
COURSE_EVENT_CLASSES = frozenset({"card_selection", "button_click", "navigation"})
@dataclass(frozen=True)
class BranchDecision:
    scene_number: int
    label: str
    score: float


def refine_event_flow(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    auth_refined = refine_auth_flow_events(events, scenes, analyses_by_scene)
    dominant_auth = dominant_auth_decision(auth_refined, scenes)
    branch_refined = suppress_conflicting_auth_branch(auth_refined, scenes, dominant_auth)
    chain_refined = chain_refined_events(branch_refined, scenes, analyses_by_scene)
    pruned = prune_scene_cluster_conflicts(chain_refined, scenes, analyses_by_scene)
    return collapse_repeated_scene_actions(pruned, scenes)
def chain_refined_events(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    refined = suppress_weaker_branch_events(events, scenes_by_number, analyses_by_scene)
    return promote_entity_labels(refined, scenes_by_number, analyses_by_scene)
def prune_scene_cluster_conflicts(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    auth_pruned = prune_auth_cluster(events, scenes_by_number, analyses_by_scene)
    return prune_course_cluster(auth_pruned, scenes_by_number, analyses_by_scene, scene_number)
def prune_auth_cluster(
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    dominant = dominant_semantic_branch(events, scenes_by_number, analyses_by_scene)
    if dominant == "generic":
        return events
    selected: list[SessionEventRecord] = []
    auth_kept = False
    for event in sorted(events, key=lambda item: item.timestamp):
        semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analyses_by_scene.get(scene_number(event)))
        if semantic.scene_type not in {"auth_provider", "account_picker"}:
            selected.append(event)
            continue
        if semantic.branch not in {dominant, "generic"}:
            continue
        if should_keep_auth_progression(event, semantic, selected):
            selected.append(event)
            auth_kept = True
            continue
        if auth_kept and semantic.branch != dominant:
            continue
        if auth_kept and semantic.semantic_action in {"auth_entry_google", "auth_signup_google", "auth_create_account"}:
            continue
        selected.append(event)
        if semantic.branch == dominant or semantic.semantic_action in {"auth_login_google", "auth_choose_account"}:
            auth_kept = True
    return selected


def should_keep_auth_progression(
    event: SessionEventRecord,
    semantic: SemanticEvent,
    selected: list[SessionEventRecord],
) -> bool:
    if semantic.semantic_action not in {"auth_entry_google", "auth_login_google", "auth_choose_account"}:
        return False
    if not selected:
        return False
    previous = selected[-1]
    previous_scene = scene_number(previous)
    current_scene = scene_number(event)
    if current_scene <= previous_scene:
        return False
    previous_label = normalize_label(previous.target.label)
    current_label = normalize_label(event.target.label)
    return previous_label != current_label and event.timestamp > previous.timestamp


def suppress_weaker_branch_events(
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    dominant = dominant_semantic_branch(events, scenes_by_number, analyses_by_scene)
    if dominant == "generic":
        return events
    return [
        event
        for event in events
        if semantic_branch_for_event(event, scenes_by_number, analyses_by_scene) in {dominant, "generic"}
    ]
def dominant_semantic_branch(
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    existing_score = 0.0
    create_score = 0.0
    for event in events:
        semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analyses_by_scene.get(scene_number(event)))
        if semantic.branch == "existing":
            existing_score += semantic.score + 0.25
            if semantic.semantic_action == "auth_choose_account":
                existing_score += 0.7
        if semantic.branch == "create":
            create_score += semantic.score + 0.18
    auth_branches = [inferred_branch_from_scene(scene) for _, scene in sorted(scenes_by_number.items())]
    auth_branches = [branch for branch in auth_branches if branch != "generic"]
    if auth_branches:
        if auth_branches[-1] == "existing":
            existing_score += 1.1
        elif auth_branches[-1] == "create":
            create_score += 1.1
    if existing_score >= create_score + 0.2:
        return "existing"
    if create_score >= existing_score + 0.2:
        return "create"
    return "generic"
def semantic_branch_for_event(
    event: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analyses_by_scene.get(scene_number(event)))
    return semantic.branch
def promote_entity_labels(
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    promoted: list[SessionEventRecord] = []
    for event in events:
        if preserve_canonical_event(event):
            promoted.append(event)
            continue
        analysis = analyses_by_scene.get(scene_number(event))
        semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analysis)
        if preserve_focus_state_label(event):
            event.metadata["action_class"] = "result_state"
            promoted.append(event)
            continue
        if semantic.semantic_action in {"course_open", "course_select"}:
            replacement = exact_visual_target_label(analysis, event.timestamp, semantic.entity)
            if replacement:
                event = relabel_event(event, replacement)
            elif not semantic.entity:
                replacement = replacement_entity_label(event, events, scenes_by_number, analyses_by_scene)
                if replacement:
                    event = relabel_event(event, replacement)
        elif should_promote_generic_label(event.target.label) and not preserve_focus_state_label(event):
            replacement = replacement_focus_label(event, scenes_by_number, analyses_by_scene)
            if replacement:
                event = relabel_event(event, replacement)
        promoted.append(event)
    return promoted
def preserve_canonical_event(event: SessionEventRecord) -> bool:
    canonical = event.metadata.get("canonical_label", "").strip()
    return bool(canonical) and canonical == event.target.label.strip()
def preserve_focus_state_label(event: SessionEventRecord) -> bool:
    label = normalize_label(event.target.label)
    if event.type != "focus":
        return False
    return any(phrase in label for phrase in ("select a course", "pick your", "choose your", "before you start"))
def replacement_entity_label(
    event: SessionEventRecord,
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    target_scene = scene_number(event)
    target_semantic = semantic_event(event, scenes_by_number.get(target_scene), analyses_by_scene.get(target_scene))
    neighbors = [
        other
        for other in events
        if abs(scene_number(other) - target_scene) <= 1
    ]
    ranked = sorted(
        (
            semantic_event(other, scenes_by_number.get(scene_number(other)), analyses_by_scene.get(scene_number(other)))
            for other in neighbors
        ),
        key=lambda item: (1.0 if item.entity else 0.0, item.score),
        reverse=True,
    )
    entity = next((item.entity for item in ranked if compatible_entity(item.entity, target_semantic.semantic_action)), "")
    if entity in {"japan", "japanese"}:
        return "Japanese"
    return entity.capitalize().strip() if entity else ""
def compatible_entity(entity: str, semantic_action: str) -> bool:
    if not entity:
        return False
    if semantic_action in {"course_open", "course_select"}:
        return entity in {"japan", "japanese", "english", "german", "spanish", "french"}
    return True
def replacement_focus_label(
    event: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> str:
    scene = scenes_by_number.get(scene_number(event))
    if scene is None:
        return ""
    analysis = analyses_by_scene.get(scene_number(event))
    resolution = resolve_scene_intent(
        scene.source_excerpt,
        scene.spoken_line,
        scene_frame_progress(event, analysis),
    )
    visual_context = contextual_target_label(
        event.target.label,
        analysis,
        resolution,
        event.timestamp,
    )
    if visual_context:
        return visual_context
    return promoted_target_label(event.target.label, resolution)
def relabel_event(event: SessionEventRecord, label: str) -> SessionEventRecord:
    target = event.target.model_copy(update={"label": label, "text": label})
    return event.model_copy(update={"target": target})
def scene_frame_progress(
    event: SessionEventRecord,
    analysis: VisualSceneAnalysisRecord | None,
) -> float:
    if analysis is None:
        return 0.5
    duration = max(analysis.end - analysis.start, 0.01)
    relative_time = event.timestamp - analysis.start
    return min(max(relative_time / duration, 0.0), 1.0)
def dominant_auth_decision(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
) -> BranchDecision | None:
    auth_events = [event for event in events if auth_candidate_event(event)]
    if not auth_events:
        return None
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    ranked = sorted(
        (
            BranchDecision(scene_number(event), event.target.label, auth_branch_strength(event, scenes_by_number.get(scene_number(event))))
            for event in auth_events
        ),
        key=lambda item: item.score,
        reverse=True,
    )
    return ranked[0] if ranked else None
def auth_candidate_event(event: SessionEventRecord) -> bool:
    label_tokens = set(normalize_label(event.target.label).split())
    if not label_tokens & {"google", "login", "log", "sign", "signup", "create", "account", "choose", "existing"}:
        return False
    return event_action_class(event) in AUTH_BRANCH_CLASSES
def auth_branch_strength(
    event: SessionEventRecord,
    scene: LaunchScriptScene | None,
) -> float:
    tokens = set(normalize_label(event.target.label).split())
    score = float(event.metadata.get("score", "0") or 0.0)
    if {"login", "log"} & tokens or {"choose", "existing"} & tokens:
        score += 0.55
    if {"sign", "signup", "create"} & tokens:
        score += 0.18
    if scene is not None:
        resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
        if resolution.intent == "account_existing" and tokens & {"login", "log", "choose", "existing"}:
            score += 0.6
        if resolution.intent == "account_create" and tokens & {"sign", "signup", "create"}:
            score += 0.6
    return round(score, 3)
def suppress_conflicting_auth_branch(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    dominant: BranchDecision | None,
) -> list[SessionEventRecord]:
    if dominant is None:
        return events
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    dominant_branch = branch_kind(dominant.label)
    refined: list[SessionEventRecord] = []
    for event in events:
        if not auth_candidate_event(event):
            refined.append(event)
            continue
        current_branch = branch_kind(event.target.label)
        if current_branch == "generic":
            current_branch = inferred_branch_from_scene(scenes_by_number.get(scene_number(event)))
        if branch_conflicts(current_branch, dominant_branch) and weaker_than_dominant(event, dominant):
            continue
        refined.append(event)
    return refined
def branch_kind(label: str) -> str:
    tokens = set(normalize_label(label).split())
    if {"sign", "signup", "create"} & tokens:
        return "create"
    if {"login", "log", "choose", "existing"} & tokens:
        return "existing"
    return "generic"
def inferred_branch_from_scene(scene: LaunchScriptScene | None) -> str:
    if scene is None:
        return "generic"
    scene_tokens = set(normalize_label(f"{scene.on_screen_text} {scene.source_excerpt} {scene.spoken_line}").split())
    if {"sign", "signup", "create"} & scene_tokens:
        return "create"
    if {"login", "log", "choose", "existing"} & scene_tokens:
        return "existing"
    intent = resolve_scene_intent(scene.source_excerpt, scene.spoken_line).intent
    if intent == "account_existing":
        return "existing"
    if intent == "account_create":
        return "create"
    return "generic"
def branch_conflicts(current_branch: str, dominant_branch: str) -> bool:
    return current_branch != "generic" and dominant_branch != "generic" and current_branch != dominant_branch
def weaker_than_dominant(
    event: SessionEventRecord,
    dominant: BranchDecision,
) -> bool:
    event_score = float(event.metadata.get("score", "0") or 0.0)
    same_or_earlier = scene_number(event) <= dominant.scene_number
    return same_or_earlier and event_score <= dominant.score + 0.08
def collapse_repeated_scene_actions(
    events: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
) -> list[SessionEventRecord]:
    scenes_by_number = {scene.scene_number: scene for scene in scenes}
    refined: list[SessionEventRecord] = []
    for event in sorted(events, key=lambda item: item.timestamp):
        replacement_index = repeated_scene_index(event, refined, scenes_by_number)
        if replacement_index is None:
            refined.append(event)
            continue
        if stronger_scene_event(event, refined[replacement_index], scenes_by_number):
            refined[replacement_index] = event
    return refined
def repeated_scene_index(
    event: SessionEventRecord,
    selected: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
) -> int | None:
    return next(
        (
            index
            for index, existing in enumerate(selected)
            if same_scene_cluster(event, existing, scenes_by_number)
        ),
        None,
    )
def same_scene_cluster(
    left: SessionEventRecord,
    right: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
) -> bool:
    if scene_number(left) != scene_number(right):
        return False
    if event_action_class(left) not in COURSE_EVENT_CLASSES or event_action_class(right) not in COURSE_EVENT_CLASSES:
        return False
    left_tokens = set(normalize_label(left.target.label).split())
    right_tokens = set(normalize_label(right.target.label).split())
    if not left_tokens or not right_tokens:
        return False
    if "japanese" in left_tokens and "japanese" in right_tokens:
        return True
    scene = scenes_by_number.get(scene_number(left))
    if scene is None:
        return False
    resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    return bool(resolution.focus_tokens & left_tokens & right_tokens)
def stronger_scene_event(
    candidate: SessionEventRecord,
    current: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
) -> bool:
    candidate_score = flow_event_strength(candidate, scenes_by_number)
    current_score = flow_event_strength(current, scenes_by_number)
    return candidate_score >= current_score
def flow_event_strength(
    event: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
) -> float:
    score = float(event.metadata.get("score", "0") or 0.0)
    label_tokens = set(normalize_label(event.target.label).split())
    scene = scenes_by_number.get(scene_number(event))
    if scene is not None:
        resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
        score += len(label_tokens & resolution.focus_tokens) * 0.22
    if len(label_tokens) >= 2:
        score += 0.08
    return round(score, 3)
def scene_number(event: SessionEventRecord) -> int:
    return int(event.metadata.get("scene_number", "0") or 0)
