from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanZoom, FocusBox, LaunchScriptScene, TemplateConfigRecord
from app.services.visual_policy import ScenePolicy


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
    duration = max(end - start, 0.5)
    if duration >= 6 and motion_profile(template_config) == "dynamic":
        return three_step_zoom(start, end, duration, policy)
    if duration >= 5 and motion_profile(template_config) == "dynamic":
        return two_step_zoom(start, end, duration, policy)
    midpoint = round(start + duration * 0.18, 2)
    return [zoom_record(midpoint, min(end, midpoint + duration * 0.62), policy, 1.0, "ease-in-out", 0.74, 0.26)]


def two_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first_start = round(start + duration * 0.12, 2)
    second_start = round(start + duration * 0.52, 2)
    return [
        zoom_record(first_start, second_start, policy, 0.94, "ease-out", 0.82, 0.22),
        zoom_record(second_start, min(end, second_start + duration * 0.34), policy, 1.04, "ease-in-out", 0.7, 0.28),
    ]


def three_step_zoom(start: float, end: float, duration: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    first = round(start + duration * 0.08, 2)
    second = round(start + duration * 0.38, 2)
    third = round(start + duration * 0.68, 2)
    return [
        zoom_record(first, second, policy, 0.92, "ease-out", 0.88, 0.18),
        zoom_record(second, third, policy, 1.0, "ease-in-out", 0.8, 0.24),
        zoom_record(third, min(end, third + duration * 0.18), policy, 1.06, "ease-in", 0.7, 0.3),
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
    return [
        EditPlanHighlight(
            start=round(start, 2),
            end=round(min(end, start + 1.35), 2),
            label=highlight_label(policy, scene),
            style=policy.highlight_style,
            anchor_region=policy.anchor_region,
            confidence=policy.highlight_confidence,
            focus_box=focus_box,
            placement_preference="avoid-ui-cover" if motion_profile(template_config) != "calm" else "static-edge",
            ui_label=policy.target_label,
        )
    ]


def motion_profile(template_config: TemplateConfigRecord | None) -> str:
    return template_config.motion_profile if template_config is not None else "balanced"


def offset_for_region(region: str, axis: str) -> float:
    if axis == "x":
        return -0.03 if region == "top-left" else 0.03 if region in {"top-right", "bottom-right"} else 0.0
    return -0.03 if region.startswith("top") else 0.03 if region == "bottom-right" else 0.0


def offset_for_box(box: FocusBox | None, region: str, axis: str) -> float:
    if box is None:
        return offset_for_region(region, axis)
    center = box.x + box.width / 2 if axis == "x" else box.y + box.height / 2
    return float(round((center - 0.5) * 0.12, 3))


def zoom_base_scale(policy: ScenePolicy) -> float:
    if policy.anchor_box is not None:
        area = policy.anchor_box.width * policy.anchor_box.height
        if area < 0.04:
            return 1.18
        if area < 0.1:
            return 1.14
        return 1.1
    return 1.12 if policy.focus_region == "center" else 1.16


def capped_zoom_scale(scale: float, policy: ScenePolicy) -> float:
    confidence_modifier = max(0.0, min(policy.zoom_confidence, 1.0))
    limit = 1.14 + confidence_modifier * 0.1
    return round(min(scale, limit), 2)


def highlight_label(policy: ScenePolicy, scene: LaunchScriptScene) -> str:
    source = policy.target_label or scene.on_screen_text.strip() or scene.purpose.strip()
    words = source.split()
    compact = " ".join(words[:6]).strip()
    return compact[:56]
