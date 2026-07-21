from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.projects import FocusBox, LaunchScriptScene
from app.services.visual_policy import ScenePolicy

MotionIntent = Literal["static", "action-focus", "selection-focus", "result-focus", "reading-focus"]
MotionStrength = Literal["none", "low", "medium"]


@dataclass(frozen=True)
class MotionPlan:
    intent: MotionIntent
    strength: MotionStrength
    focus_box: FocusBox | None
    zoom_scale: float
    drift_x: float
    drift_y: float
    highlight_style: str
    should_highlight: bool


def motion_plan(scene: LaunchScriptScene, policy: ScenePolicy, duration: float) -> MotionPlan:
    intent = motion_intent(scene, policy)
    focus_box = context_focus_box(primary_focus_box(policy), intent)
    strength = motion_strength(policy, intent, focus_box, duration)
    scale = zoom_scale(policy, intent, strength, focus_box)
    drift_x, drift_y = motion_drift(focus_box, strength)
    highlight = should_highlight(policy, intent, strength, focus_box)
    return MotionPlan(
        intent=intent,
        strength=strength,
        focus_box=focus_box,
        zoom_scale=scale,
        drift_x=drift_x,
        drift_y=drift_y,
        highlight_style=highlight_style(intent, focus_box),
        should_highlight=highlight,
    )


def motion_intent(scene: LaunchScriptScene, policy: ScenePolicy) -> MotionIntent:
    if policy.scene_role == "result":
        return "result-focus"
    if policy.action_class == "card_selection":
        return "selection-focus"
    if setup_like(scene):
        return "reading-focus"
    if policy.action_class in {"auth_action", "button_click", "navigation", "tab_switch"}:
        return "action-focus"
    if policy.should_zoom:
        return "action-focus"
    return "static"


def primary_focus_box(policy: ScenePolicy) -> FocusBox | None:
    return policy.anchor_box or policy.click_target_box or policy.focus_box or policy.cursor_box


def context_focus_box(box: FocusBox | None, intent: MotionIntent) -> FocusBox | None:
    if box is None:
        return None
    if intent == "selection-focus":
        return expanded_box(box, width_factor=1.8, height_factor=1.45, min_width=0.18, min_height=0.14)
    if intent == "reading-focus":
        return expanded_box(box, width_factor=1.65, height_factor=1.3, min_width=0.22, min_height=0.16)
    if intent == "result-focus":
        return expanded_box(box, width_factor=1.55, height_factor=1.25, min_width=0.18, min_height=0.14)
    return expanded_box(box, width_factor=1.35, height_factor=1.18, min_width=0.12, min_height=0.09)


def motion_strength(
    policy: ScenePolicy,
    intent: MotionIntent,
    focus_box: FocusBox | None,
    duration: float,
) -> MotionStrength:
    if intent == "static" or duration < 1.25 or policy.zoom_confidence < 0.58:
        return "none"
    if focus_box is None:
        return "low" if policy.should_zoom and policy.zoom_confidence >= 0.7 else "none"
    area = focus_box.width * focus_box.height
    centered = center_delta(focus_box) < 0.09
    if intent in {"reading-focus", "result-focus"}:
        return "low" if area < 0.2 and not centered else "none"
    if area > 0.22 and centered:
        return "none"
    if area < 0.08 and policy.zoom_confidence >= 0.74:
        return "medium"
    if area < 0.16:
        return "low"
    return "none"


def zoom_scale(
    policy: ScenePolicy,
    intent: MotionIntent,
    strength: MotionStrength,
    focus_box: FocusBox | None,
) -> float:
    if strength == "none":
        return 1.0
    area = focus_box.width * focus_box.height if focus_box is not None else 0.16
    if intent == "selection-focus":
        return 1.03 if strength == "medium" else 1.02
    if intent in {"reading-focus", "result-focus"}:
        return 1.03 if strength == "low" else 1.04
    if area < 0.04:
        return 1.12 if strength == "medium" else 1.08
    if area < 0.1:
        return 1.08 if strength == "medium" else 1.05
    return 1.04


def motion_drift(focus_box: FocusBox | None, strength: MotionStrength) -> tuple[float, float]:
    if focus_box is None or strength == "none":
        return 0.0, 0.0
    scale = 0.05 if strength == "medium" else 0.03
    center_x = focus_box.x + focus_box.width / 2
    center_y = focus_box.y + focus_box.height / 2
    drift_x = round(clamp((center_x - 0.5) * scale, -0.028, 0.028), 3)
    drift_y = round(clamp((center_y - 0.5) * scale, -0.022, 0.022), 3)
    return (0.0 if abs(drift_x) < 0.01 else drift_x, 0.0 if abs(drift_y) < 0.01 else drift_y)


def should_highlight(
    policy: ScenePolicy,
    intent: MotionIntent,
    strength: MotionStrength,
    focus_box: FocusBox | None,
) -> bool:
    if strength == "none":
        return False
    if not policy.should_highlight or focus_box is None or policy.highlight_confidence < 0.58:
        return False
    area = focus_box.width * focus_box.height
    if intent in {"reading-focus", "result-focus"}:
        return area < 0.12 and strength != "none"
    if strength == "medium":
        return area < 0.12
    return area < 0.08 and center_delta(focus_box) > 0.08


def highlight_style(intent: MotionIntent, focus_box: FocusBox | None) -> str:
    area = focus_box.width * focus_box.height if focus_box is not None else 0.2
    if intent == "selection-focus":
        return "ambient-lift"
    if intent in {"reading-focus", "result-focus"}:
        return "ambient"
    if area < 0.03:
        return "soft-glow"
    if area < 0.08:
        return "ambient-lift"
    return "ambient"


def setup_like(scene: LaunchScriptScene) -> bool:
    combined = " ".join(part.lower() for part in (scene.purpose, scene.on_screen_text, scene.spoken_line) if part)
    return any(token in combined for token in ("level", "settings", "preferences", "setup", "starting point", "before the lesson"))


def expanded_box(
    box: FocusBox,
    *,
    width_factor: float,
    height_factor: float,
    min_width: float,
    min_height: float,
) -> FocusBox:
    width = clamp(max(box.width * width_factor, min_width), 0.1, 0.9)
    height = clamp(max(box.height * height_factor, min_height), 0.08, 0.75)
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    return FocusBox(
        x=round(clamp(center_x - width / 2, 0.0, 1.0 - width), 4),
        y=round(clamp(center_y - height / 2, 0.0, 1.0 - height), 4),
        width=round(width, 4),
        height=round(height, 4),
    )


def center_delta(box: FocusBox) -> float:
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    return abs(center_x - 0.5) + abs(center_y - 0.5)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
