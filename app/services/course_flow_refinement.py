from __future__ import annotations

from collections.abc import Callable

from app.models.projects import LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import event_action_class
from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent
from app.services.semantic_event_normalizer import semantic_event


def prune_course_cluster(
    events: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    scene_number: Callable[[SessionEventRecord], int],
) -> list[SessionEventRecord]:
    selected: list[SessionEventRecord] = []
    best_actions: list[SessionEventRecord] = []
    best_results: list[SessionEventRecord] = []
    for event in events:
        semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analyses_by_scene.get(scene_number(event)))
        if semantic.scene_type != "course_catalog":
            selected.append(event)
            continue
        bucket = best_results if event_action_class(event) == "result_state" else best_actions
        replacement_index = matching_course_cluster_index(event, bucket, scenes_by_number, analyses_by_scene, scene_number)
        if replacement_index is None:
            bucket.append(event)
            continue
        current = bucket[replacement_index]
        if stronger_course_event(event, current, scenes_by_number, analyses_by_scene, scene_number):
            bucket[replacement_index] = event
    selected.extend(best_actions)
    selected.extend(best_results)
    return sorted(selected, key=lambda item: item.timestamp)


def matching_course_cluster_index(
    candidate: SessionEventRecord,
    bucket: list[SessionEventRecord],
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    scene_number: Callable[[SessionEventRecord], int],
) -> int | None:
    for index, current in enumerate(bucket):
        if same_course_cluster(candidate, current, scenes_by_number, analyses_by_scene, scene_number):
            return index
    return None


def same_course_cluster(
    candidate: SessionEventRecord,
    current: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    scene_number: Callable[[SessionEventRecord], int],
) -> bool:
    candidate_scene = scene_number(candidate)
    current_scene = scene_number(current)
    if candidate_scene <= 0 or current_scene <= 0:
        return False
    if abs(candidate_scene - current_scene) > 1:
        return False
    if abs(candidate.timestamp - current.timestamp) > 10.0:
        return False
    candidate_semantic = semantic_event(
        candidate,
        scenes_by_number.get(candidate_scene),
        analyses_by_scene.get(candidate_scene),
    )
    current_semantic = semantic_event(
        current,
        scenes_by_number.get(current_scene),
        analyses_by_scene.get(current_scene),
    )
    if candidate_semantic.scene_type != "course_catalog" or current_semantic.scene_type != "course_catalog":
        return False
    if candidate_semantic.entity and current_semantic.entity:
        return candidate_semantic.entity == current_semantic.entity
    candidate_label = normalize_label(candidate.target.label)
    current_label = normalize_label(current.target.label)
    if candidate_label == current_label and candidate_label:
        return True
    generic_labels = {"select a course", "open course"}
    if candidate_label in generic_labels or current_label in generic_labels:
        return generic_course_cluster_continuity(candidate, current, scenes_by_number, scene_number)
    if candidate_semantic.semantic_action != current_semantic.semantic_action:
        return False
    return generic_course_cluster_continuity(candidate, current, scenes_by_number, scene_number)


def generic_course_cluster_continuity(
    candidate: SessionEventRecord,
    current: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    scene_number: Callable[[SessionEventRecord], int],
) -> bool:
    if abs(candidate.timestamp - current.timestamp) > 3.5:
        return False
    candidate_excerpt = normalize_label(candidate.metadata.get("transcript_excerpt", ""))
    current_excerpt = normalize_label(current.metadata.get("transcript_excerpt", ""))
    if candidate_excerpt and current_excerpt and candidate_excerpt == current_excerpt:
        return True
    candidate_scene = scenes_by_number.get(scene_number(candidate))
    current_scene = scenes_by_number.get(scene_number(current))
    if candidate_scene is None or current_scene is None:
        return False
    candidate_focus = resolve_scene_intent(candidate_scene.source_excerpt, candidate_scene.spoken_line).focus_tokens
    current_focus = resolve_scene_intent(current_scene.source_excerpt, current_scene.spoken_line).focus_tokens
    if not candidate_focus or not current_focus:
        return False
    shared_focus = candidate_focus & current_focus
    if not shared_focus:
        return False
    candidate_label_tokens = set(normalize_label(candidate.target.label).split())
    current_label_tokens = set(normalize_label(current.target.label).split())
    return bool(shared_focus & (candidate_label_tokens | current_label_tokens))


def stronger_course_event(
    candidate: SessionEventRecord,
    current: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    scene_number: Callable[[SessionEventRecord], int],
) -> bool:
    return course_event_strength(candidate, scenes_by_number, analyses_by_scene, scene_number) >= course_event_strength(
        current,
        scenes_by_number,
        analyses_by_scene,
        scene_number,
    )


def course_event_strength(
    event: SessionEventRecord,
    scenes_by_number: dict[int, LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    scene_number: Callable[[SessionEventRecord], int],
) -> float:
    semantic = semantic_event(event, scenes_by_number.get(scene_number(event)), analyses_by_scene.get(scene_number(event)))
    score = semantic.score
    if semantic.entity:
        score += 0.5
    if semantic.semantic_action == "course_open":
        score += 0.22
    if "open course" in normalize_label(event.target.label):
        score += 0.12
    if normalize_label(event.target.label) in {"select a course", "open course"}:
        score -= 0.35
    return round(score, 3)
