from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanZoom, FocusBox, LaunchScriptScene, TemplateConfigRecord
from app.services.visual_policy import ScenePolicy
from app.services.walkthrough_windows import action_result_window


def build_motion_track(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    policy: ScenePolicy,
    template_config: TemplateConfigRecord | None,
) -> tuple[list[EditPlanZoom], list[EditPlanHighlight]]:
    return (
        build_zooms(start, end, policy, template_config),
        build_highlights(scene, start, end, policy, template_config),
    )


def build_zooms(
    start: float,
    end: float,
    policy: ScenePolicy,
    template_config: TemplateConfigRecord | None,
) -> list[EditPlanZoom]:
    if not policy.should_zoom:
        return []
    if policy.scene_role == "result":
        return result_zooms(start, end, policy)
    duration = max(end - start, 0.5)
    if duration >= 6:
        return three_step_zoom(start, end, duration, policy)
    if duration >= 3.8:
        return two_step_zoom(start, end, duration, policy)
    focus_start = round(start + duration * 0.08, 2)
    return [zoom_record(focus_start, min(end, focus_start + duration * 0.78), policy, 1.08, "ease-in-out", 0.82, 0.48)]


def result_zooms(start: float, end: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    focus_start = round(start + duration * 0.12, 2)
    focus_end = min(end, round(focus_start + duration * 0.62, 2))
    return [zoom_record(focus_start, focus_end, policy, 1.02, "ease-out", 0.9, 0.7)]


def two_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first_start = round(start + duration * 0.06, 2)
    second_start = round(start + duration * 0.44, 2)
    return [
        zoom_record(first_start, second_start, policy, 0.98, "ease-out", 0.84, 0.34),
        zoom_record(second_start, min(end, second_start + duration * 0.42), policy, 1.1, "ease-in-out", 0.8, 0.5),
    ]


def three_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first = round(start + duration * 0.05, 2)
    second = round(start + duration * 0.3, 2)
    third = round(start + duration * 0.58, 2)
    return [
        zoom_record(first, second, policy, 0.98, "ease-out", 0.88, 0.28),
        zoom_record(second, third, policy, 1.08, "ease-in-out", 0.84, 0.4),
        zoom_record(third, min(end, third + duration * 0.28), policy, 1.16, "ease-in", 0.8, 0.54),
    ]


def zoom_record(
    start: float,
    end: float,
    policy: ScenePolicy,
    multiplier: float,
    easing: str,
    smoothing: float,
    hold_ratio: float,
) -> EditPlanZoom:
    base_scale = zoom_base_scale(policy)
    scale = capped_zoom_scale(base_scale * multiplier, policy)
    return EditPlanZoom(
        start=round(start, 2),
        end=round(end, 2),
        scale=scale,
        focus_region=policy.focus_region,
        reason="Confidence-approved focus move around the strongest UI action.",
        confidence=policy.zoom_confidence,
        focus_box=policy.anchor_box or policy.focus_box,
        easing=easing,
        x_offset=offset_for_box(policy.anchor_box, policy.focus_region, axis="x"),
        y_offset=offset_for_box(policy.anchor_box, policy.focus_region, axis="y"),
        smoothing=smoothing,
        hold_ratio=hold_ratio,
    )


def build_highlights(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    policy: ScenePolicy,
    template_config: TemplateConfigRecord | None,
) -> list[EditPlanHighlight]:
    if not policy.should_highlight:
        return []
    focus_box = policy.anchor_box or policy.click_target_box or policy.cursor_box or policy.focus_box
    highlight_start, highlight_end = highlight_window(start, end, policy)
    return [
        EditPlanHighlight(
            start=highlight_start,
            end=highlight_end,
            label=highlight_label(policy, scene),
            style=policy.highlight_style,
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
        "",
        scene_role=policy.scene_role,
        action_class=policy.action_class,
    )
    return highlight_start, highlight_end


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
    return float(round((center - 0.5) * 0.18, 3))


def zoom_base_scale(policy: ScenePolicy) -> float:
    if policy.scene_role == "result":
        return 1.08 if policy.anchor_box is not None else 1.04
    if policy.anchor_box is not None:
        area = policy.anchor_box.width * policy.anchor_box.height
        if area < 0.04:
            return 1.24
        if area < 0.1:
            return 1.2
        return 1.14
    return 1.16 if policy.focus_region == "center" else 1.22


def capped_zoom_scale(scale: float, policy: ScenePolicy) -> float:
    confidence_modifier = max(0.0, min(policy.zoom_confidence, 1.0))
    limit = 1.22 + confidence_modifier * 0.14
    return round(min(scale, limit), 2)


def highlight_label(policy: ScenePolicy, scene: LaunchScriptScene) -> str:
    source = policy.target_label or scene.on_screen_text.strip() or scene.purpose.strip()
    words = source.split()
    compact = " ".join(words[:6]).strip()
    return compact[:56]
