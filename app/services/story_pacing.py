from __future__ import annotations

from typing import Any

TARGET_SCENE_COUNT = 5
TARGET_CONTENT_DURATION_SECONDS = 30.0
MIN_SCENE_DURATION_SECONDS = 4.2
MAX_SCENE_DURATION_SECONDS = 7.8
PRIORITY_TERMS = (
    "login",
    "sign up",
    "signup",
    "course",
    "lesson",
    "dashboard",
    "score",
    "feedback",
    "pronunciation",
    "practice",
    "result",
    "session",
    "start",
    "continue",
)


def shape_launch_story(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not scenes:
        return []
    selected = select_key_scenes(scenes)
    durations = rebalance_scene_durations(selected)
    return [
        {
            **scene,
            "scene_number": index,
            "estimated_duration_seconds": duration,
        }
        for index, (scene, duration) in enumerate(zip(selected, durations, strict=True), start=1)
    ]


def select_key_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(scenes) <= TARGET_SCENE_COUNT:
        return scenes
    indexed = list(enumerate(scenes))
    first = indexed[0]
    last = indexed[-1]
    middle = indexed[1:-1]
    ranked_middle = sorted(middle, key=lambda item: scene_priority(item[1], item[0]), reverse=True)
    keep_count = max(0, TARGET_SCENE_COUNT - 2)
    kept_indexes = {first[0], last[0], *(index for index, _ in ranked_middle[:keep_count])}
    return [scene for index, scene in indexed if index in kept_indexes]


def rebalance_scene_durations(scenes: list[dict[str, Any]]) -> list[float]:
    desired_total = max(TARGET_CONTENT_DURATION_SECONDS, len(scenes) * MIN_SCENE_DURATION_SECONDS)
    weights = [scene_duration_weight(scene, index) for index, scene in enumerate(scenes)]
    total_weight = sum(weights) or 1.0
    durations = [
        clamp_duration(round(desired_total * (weight / total_weight), 2))
        for weight in weights
    ]
    return rebalance_rounding(durations, desired_total)


def scene_priority(scene: dict[str, Any], index: int) -> float:
    text = joined_scene_text(scene)
    keyword_bonus = sum(1.0 for term in PRIORITY_TERMS if term in text)
    early_bonus = max(0.0, 1.6 - index * 0.18)
    action_bonus = 0.8 if any(word in text for word in ("click", "open", "choose", "select", "start", "continue")) else 0.0
    generic_penalty = 0.6 if any(word in text for word in ("welcome", "overview", "intro")) else 0.0
    return keyword_bonus + early_bonus + action_bonus - generic_penalty


def scene_duration_weight(scene: dict[str, Any], index: int) -> float:
    base = float(scene.get("estimated_duration_seconds") or 0) or 5.0
    priority = scene_priority(scene, index)
    return max(1.0, min(2.8, base / 4.8 + priority * 0.22))


def rebalance_rounding(durations: list[float], desired_total: float) -> list[float]:
    rounded = durations[:]
    delta = round(desired_total - sum(rounded), 2)
    step = 0.2
    while abs(delta) >= step:
        direction = step if delta > 0 else -step
        index = best_adjustment_index(rounded, direction)
        if index is None:
            break
        rounded[index] = round(rounded[index] + direction, 2)
        delta = round(desired_total - sum(rounded), 2)
    return rounded


def best_adjustment_index(durations: list[float], direction: float) -> int | None:
    for index, duration in sorted(enumerate(durations), key=lambda item: item[1], reverse=direction > 0):
        next_value = duration + direction
        if MIN_SCENE_DURATION_SECONDS <= next_value <= MAX_SCENE_DURATION_SECONDS:
            return index
    return None


def clamp_duration(value: float) -> float:
    return round(max(MIN_SCENE_DURATION_SECONDS, min(MAX_SCENE_DURATION_SECONDS, value)), 2)


def joined_scene_text(scene: dict[str, Any]) -> str:
    return " ".join(
        str(scene.get(key, "")).strip().lower()
        for key in ("purpose", "spoken_line", "on_screen_text", "source_excerpt")
    )
