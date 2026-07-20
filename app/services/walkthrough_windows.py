from __future__ import annotations

from app.models.projects import EditPlanScene

RESULT_WORDS = frozenset({"see", "view", "available", "logged", "shown", "displayed", "appears", "will", "now"})
EXPLANATION_WORDS = frozenset({"because", "here", "notice", "right", "you", "this", "where"})
MAX_GUIDED_CLIP_SECONDS = 8.4


def action_result_window(
    start: float,
    end: float,
    action_time: float | None,
    spoken_line: str,
    *,
    scene_role: str = "action",
    action_class: str = "generic_action",
) -> tuple[float, float, float]:
    duration = max(end - start, 0.8)
    anchor = min(max(action_time if action_time is not None else start + duration * 0.4, start), end)
    pre_roll = scene_pre_roll(duration, scene_role, action_class)
    focus_start = max(start, anchor - pre_roll)
    result_hold = result_hold_seconds(spoken_line, duration, scene_role)
    focus_peak_end = min(end, anchor + focus_peak_seconds(duration, scene_role))
    settle_end = min(end, max(focus_peak_end + result_hold, focus_peak_end))
    return round(focus_start, 2), round(focus_peak_end, 2), round(settle_end, 2)


def step_clip_window(scene: EditPlanScene) -> tuple[float, float]:
    duration = max(scene.end - scene.start, 0.8)
    if scene.action_timestamp is None:
        return bounded_scene_window(scene.start, scene.end)
    hold_extension = narration_density_hold(scene)
    clip_start = max(scene.start, scene.action_timestamp - pre_action_seconds(scene))
    clip_end = min(
        scene.end,
        max(
            scene.action_timestamp
            + result_hold_seconds(scene.spoken_line, duration, scene.scene_role)
            + explanation_hold_seconds(scene.spoken_line, scene.action_class)
            + hold_extension,
            scene.action_timestamp + minimum_post_action_seconds(scene.action_class) + hold_extension,
        ),
    )
    return bounded_action_window(scene.start, scene.end, clip_start, clip_end, scene.action_timestamp)


def result_hold_seconds(spoken_line: str, duration: float, scene_role: str = "action") -> float:
    words = spoken_line.lower().split()
    explanation_bonus = 0.34 if any(word.strip(".,") in RESULT_WORDS for word in words) else 0.0
    if scene_role == "result":
        return min(2.8, max(1.45, duration * 0.38 + explanation_bonus))
    return min(2.15, max(1.05, duration * 0.31 + explanation_bonus))


def explanation_hold_seconds(spoken_line: str, action_class: str = "generic_action") -> float:
    words = [word.strip(".,") for word in spoken_line.lower().split()]
    if action_class in {"result_state", "explanatory_hold"}:
        return 0.55
    if any(word in EXPLANATION_WORDS for word in words):
        return 0.35
    return 0.0


def pre_action_seconds(scene: EditPlanScene) -> float:
    if scene.scene_role == "result":
        return min(0.75, max(0.45, (scene.end - scene.start) * 0.18))
    if scene.action_class in {"auth_action", "menu_open", "tab_switch"}:
        return min(1.35, max(0.9, (scene.end - scene.start) * 0.34))
    if scene.action_class == "card_selection":
        return min(1.25, max(0.85, (scene.end - scene.start) * 0.32))
    return min(1.15, max(0.75, (scene.end - scene.start) * 0.3))


def minimum_post_action_seconds(action_class: str) -> float:
    if action_class in {"result_state", "explanatory_hold"}:
        return 2.35
    if action_class in {"auth_action", "navigation", "tab_switch"}:
        return 2.05
    if action_class == "card_selection":
        return 1.9
    return 1.6


def narration_density_hold(scene: EditPlanScene) -> float:
    spoken_words = word_count(scene.spoken_line)
    source_words = word_count(scene.source_excerpt)
    if source_words <= spoken_words + 2:
        return 0.0
    density_gap = min((source_words - spoken_words) / 10.0, 1.0)
    base_extension = 0.4 + density_gap * 0.9
    if scene.action_class in {"result_state", "explanatory_hold", "card_selection"}:
        return round(min(base_extension + 0.45, 1.8), 2)
    return round(min(base_extension, 1.35), 2)


def scene_pre_roll(duration: float, scene_role: str, action_class: str) -> float:
    if scene_role == "result":
        return min(0.7, max(0.35, duration * 0.16))
    if action_class in {"auth_action", "navigation", "tab_switch"}:
        return min(1.25, max(0.7, duration * 0.3))
    return min(1.1, max(0.55, duration * 0.28))


def focus_peak_seconds(duration: float, scene_role: str) -> float:
    if scene_role == "result":
        return min(0.85, duration * 0.2 + 0.28)
    return min(1.2, duration * 0.28 + 0.45)


def word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip(".,")])


def bounded_scene_window(start: float, end: float) -> tuple[float, float]:
    if end - start <= MAX_GUIDED_CLIP_SECONDS:
        return round(start, 2), round(end, 2)
    return round(start, 2), round(start + MAX_GUIDED_CLIP_SECONDS, 2)


def bounded_action_window(
    scene_start: float,
    scene_end: float,
    clip_start: float,
    clip_end: float,
    action_time: float,
) -> tuple[float, float]:
    if clip_end - clip_start <= MAX_GUIDED_CLIP_SECONDS:
        return round(clip_start, 2), round(clip_end, 2)
    centered_start = max(scene_start, action_time - 1.5)
    centered_end = min(scene_end, max(action_time + 3.4, centered_start + MAX_GUIDED_CLIP_SECONDS))
    if centered_end - centered_start > MAX_GUIDED_CLIP_SECONDS:
        centered_end = centered_start + MAX_GUIDED_CLIP_SECONDS
    return round(centered_start, 2), round(centered_end, 2)
