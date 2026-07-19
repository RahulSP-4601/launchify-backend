from __future__ import annotations

from app.models.projects import EditPlanScene

RESULT_WORDS = frozenset({"see", "view", "available", "logged", "shown", "displayed", "appears", "will", "now"})


def action_result_window(
    start: float,
    end: float,
    action_time: float | None,
    spoken_line: str,
) -> tuple[float, float, float]:
    duration = max(end - start, 0.8)
    anchor = min(max(action_time if action_time is not None else start + duration * 0.4, start), end)
    pre_roll = min(0.55, duration * 0.22)
    focus_start = max(start, anchor - pre_roll)
    result_hold = result_hold_seconds(spoken_line, duration)
    focus_peak_end = min(end, anchor + min(0.95, duration * 0.26 + 0.35))
    settle_end = min(end, max(focus_peak_end + result_hold, focus_peak_end))
    return round(focus_start, 2), round(focus_peak_end, 2), round(settle_end, 2)


def step_clip_window(scene: EditPlanScene) -> tuple[float, float]:
    duration = max(scene.end - scene.start, 0.8)
    if scene.action_timestamp is None:
        return scene.start, scene.end
    clip_start = max(scene.start, scene.action_timestamp - min(0.55, duration * 0.22))
    clip_end = min(scene.end, max(scene.action_timestamp + result_hold_seconds(scene.spoken_line, duration), scene.action_timestamp + 0.9))
    return round(clip_start, 2), round(clip_end, 2)


def result_hold_seconds(spoken_line: str, duration: float) -> float:
    words = spoken_line.lower().split()
    explanation_bonus = 0.26 if any(word.strip(".,") in RESULT_WORDS for word in words) else 0.0
    return min(1.2, max(0.52, duration * 0.22 + explanation_bonus))
