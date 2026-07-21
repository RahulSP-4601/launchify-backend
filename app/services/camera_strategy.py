from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom

CALM_LAYOUTS = {"screen-only", "dashboard-wide"}


def apply_camera_strategy(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [direct_scene_camera(scene) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def direct_scene_camera(scene: EditPlanScene) -> EditPlanScene:
    if scene.layout_mode in CALM_LAYOUTS:
        return calm_scene(scene)
    return scene.model_copy(
        update={
            "zooms": beat_aligned_zooms(scene),
            "highlights": beat_aligned_highlights(scene),
            "transition_style": scene_transition_style(scene),
            "transition_duration_seconds": scene_transition_duration(scene),
        }
    )


def calm_scene(scene: EditPlanScene) -> EditPlanScene:
    return scene.model_copy(
        update={
            "camera_mode": "static",
            "zooms": [],
            "highlights": [],
            "transition_style": "fade",
            "transition_duration_seconds": 0.24,
        }
    )


def beat_aligned_zooms(scene: EditPlanScene) -> list[EditPlanZoom]:
    if not scene.zooms:
        return []
    focus_start = scene.focus_start_timestamp or scene.start
    focus_end = scene.focus_end_timestamp or scene.end
    settle_end = scene.settle_end_timestamp or scene.end
    refined: list[EditPlanZoom] = []
    for index, zoom in enumerate(scene.zooms[:2]):
        start = max(scene.start, focus_start if index == 0 else focus_end)
        end = min(scene.end, max(focus_end if index == 0 else settle_end, start + 0.46))
        refined.append(
            zoom.model_copy(
                update={
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "hold_ratio": max(zoom.hold_ratio, 0.74 if index == 0 else 0.82),
                    "smoothing": max(zoom.smoothing, 0.16),
                }
            )
        )
    return refined


def beat_aligned_highlights(scene: EditPlanScene) -> list[EditPlanHighlight]:
    if not scene.highlights:
        return []
    focus_start = scene.focus_start_timestamp or scene.start
    focus_end = scene.focus_end_timestamp or scene.end
    refined: list[EditPlanHighlight] = []
    for highlight in scene.highlights[:1]:
        start = max(scene.start, min(highlight.start, focus_start))
        end = min(scene.end, max(highlight.end, focus_end))
        refined.append(
            highlight.model_copy(
                update={
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "style": "soft-glow" if highlight.style == "spotlight" else highlight.style,
                }
            )
        )
    return refined


def scene_transition_style(scene: EditPlanScene) -> str:
    if scene.layout_mode == "split-right":
        return "focus-push"
    if scene.scene_role == "result":
        return "fade"
    return scene.transition_style


def scene_transition_duration(scene: EditPlanScene) -> float:
    if scene.layout_mode in CALM_LAYOUTS:
        return 0.24
    if scene.layout_mode == "split-right":
        return min(max(scene.transition_duration_seconds, 0.28), 0.36)
    return min(scene.transition_duration_seconds, 0.32)
