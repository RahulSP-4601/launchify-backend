from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom
from app.services.walkthrough_windows import step_clip_window


def trim_edit_plan(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [trimmed_scene(scene) for scene in edit_plan.scenes]
    total_duration = round(sum(scene_duration(scene) for scene in scenes), 2)
    return edit_plan.model_copy(
        update={
            "scenes": scenes,
            "total_duration_seconds": total_duration,
            "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": total_duration}),
        }
    )


def trimmed_scene(scene: EditPlanScene) -> EditPlanScene:
    clip_start, clip_end = step_clip_window(scene)
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
    bounded_end = round(min(end, max(highlight.end, bounded_start + 0.35)), 2)
    return highlight.model_copy(update={"start": bounded_start, "end": bounded_end})


def bounded_action_time(action_timestamp: float | None, start: float, end: float) -> float | None:
    if action_timestamp is None:
        return None
    return round(min(max(action_timestamp, start), end), 2)


def scene_duration(scene: EditPlanScene) -> float:
    return round(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8), 2)
