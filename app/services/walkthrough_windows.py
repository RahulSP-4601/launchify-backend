from __future__ import annotations

from app.models.projects import EditPlanScene

RESULT_WORDS = frozenset({"see", "view", "available", "logged", "shown", "displayed", "appears", "will", "now"})
EXPLANATION_WORDS = frozenset({"because", "here", "notice", "right", "you", "this", "where"})


def action_result_window(
    start: float,
    end: float,
    action_time: float | None,
    spoken_line: str,
) -> tuple[float, float, float]:
    duration = max(end - start, 0.8)
    anchor = min(max(action_time if action_time is not None else start + duration * 0.4, start), end)
    pre_roll = min(1.1, max(0.6, duration * 0.28))
    focus_start = max(start, anchor - pre_roll)
    result_hold = result_hold_seconds(spoken_line, duration)
    focus_peak_end = min(end, anchor + min(1.2, duration * 0.28 + 0.45))
    settle_end = min(end, max(focus_peak_end + result_hold, focus_peak_end))
    return round(focus_start, 2), round(focus_peak_end, 2), round(settle_end, 2)


def step_clip_window(scene: EditPlanScene) -> tuple[float, float]:
    duration = max(scene.end - scene.start, 0.8)
    if scene.action_timestamp is None:
        return scene.start, scene.end
    clip_start = max(scene.start, scene.action_timestamp - pre_action_seconds(scene))
    clip_end = min(
        scene.end,
        max(
            scene.action_timestamp + result_hold_seconds(scene.spoken_line, duration) + explanation_hold_seconds(scene.spoken_line, scene.action_class),
            scene.action_timestamp + minimum_post_action_seconds(scene.action_class),
        ),
    )
    return round(clip_start, 2), round(clip_end, 2)


def result_hold_seconds(spoken_line: str, duration: float) -> float:
    words = spoken_line.lower().split()
    explanation_bonus = 0.34 if any(word.strip(".,") in RESULT_WORDS for word in words) else 0.0
    return min(1.8, max(0.9, duration * 0.28 + explanation_bonus))


def explanation_hold_seconds(spoken_line: str, action_class: str = "generic_action") -> float:
    words = [word.strip(".,") for word in spoken_line.lower().split()]
    if action_class in {"result_state", "explanatory_hold"}:
        return 0.55
    if any(word in EXPLANATION_WORDS for word in words):
        return 0.35
    return 0.0


def pre_action_seconds(scene: EditPlanScene) -> float:
    if scene.action_class in {"auth_action", "menu_open", "tab_switch"}:
        return min(1.35, max(0.9, (scene.end - scene.start) * 0.34))
    if scene.action_class == "card_selection":
        return min(1.25, max(0.85, (scene.end - scene.start) * 0.32))
    return min(1.15, max(0.75, (scene.end - scene.start) * 0.3))


def minimum_post_action_seconds(action_class: str) -> float:
    if action_class in {"result_state", "explanatory_hold"}:
        return 2.0
    if action_class in {"auth_action", "navigation", "tab_switch"}:
        return 1.7
    return 1.45
