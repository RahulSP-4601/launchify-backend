from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, FocusBox

CALM_LAYOUTS = {"screen-only", "dashboard-wide"}


def apply_camera_strategy(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [direct_scene_camera(scene) for scene in edit_plan.scenes]
    scenes = [continuous_scene_camera(index, scenes) for index in range(len(scenes))]
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


def continuous_scene_camera(index: int, scenes: list[EditPlanScene]) -> EditPlanScene:
    scene = scenes[index]
    current = scene_focus_box(scene)
    previous = nearest_focus_box(scenes, index, -1)
    if current is None or previous is None:
        return scene
    if not shares_motion_context(scene, scenes[index - 1] if index > 0 else None):
        return scene
    if box_distance(current, previous) > 0.1:
        return scene
    return scene.model_copy(
        update={
            "zooms": softened_neighbor_zooms(scene.zooms),
            "highlights": softened_neighbor_highlights(scene.highlights),
            "transition_style": "fade" if scene.layout_mode in CALM_LAYOUTS else scene.transition_style,
        }
    )


def scene_focus_box(scene: EditPlanScene) -> FocusBox | None:
    if scene.highlights and scene.highlights[0].focus_box is not None:
        return scene.highlights[0].focus_box
    if scene.zooms and scene.zooms[0].focus_box is not None:
        return scene.zooms[0].focus_box
    return None


def nearest_focus_box(scenes: list[EditPlanScene], start_index: int, step: int) -> FocusBox | None:
    index = start_index + step
    while 0 <= index < len(scenes):
        candidate = scene_focus_box(scenes[index])
        if candidate is not None:
            return candidate
        index += step
    return None


def softened_neighbor_zooms(zooms: list[EditPlanZoom]) -> list[EditPlanZoom]:
    softened: list[EditPlanZoom] = []
    for index, zoom in enumerate(zooms):
        softened.append(
            zoom.model_copy(
                update={
                    "scale": round(max(1.0, zoom.scale - (0.02 if index == 0 else 0.01)), 2),
                    "smoothing": max(zoom.smoothing, 0.2),
                    "hold_ratio": max(zoom.hold_ratio, 0.8),
                }
            )
        )
    return softened


def softened_neighbor_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [
        highlight.model_copy(update={"style": "ambient" if highlight.style == "soft-glow" else highlight.style})
        for highlight in highlights
    ]


def box_distance(left: FocusBox, right: FocusBox) -> float:
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)


def shares_motion_context(scene: EditPlanScene, previous_scene: EditPlanScene | None) -> bool:
    if previous_scene is None:
        return False
    if scene.layout_mode != previous_scene.layout_mode:
        return False
    if scene.scene_role != previous_scene.scene_role:
        return False
    if scene.action_class == previous_scene.action_class:
        return True
    return scene_family(scene.action_class) == scene_family(previous_scene.action_class)


def scene_family(action_class: str) -> str:
    if action_class in {"auth_action"}:
        return "auth"
    if action_class in {"card_selection", "button_click", "focus"}:
        return "guided-selection"
    return action_class or "generic"
