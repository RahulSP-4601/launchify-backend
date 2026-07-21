from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom
from app.services.editorial_flow import FlowSceneContext, scene_contexts
from app.services.walkthrough_windows import step_clip_window_with_context


def trim_edit_plan(edit_plan: EditPlanRecord) -> EditPlanRecord:
    contexts = scene_contexts(edit_plan.scenes)
    scenes = [trimmed_scene(scene, contexts.get(scene.scene_number)) for scene in edit_plan.scenes]
    total_duration = round(sum(scene_duration(scene) for scene in scenes), 2)
    return edit_plan.model_copy(
        update={
            "scenes": scenes,
            "total_duration_seconds": total_duration,
            "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": total_duration}),
        }
    )


def trimmed_scene(scene: EditPlanScene, context: FlowSceneContext | None) -> EditPlanScene:
    clip_start, clip_end = step_clip_window_with_context(scene, context)
    clip_start, clip_end = preserve_editorial_beats(scene, clip_start, clip_end)
    return scene.model_copy(
        update={
            "start": clip_start,
            "end": clip_end,
            "render_duration_seconds": round(max(clip_end - clip_start, 0.8), 2),
            "zooms": [bounded_zoom(zoom, clip_start, clip_end) for zoom in scene.zooms if zoom.end > clip_start and zoom.start < clip_end],
            "highlights": [
                bounded_highlight(highlight, clip_start, clip_end)
                for highlight in scene.highlights
                if highlight.end > clip_start and highlight.start < clip_end
            ],
            "action_timestamp": bounded_action_time(scene.action_timestamp, clip_start, clip_end),
        }
    )


def bounded_zoom(zoom: EditPlanZoom, start: float, end: float) -> EditPlanZoom:
    bounded_start = round(max(start, zoom.start), 2)
    bounded_end = round(min(end, max(zoom.end, bounded_start + 0.35)), 2)
    return zoom.model_copy(update={"start": bounded_start, "end": bounded_end})


def bounded_highlight(highlight: EditPlanHighlight, start: float, end: float) -> EditPlanHighlight:
    bounded_start = round(max(start, highlight.start), 2)
    max_duration = highlight_duration_limit(highlight)
    bounded_end = round(min(end, max(highlight.end, bounded_start + 0.35), bounded_start + 0.35), 2)
    bounded_end = round(min(bounded_end, bounded_start + max_duration), 2)
    return highlight.model_copy(update={"start": bounded_start, "end": bounded_end})


def bounded_action_time(action_timestamp: float | None, start: float, end: float) -> float | None:
    if action_timestamp is None:
        return None
    return round(min(max(action_timestamp, start), end), 2)


def preserve_editorial_beats(scene: EditPlanScene, clip_start: float, clip_end: float) -> tuple[float, float]:
    establish_start = min(
        value
        for value in (
            clip_start,
            scene.establish_end_timestamp or clip_start,
            scene.focus_start_timestamp or clip_start,
        )
    )
    settle_target = max(
        clip_end,
        scene.settle_end_timestamp or clip_end,
        (scene.result_anchor_timestamp + min(scene.readable_hold_seconds, 1.6)) if scene.result_anchor_timestamp is not None else clip_end,
    )
    bounded_start = round(max(scene.start, establish_start), 2)
    bounded_end = round(min(scene.end, settle_target), 2)
    if bounded_end - bounded_start < 0.8:
        bounded_end = round(min(scene.end, bounded_start + 0.8), 2)
    return bounded_start, bounded_end


def scene_duration(scene: EditPlanScene) -> float:
    return round(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8), 2)


def highlight_duration_limit(highlight: EditPlanHighlight) -> float:
    label = " ".join(part for part in (highlight.label, highlight.ui_label) if part).lower()
    if any(token in label for token in ("login", "google", "account")):
        return 1.45
    if any(token in label for token in ("course", "japanese")):
        return 1.35
    return 1.2
