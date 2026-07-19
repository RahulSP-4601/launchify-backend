from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import box_area
from app.services.walkthrough_windows import action_result_window

PRE_ZOOM_LEAD = 0.28
HIGHLIGHT_DURATION = 1.2


def sync_edit_plan_timing(
    edit_plan: EditPlanRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None,
) -> EditPlanRecord:
    analyses_by_scene = {analysis.scene_number: analysis for analysis in visual_analyses or []}
    scenes = [synced_scene(scene, analyses_by_scene.get(scene.scene_number), len(edit_plan.scenes)) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def synced_scene(
    scene: EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    scene_count: int,
) -> EditPlanScene:
    action_time = action_timestamp(scene, analysis)
    return scene.model_copy(
        update={
            "action_timestamp": action_time,
            "zooms": synced_zooms(scene, action_time),
            "highlights": synced_highlights(scene, action_time),
            "transition_style": transition_style(scene, scene_count),
            "transition_duration_seconds": transition_duration(scene),
        }
    )


def action_timestamp(scene: EditPlanScene, analysis: VisualSceneAnalysisRecord | None) -> float | None:
    if analysis is None or not analysis.frames:
        return None
    scored_frames = sorted(analysis.frames, key=lambda frame: action_frame_score(frame), reverse=True)
    if not scored_frames:
        return None
    best_time = max(scene.start, min(scene.end, scored_frames[0].timestamp))
    return round(best_time, 2)


def action_frame_score(frame: object) -> float:
    click_target_box = getattr(frame, "click_target_box", None)
    compact_focus = max(0.0, 0.14 - box_area(click_target_box)) if click_target_box is not None else 0.0
    return (
        getattr(frame, "click_confidence", 0.0) * 0.44
        + getattr(frame, "importance_score", 0.0) * 0.24
        + getattr(frame, "diff_score", 0.0) * 0.16
        + compact_focus
    )


def synced_zooms(
    scene: EditPlanScene,
    action_time: float | None,
) -> list[EditPlanZoom]:
    if action_time is None or not scene.zooms:
        return scene.zooms
    original_start = min(zoom.start for zoom in scene.zooms)
    original_end = max(zoom.end for zoom in scene.zooms)
    target_start = action_time - PRE_ZOOM_LEAD
    shift = target_start - original_start
    shifted_start = original_start + shift
    shifted_end = original_end + shift
    if shifted_start < scene.start:
        shift += scene.start - shifted_start
    if shifted_end > scene.end:
        shift -= shifted_end - scene.end
    synced: list[EditPlanZoom] = []
    for zoom in scene.zooms:
        start = max(scene.start, zoom.start + shift)
        end = min(scene.end, zoom.end + shift)
        if end <= start:
            end = min(scene.end, start + 0.5)
        synced.append(zoom.model_copy(update={"start": round(start, 2), "end": round(end, 2)}))
    return synced


def synced_highlights(
    scene: EditPlanScene,
    action_time: float | None,
) -> list[EditPlanHighlight]:
    if action_time is None:
        return scene.highlights
    synced = []
    for highlight in scene.highlights:
        start, end, _settle_end = action_result_window(scene.start, scene.end, action_time, scene.spoken_line)
        end = min(scene.end, max(end, start + HIGHLIGHT_DURATION * 0.75))
        synced.append(highlight.model_copy(update={"start": round(start, 2), "end": round(end, 2)}))
    return synced


def transition_style(scene: EditPlanScene, scene_count: int) -> str:
    if scene.scene_number == 1:
        return "slide-up"
    if scene.scene_number == scene_count:
        return "fade"
    if scene.camera_mode == "focus":
        return "focus-push"
    return "fade"


def transition_duration(scene: EditPlanScene) -> float:
    if scene.camera_mode == "focus":
        return 0.4
    return 0.28
