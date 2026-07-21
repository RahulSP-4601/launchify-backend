from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, FocusBox

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
    if should_preserve_calm_motion(scene):
        return scene.model_copy(
            update={
                "camera_mode": "focus",
                "zooms": seeded_calm_zooms(scene),
                "highlights": seeded_calm_highlights(scene),
                "transition_style": "fade",
                "transition_duration_seconds": 0.24,
            }
        )
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


def should_preserve_calm_motion(scene: EditPlanScene) -> bool:
    if scene.scene_role != "action":
        return False
    if scene.action_class not in {"button_click", "focus"}:
        return False
    combined = " ".join(part.lower() for part in (scene.title, scene.on_screen_text, scene.purpose) if part)
    return any(token in combined for token in ("level", "settings", "preferences", "plan", "workspace", "role", "template", "setup"))


def seeded_calm_zooms(scene: EditPlanScene) -> list[EditPlanZoom]:
    aligned = beat_aligned_zooms(scene)
    if aligned:
        return aligned
    focus_box = default_setup_focus_box()
    focus_start = round(scene.focus_start_timestamp or scene.start, 2)
    focus_end = round(scene.focus_end_timestamp or min(scene.end, focus_start + 1.0), 2)
    settle_end = round(scene.settle_end_timestamp or min(scene.end, focus_end + 0.7), 2)
    return [
        EditPlanZoom(
            start=focus_start,
            end=settle_end,
            scale=1.07,
            focus_region="center",
            reason="calm setup focus",
            confidence=0.72,
            focus_box=focus_box,
            x_offset=0.0,
            y_offset=0.0,
            hold_ratio=0.82,
            smoothing=0.18,
        )
    ]


def seeded_calm_highlights(scene: EditPlanScene) -> list[EditPlanHighlight]:
    aligned = beat_aligned_highlights(scene)
    if aligned:
        return aligned
    focus_box = default_setup_focus_box()
    focus_start = round(scene.focus_start_timestamp or scene.start, 2)
    focus_end = round(min(scene.end, (scene.focus_end_timestamp or focus_start + 0.95)), 2)
    return [
        EditPlanHighlight(
            start=focus_start,
            end=focus_end,
            label=(scene.on_screen_text or scene.title or "Setup")[:48],
            style="soft-glow",
            anchor_region="center",
            confidence=0.72,
            focus_box=focus_box,
            placement_preference="avoid-ui-cover",
            ui_label=scene.on_screen_text or scene.title,
        )
    ]


def default_setup_focus_box() -> FocusBox:
    return FocusBox(x=0.24, y=0.2, width=0.52, height=0.32)
