from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene


def reference_style_score(edit_plan: EditPlanRecord) -> float:
    if not edit_plan.scenes:
        return 0.0
    return sum(scene_reference_score(scene) for scene in edit_plan.scenes) / len(edit_plan.scenes)


def scene_reference_score(scene: EditPlanScene) -> float:
    return weighted_average(
        (scene_phase_coverage_score(scene), 0.24),
        (zoom_choreography_score(scene), 0.24),
        (highlight_continuity_score(scene), 0.18),
        (result_readability_score(scene), 0.18),
        (cursor_commitment_score(scene), 0.16),
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


def weighted_average(*pairs: tuple[float, float]) -> float:
    total_weight = sum(weight for _value, weight in pairs)
    if total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in pairs) / total_weight
