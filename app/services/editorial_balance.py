from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, SELECTION_FAMILY, scene_contexts


def rebalance_editorial_pacing(edit_plan: EditPlanRecord) -> EditPlanRecord:
    contexts = scene_contexts(edit_plan.scenes)
    ordered = sorted(edit_plan.scenes, key=lambda scene: (scene.start, scene.scene_number))
    balanced: list[EditPlanScene] = []
    for scene in ordered:
        context = contexts.get(scene.scene_number)
        rebalanced = rebalanced_scene(scene, context)
        if scene_has_source_span(rebalanced):
            balanced.append(rebalanced)
    total_duration = round(sum(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8) for scene in balanced), 2)
    return edit_plan.model_copy(
        update={
            "scenes": balanced,
            "total_duration_seconds": total_duration,
            "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": total_duration}),
        }
    )


def rebalanced_scene(scene: EditPlanScene, context: FlowSceneContext | None) -> EditPlanScene:
    if context is None:
        return scene
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    minimum, maximum = duration_band(scene, context)
    if duration < minimum:
        new_end = round(scene.start + minimum, 2)
    elif duration > maximum:
        new_end = constrained_end(scene, maximum)
    else:
        new_end = round(scene.end, 2)
    if context.next_scene is not None:
        new_end = min(new_end, max(scene.start, round(context.next_scene.start - 0.01, 2)))
    source_duration = round(max(new_end - scene.start, 0.0), 2)
    render_duration = round(max(source_duration, 0.8), 2) if source_duration > 0.0 else source_duration
    return scene.model_copy(update={"end": new_end, "render_duration_seconds": render_duration})


def constrained_end(scene: EditPlanScene, maximum: float) -> float:
    anchor = scene.result_anchor_timestamp or scene.action_timestamp or scene.start
    readable = max(scene.readable_hold_seconds, 0.8)
    floor = max(scene.start + 0.8, anchor + min(readable * 0.6, 1.4))
    target = scene.start + maximum
    return round(max(floor, target), 2)


def duration_band(scene: EditPlanScene, context: FlowSceneContext) -> tuple[float, float]:
    readability = max(scene.readable_hold_seconds, 0.8)
    if context.family == AUTH_FAMILY:
        if context.is_first:
            return 7.0 + min(readability * 0.2, 0.6), 9.2
        return 2.8, 4.4
    if context.family == SELECTION_FAMILY:
        return (5.2 + min(readability * 0.18, 0.5), 7.1) if context.next_scene is not None else (4.6, 6.6)
    if context.family == CONFIG_FAMILY:
        return 3.4 + min(readability * 0.16, 0.45), 5.2
    if scene.scene_role == "result":
        return 2.6, 4.8
    return 2.2, 5.8


def scene_has_source_span(scene: EditPlanScene) -> bool:
    return round(scene.end - scene.start, 2) > 0.0
