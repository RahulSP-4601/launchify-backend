from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, FocusBox
from app.services.editorial_coverage import scene_profile

CALM_LAYOUTS = {"screen-only", "dashboard-wide"}
SAFE_MARGIN = 0.05
TARGET_MIN_AREA = 0.05
TARGET_MAX_AREA = 0.22


def apply_camera_strategy(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [direct_scene_camera(scene) for scene in edit_plan.scenes]
    scenes = [continuous_scene_camera(index, scenes) for index in range(len(scenes))]
    return edit_plan.model_copy(update={"scenes": scenes})


def direct_scene_camera(scene: EditPlanScene) -> EditPlanScene:
    if scene.layout_mode in CALM_LAYOUTS:
        return calm_scene(scene)
    directed = scene.model_copy(
        update={
            "zooms": beat_aligned_zooms(scene),
            "highlights": beat_aligned_highlights(scene),
            "transition_style": scene_transition_style(scene),
            "transition_duration_seconds": scene_transition_duration(scene),
        }
    )
    return normalize_scene_focus(directed)


def calm_scene(scene: EditPlanScene) -> EditPlanScene:
    if should_preserve_calm_motion(scene):
        return normalize_scene_focus(
            scene.model_copy(
            update={
                "camera_mode": "focus",
                "zooms": seeded_calm_zooms(scene),
                "highlights": seeded_calm_highlights(scene),
                "transition_style": "fade",
                "transition_duration_seconds": 0.24,
            }
            )
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
    windows = zoom_windows(scene, focus_start, focus_end, settle_end)
    refined: list[EditPlanZoom] = []
    for index, zoom in enumerate(scene.zooms):
        start, end = windows[min(index, len(windows) - 1)]
        refined.append(
            zoom.model_copy(
                update={
                    "start": start,
                    "end": end,
                    "hold_ratio": zoom_hold_ratio(index, zoom.hold_ratio),
                    "smoothing": zoom_smoothing(index, zoom.smoothing),
                    "focus_box": composition_safe_box(zoom.focus_box, scene, stage=index),
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
    scene_type = scene_profile(scene)
    for highlight in scene.highlights:
        start = max(scene.start, min(highlight.start, focus_start))
        end = min(scene.end, max(highlight.end, focus_end, highlight_end_floor(scene, scene_type, start)))
        refined.append(
            highlight.model_copy(
                update={
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "style": "soft-glow" if highlight.style == "spotlight" else highlight.style,
                    "focus_box": composition_safe_box(highlight.focus_box, scene),
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
    if scene.zooms or scene.highlights:
        return True
    if scene.action_class in {"auth_action", "card_selection"}:
        return scene_focus_box(scene) is not None
    if scene.action_class not in {"button_click", "focus"}:
        return False
    combined = " ".join(part.lower() for part in (scene.title, scene.on_screen_text, scene.purpose) if part)
    return any(token in combined for token in ("level", "settings", "preferences", "plan", "workspace", "role", "template", "setup"))


def seeded_calm_zooms(scene: EditPlanScene) -> list[EditPlanZoom]:
    aligned = beat_aligned_zooms(scene)
    if aligned:
        return aligned
    focus_box = composition_safe_box(scene_focus_box(scene) or default_calm_focus_box(scene), scene)
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
    focus_box = composition_safe_box(scene_focus_box(scene) or default_calm_focus_box(scene), scene)
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


def default_calm_focus_box(scene: EditPlanScene) -> FocusBox:
    return default_setup_focus_box()


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


def normalize_scene_focus(scene: EditPlanScene) -> EditPlanScene:
    return scene.model_copy(
        update={
            "zooms": [
                zoom.model_copy(update={"focus_box": composition_safe_box(zoom.focus_box, scene, stage=index)})
                for index, zoom in enumerate(scene.zooms)
            ],
            "highlights": [
                highlight.model_copy(update={"focus_box": composition_safe_box(highlight.focus_box, scene)})
                for highlight in scene.highlights
            ],
        }
    )


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


def zoom_windows(
    scene: EditPlanScene,
    focus_start: float,
    focus_end: float,
    settle_end: float,
) -> list[tuple[float, float]]:
    if len(scene.zooms) <= 1:
        return [safe_zoom_window(scene, focus_start, settle_end)]
    if len(scene.zooms) == 2:
        return [
            safe_zoom_window(scene, focus_start, focus_end),
            safe_zoom_window(scene, focus_end, settle_end),
        ]
    anchors = [
        round(scene.start, 2),
        round(max(scene.start, min(focus_start, focus_end - 0.24)), 2),
        round(focus_end, 2),
        round(settle_end, 2),
        round(scene.end, 2),
    ]
    windows = [
        (anchors[0], anchors[1]),
        (anchors[1], anchors[2]),
        (anchors[2], anchors[3]),
        (anchors[3], anchors[4]),
    ]
    return [safe_zoom_window(scene, start, end) for start, end in windows]


def safe_zoom_window(scene: EditPlanScene, start: float, end: float) -> tuple[float, float]:
    bounded_start = max(scene.start, start)
    bounded_end = min(scene.end, max(end, bounded_start + 0.46))
    return round(bounded_start, 2), round(bounded_end, 2)


def zoom_hold_ratio(index: int, current: float) -> float:
    if index == 0:
        return max(current, 0.78)
    if index == 1:
        return max(current, 0.86)
    return max(current, 0.9)


def zoom_smoothing(index: int, current: float) -> float:
    baseline = 0.2 if index <= 1 else 0.24
    return max(current, baseline)


def softened_neighbor_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [
        highlight.model_copy(update={"style": "ambient" if highlight.style == "soft-glow" else highlight.style, "confidence": max(highlight.confidence, 0.72)})
        for highlight in highlights
    ]


def highlight_end_floor(scene: EditPlanScene, scene_type: str, start: float) -> float:
    if scene_type == "auth_card":
        return start + 1.35
    if scene_type in {"course_card", "setup_choice"}:
        return start + 1.25
    if scene.scene_role == "action":
        return start + 0.96
    return start + 0.72


def composition_safe_box(box: FocusBox | None, scene: EditPlanScene, stage: int = 0) -> FocusBox | None:
    if box is None:
        return None
    width = max(box.width, TARGET_MIN_AREA ** 0.5)
    height = max(box.height, TARGET_MIN_AREA ** 0.5)
    area = width * height
    if area > TARGET_MAX_AREA:
        shrink = (TARGET_MAX_AREA / area) ** 0.5
        width *= shrink
        height *= shrink
    if stage == 0 and scene.scene_role == "action":
        width = min(width * 1.08, 0.42)
        height = min(height * 1.08, 0.34)
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    x = clamp(center_x - width / 2, SAFE_MARGIN, 1.0 - SAFE_MARGIN - width)
    y = clamp(center_y - height / 2, SAFE_MARGIN, 1.0 - SAFE_MARGIN - height)
    return FocusBox(x=round(x, 4), y=round(y, 4), width=round(width, 4), height=round(height, 4))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


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
