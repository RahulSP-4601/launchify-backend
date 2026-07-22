from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, FocusBox


def reference_style_score(edit_plan: EditPlanRecord) -> float:
    if not edit_plan.scenes:
        return 0.0
    scene_average = sum(scene_reference_score(scene) for scene in edit_plan.scenes) / len(edit_plan.scenes)
    return weighted_average(
        (scene_average, 0.84),
        (plan_continuity_score(edit_plan), 0.16),
    )


def scene_reference_score(scene: EditPlanScene) -> float:
    return weighted_average(
        (scene_phase_coverage_score(scene), 0.2),
        (zoom_choreography_score(scene), 0.2),
        (highlight_continuity_score(scene), 0.16),
        (result_readability_score(scene), 0.16),
        (cursor_commitment_score(scene), 0.14),
        (composition_score(scene), 0.08),
        (dead_air_score(scene), 0.06),
    )


def scene_phase_coverage_score(scene: EditPlanScene) -> float:
    if scene.scene_role != "action":
        return 1.0 if scene.scene_role == "result" and scene.readable_hold_seconds >= 0.8 else 0.82
    action_time = scene.action_timestamp
    if action_time is None:
        return 0.28 if scene.camera_mode == "focus" else 0.58
    score = 0.36
    if scene.establish_end_timestamp is not None and scene.establish_end_timestamp <= action_time - 0.16:
        score += 0.2
    if scene.focus_start_timestamp is not None and scene.focus_start_timestamp <= action_time - 0.08:
        score += 0.2
    if scene.result_anchor_timestamp is not None and scene.result_anchor_timestamp >= action_time + 0.18:
        score += 0.14
    if scene.settle_end_timestamp is not None and scene.settle_end_timestamp >= action_time + 0.34:
        score += 0.1
    return min(score, 1.0)


def zoom_choreography_score(scene: EditPlanScene) -> float:
    if scene.camera_mode == "static":
        return 1.0 if scene.scene_role != "action" else 0.52
    if not scene.zooms:
        return 0.24
    if len(scene.zooms) == 1:
        return 0.62 if scene.scene_role == "result" else 0.54
    contiguous = sum(1 for previous, current in zip(scene.zooms, scene.zooms[1:]) if current.start <= previous.end + 0.24)
    variety = sum(1 for zoom in scene.zooms if zoom.easing != "ease-in-out")
    span = max(scene.zooms[-1].end - scene.zooms[0].start, 0.0)
    coverage = min(1.0, span / max(scene.end - scene.start, 0.8))
    return min(1.0, 0.42 + contiguous * 0.18 + min(variety, 2) * 0.08 + coverage * 0.18)


def highlight_continuity_score(scene: EditPlanScene) -> float:
    if scene.scene_role != "action":
        return 1.0 if not scene.highlights else 0.72
    if not scene.highlights:
        return 0.24
    action_time = scene.action_timestamp or scene.start
    earliest_start = min(highlight.start for highlight in scene.highlights)
    latest_end = max(highlight.end for highlight in scene.highlights)
    styles = {highlight.style for highlight in scene.highlights}
    score = 0.42
    if earliest_start <= action_time - 0.08:
        score += 0.18
    if latest_end >= (scene.result_anchor_timestamp or action_time + 0.42):
        score += 0.2
    if len(scene.highlights) >= 2:
        score += 0.12
    if "ambient" in styles and len(styles) > 1:
        score += 0.08
    return min(score, 1.0)


def result_readability_score(scene: EditPlanScene) -> float:
    if scene.scene_role == "explanation":
        return 1.0
    anchor = scene.result_anchor_timestamp or scene.action_timestamp
    if anchor is None:
        return 0.54
    hold = max(scene.readable_hold_seconds, scene.end - anchor, 0.0)
    if hold >= 1.15:
        return 1.0
    if hold >= 0.85:
        return 0.82
    if hold >= 0.6:
        return 0.62
    return 0.34


def cursor_commitment_score(scene: EditPlanScene) -> float:
    if scene.scene_role != "action" or scene.action_timestamp is None:
        return 1.0
    establish = scene.establish_end_timestamp
    focus_start = scene.focus_start_timestamp
    if establish is None and focus_start is None:
        return 0.26
    score = 0.38
    if establish is not None and establish <= scene.action_timestamp - 0.26:
        score += 0.22
    if focus_start is not None and focus_start <= scene.action_timestamp - 0.1:
        score += 0.24
    if scene.result_anchor_timestamp is not None and scene.result_anchor_timestamp >= scene.action_timestamp + 0.18:
        score += 0.16
    return min(score, 1.0)


def composition_score(scene: EditPlanScene) -> float:
    box = primary_focus_box(scene)
    if box is None:
        return 0.52 if scene.camera_mode == "static" else 0.28
    area = box.width * box.height
    occupancy = occupancy_score(area, scene.scene_role)
    safe = safe_margin_score(box)
    return min(1.0, occupancy * 0.56 + safe * 0.44)


def dead_air_score(scene: EditPlanScene) -> float:
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.0)
    if duration <= 0.0:
        return 0.0
    if scene.scene_role == "result":
        return 1.0 if duration <= 4.4 else 0.72
    motion_density = min((len(scene.zooms) * 0.34) + (len(scene.highlights) * 0.28), 1.0)
    if duration <= 2.5:
        return max(0.64, motion_density)
    if scene.camera_mode == "static":
        return 0.34
    return min(1.0, 0.38 + motion_density * 0.62)


def plan_continuity_score(edit_plan: EditPlanRecord) -> float:
    if len(edit_plan.scenes) < 2:
        return 1.0
    scores = [adjacent_scene_continuity(left, right) for left, right in zip(edit_plan.scenes, edit_plan.scenes[1:])]
    return sum(scores) / len(scores)


def adjacent_scene_continuity(left: EditPlanScene, right: EditPlanScene) -> float:
    left_box = primary_focus_box(left)
    right_box = primary_focus_box(right)
    if left_box is None or right_box is None:
        return 0.74 if left.layout_mode == right.layout_mode else 0.56
    if left.layout_mode != right.layout_mode:
        return 0.52
    distance = center_distance(left_box, right_box)
    if distance <= 0.08:
        return 1.0
    if distance <= 0.16:
        return 0.82
    if distance <= 0.24:
        return 0.64
    return 0.42


def primary_focus_box(scene: EditPlanScene) -> FocusBox | None:
    if scene.highlights and scene.highlights[0].focus_box is not None:
        return scene.highlights[0].focus_box
    if scene.zooms and scene.zooms[0].focus_box is not None:
        return scene.zooms[0].focus_box
    return None


def occupancy_score(area: float, scene_role: str) -> float:
    target_min = 0.07 if scene_role == "result" else 0.05
    target_max = 0.24 if scene_role == "result" else 0.22
    if target_min <= area <= target_max:
        return 1.0
    if area < target_min:
        return max(0.22, area / target_min)
    overflow = min((area - target_max) / max(1.0 - target_max, 0.01), 1.0)
    return max(0.18, 1.0 - overflow)


def safe_margin_score(box: FocusBox) -> float:
    margin = min(box.x, box.y, 1.0 - (box.x + box.width), 1.0 - (box.y + box.height))
    if margin >= 0.05:
        return 1.0
    if margin >= 0.03:
        return 0.78
    if margin >= 0.015:
        return 0.56
    return 0.3


def center_distance(left: FocusBox, right: FocusBox) -> float:
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)


def weighted_average(*pairs: tuple[float, float]) -> float:
    total_weight = sum(weight for _value, weight in pairs)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in pairs) / total_weight
