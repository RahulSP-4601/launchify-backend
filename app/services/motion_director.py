from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanZoom, FocusBox, LaunchScriptScene, TemplateConfigRecord
from app.services.editorial_motion import MotionPlan, motion_plan
from app.services.visual_policy import ScenePolicy
from app.services.walkthrough_windows import action_result_window


def build_motion_track(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    policy: ScenePolicy,
    template_config: TemplateConfigRecord | None,
) -> tuple[list[EditPlanZoom], list[EditPlanHighlight]]:
    plan = motion_plan(scene, policy, max(end - start, 0.8))
    return (
        build_zooms(start, end, policy, plan, template_config),
        build_highlights(scene, start, end, policy, plan, template_config),
    )


def build_zooms(
    start: float,
    end: float,
    policy: ScenePolicy,
    plan: MotionPlan,
    template_config: TemplateConfigRecord | None,
) -> list[EditPlanZoom]:
    return planned_zooms(start, end, policy, plan)


def planned_zooms(start: float, end: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    if plan.strength == "none":
        return []
    if should_prefer_static_scene(policy):
        return []
    if should_hold_static(policy, start, end):
        return []
    if plan.intent in {"result-focus", "reading-focus"}:
        return result_zooms(start, end, policy, plan)
    return action_led_zooms(start, end, policy, plan)


def result_zooms(start: float, end: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    focus_start = round(start + duration * 0.14, 2)
    focus_end = min(end, round(focus_start + duration * 0.44, 2))
    return [zoom_record(focus_start, focus_end, policy, plan, 1.0, "ease-out", 0.97, 0.88)]


def action_led_zooms(start: float, end: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    if should_use_multi_step_zoom(policy, duration, plan):
        return multi_step_action_zoom(start, end, duration, policy, plan)
    focus_start, focus_peak_end, settle_end = action_result_window(
        start,
        end,
        None,
        policy.target_label,
        scene_role=policy.scene_role,
        action_class=policy.action_class,
    )
    zooms: list[EditPlanZoom] = []
    focus_start = round(max(focus_start, start + 0.18), 2)
    if focus_peak_end - focus_start >= 0.55:
        zooms.append(zoom_record(focus_start, focus_peak_end, policy, plan, 1.0, "ease-in-out", 0.94, 0.76))
    if settle_end - focus_peak_end >= 0.32:
        zooms.append(zoom_record(focus_peak_end, settle_end, policy, plan, 0.98, "ease-out", 0.96, 0.86))
    if zooms:
        return zooms
    focus_end = min(end, round(max(focus_start + 0.6, end - 0.18), 2))
    return [zoom_record(focus_start, focus_end, policy, plan, 1.0, "ease-in-out", 0.95, 0.78)]


def multi_step_action_zoom(start: float, end: float, duration: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    if plan.intent == "selection-focus":
        return two_step_zoom(start, end, duration, policy, plan)
    if duration >= 7.5 and focus_area(policy) < 0.08 and plan.strength == "medium":
        return three_step_zoom(start, end, duration, policy, plan)
    return two_step_zoom(start, end, duration, policy, plan)


def two_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    first_start = round(start + duration * 0.06, 2)
    second_start = round(start + duration * 0.42, 2)
    return [
        zoom_record(first_start, second_start, policy, plan, 0.98, "ease-out", 0.94, 0.48),
        zoom_record(second_start, min(end, second_start + duration * 0.4), policy, plan, 1.01, "ease-in-out", 0.92, 0.82),
    ]


def three_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy, plan: MotionPlan) -> list[EditPlanZoom]:
    first = round(start + duration * 0.06, 2)
    second = round(start + duration * 0.3, 2)
    third = round(start + duration * 0.58, 2)
    return [
        zoom_record(first, second, policy, plan, 0.98, "ease-out", 0.95, 0.42),
        zoom_record(second, third, policy, plan, 1.0, "ease-in-out", 0.93, 0.62),
        zoom_record(third, min(end, third + duration * 0.26), policy, plan, 1.02, "ease-in", 0.91, 0.82),
    ]


def zoom_record(
    start: float,
    end: float,
    policy: ScenePolicy,
    plan: MotionPlan,
    multiplier: float,
    easing: str,
    smoothing: float,
    hold_ratio: float,
) -> EditPlanZoom:
    base_scale = plan.zoom_scale
    scale = capped_zoom_scale(base_scale * multiplier, policy)
    return EditPlanZoom(
        start=round(start, 2),
        end=round(end, 2),
        scale=scale,
        focus_region=policy.focus_region,
        reason=f"Editorial {plan.intent} move around the strongest grounded UI region.",
        confidence=policy.zoom_confidence,
        focus_box=plan.focus_box,
        easing=easing,
        x_offset=plan.drift_x,
        y_offset=plan.drift_y,
        smoothing=smoothing,
        hold_ratio=hold_ratio,
    )


def build_highlights(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    policy: ScenePolicy,
    plan: MotionPlan,
    template_config: TemplateConfigRecord | None,
) -> list[EditPlanHighlight]:
    if not plan.should_highlight or should_prefer_static_scene(policy):
        return []
    focus_box = plan.focus_box
    if should_skip_highlight(policy, focus_box):
        return []
    highlight_start, highlight_end = highlight_window(start, end, policy)
    return [
        EditPlanHighlight(
            start=highlight_start,
            end=highlight_end,
            label=highlight_label(policy, scene),
            style=plan.highlight_style,
            anchor_region=policy.anchor_region,
            confidence=policy.highlight_confidence,
            focus_box=focus_box,
            placement_preference="avoid-ui-cover" if motion_profile(template_config) != "calm" else "static-edge",
            ui_label=policy.target_label,
        )
    ]


def highlight_window(start: float, end: float, policy: ScenePolicy) -> tuple[float, float]:
    highlight_start, highlight_end, _settle_end = action_result_window(
        start,
        end,
        None,
        policy.target_label,
        scene_role=policy.scene_role,
        action_class=policy.action_class,
    )
    tightened_start = max(start, highlight_start - 0.1)
    tightened_end = min(end, max(tightened_start + 0.78, highlight_end + 0.22))
    if policy.action_class in {"auth_action", "card_selection"}:
        tightened_end = min(end, max(tightened_end, tightened_start + 1.16))
    if policy.action_class in {"navigation", "tab_switch"}:
        tightened_end = min(end, max(tightened_end, tightened_start + 0.96))
    if policy.scene_role != "action":
        tightened_end = min(tightened_end, tightened_start + 0.82)
    return round(tightened_start, 2), round(tightened_end, 2)


def motion_profile(template_config: TemplateConfigRecord | None) -> str:
    return template_config.motion_profile if template_config is not None else "balanced"


def offset_for_region(region: str, axis: str) -> float:
    if axis == "x":
        return -0.05 if region == "top-left" else 0.05 if region in {"top-right", "bottom-right"} else 0.0
    return -0.05 if region.startswith("top") else 0.05 if region == "bottom-right" else 0.0


def offset_for_box(box: FocusBox | None, region: str, axis: str) -> float:
    if box is None:
        return offset_for_region(region, axis)
    center = box.x + box.width / 2 if axis == "x" else box.y + box.height / 2
    drift = (center - 0.5) * 0.12
    return float(round(drift if abs(drift) > 0.015 else 0.0, 3))


def capped_zoom_scale(scale: float, policy: ScenePolicy) -> float:
    confidence_modifier = max(0.0, min(policy.zoom_confidence, 1.0))
    limit = 1.08 + confidence_modifier * 0.06
    return round(min(scale, limit), 2)


def highlight_label(policy: ScenePolicy, scene: LaunchScriptScene) -> str:
    source = policy.target_label or scene.specific_target_label.strip() or scene.on_screen_text.strip() or scene.purpose.strip()
    words = source.split()
    compact = " ".join(words[:6]).strip()
    return compact[:56]


def should_hold_static(policy: ScenePolicy, start: float, end: float) -> bool:
    if not policy.should_zoom:
        return True
    duration = max(end - start, 0.0)
    if duration < 1.15:
        return True
    if policy.zoom_confidence < 0.56:
        return True
    box = policy.anchor_box or policy.focus_box
    if box is None:
        return policy.focus_region == "center"
    area = focus_area(policy)
    centered = abs((box.x + box.width / 2) - 0.5) < 0.08 and abs((box.y + box.height / 2) - 0.5) < 0.08
    return area > 0.22 and centered


def should_prefer_static_scene(policy: ScenePolicy) -> bool:
    return False


def should_skip_highlight(policy: ScenePolicy, focus_box: FocusBox | None) -> bool:
    if focus_box is None:
        return policy.scene_role != "action"
    area = focus_box.width * focus_box.height
    if area > 0.2:
        return True
    return policy.scene_role == "result" and area > 0.1


def should_use_multi_step_zoom(policy: ScenePolicy, duration: float, plan: MotionPlan) -> bool:
    if policy.scene_role != "action":
        return False
    if duration < 2.45:
        return False
    if plan.strength == "none":
        return False
    area = focus_area(policy)
    if area > 0.24:
        return False
    if policy.zoom_confidence < 0.7:
        return False
    return policy.action_class in {"auth_action", "button_click", "card_selection", "navigation", "tab_switch", "focus"}


def focus_area(policy: ScenePolicy) -> float:
    box = policy.anchor_box or policy.focus_box
    if box is None:
        return 0.18
    return box.width * box.height
