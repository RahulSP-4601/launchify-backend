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
    if should_hold_static(policy, start, end):
        return []
    if policy.scene_role == "result":
        return result_zooms(start, end, policy)
    duration = max(end - start, 0.5)
    if duration >= 5.2:
        return three_step_zoom(start, end, duration, policy)
    if duration >= 3.2:
        return two_step_zoom(start, end, duration, policy)
    focus_start = round(start + duration * 0.1, 2)
    focus_end = min(end, round(focus_start + duration * 0.68, 2))
    return [zoom_record(focus_start, focus_end, policy, 1.05, "ease-in-out", 0.88, 0.58)]


def result_zooms(start: float, end: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    focus_start = round(start + duration * 0.12, 2)
    focus_end = min(end, round(focus_start + duration * 0.56, 2))
    return [zoom_record(focus_start, focus_end, policy, 1.01, "ease-out", 0.92, 0.78)]


def two_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first_start = round(start + duration * 0.08, 2)
    second_start = round(start + duration * 0.46, 2)
    return [
        zoom_record(first_start, second_start, policy, 0.99, "ease-out", 0.9, 0.42),
        zoom_record(second_start, min(end, second_start + duration * 0.36), policy, 1.07, "ease-in-out", 0.86, 0.62),
    ]


def three_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first = round(start + duration * 0.08, 2)
    second = round(start + duration * 0.34, 2)
    third = round(start + duration * 0.62, 2)
    return [
        zoom_record(first, second, policy, 0.99, "ease-out", 0.92, 0.36),
        zoom_record(second, third, policy, 1.05, "ease-in-out", 0.9, 0.56),
        zoom_record(third, min(end, third + duration * 0.22), policy, 1.1, "ease-in", 0.88, 0.66),
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
            style=highlight_style(policy, focus_box),
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
    return round(max(start, highlight_start - 0.08), 2), round(min(end, highlight_end + 0.12), 2)


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
    limit = 1.18 + confidence_modifier * 0.12
    return round(min(scale, limit), 2)


def highlight_label(policy: ScenePolicy, scene: LaunchScriptScene) -> str:
    source = policy.target_label or scene.on_screen_text.strip() or scene.purpose.strip()
    words = source.split()
    compact = " ".join(words[:6]).strip()
    return compact[:56]


def highlight_style(policy: ScenePolicy, focus_box: FocusBox | None) -> str:
    if focus_box is None:
        return "ambient"
    area = focus_box.width * focus_box.height
    if policy.scene_role == "result":
        return "ambient"
    if area < 0.05:
        return "soft-glow"
    if area < 0.12:
        return "ambient-lift"
    return "ambient"


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
    area = box.width * box.height
    centered = abs((box.x + box.width / 2) - 0.5) < 0.08 and abs((box.y + box.height / 2) - 0.5) < 0.08
    return area > 0.22 and centered
