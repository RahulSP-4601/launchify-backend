from __future__ import annotations

from app.models.projects import EditPlanScene
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, RESULT_FAMILY, SELECTION_FAMILY
from app.services.editorial_trim_signals import TrimSignals, suggested_bridge, suggested_max_duration, suggested_post_hold, suggested_pre_roll, trim_signals

RESULT_WORDS = frozenset({"see", "view", "available", "logged", "shown", "displayed", "appears", "will", "now"})
EXPLANATION_WORDS = frozenset({"because", "here", "notice", "right", "you", "this", "where"})
MAX_GUIDED_CLIP_SECONDS = 11.6


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
    anchor_time = scene.result_anchor_timestamp or scene.action_timestamp
    hold_extension = narration_density_hold(scene) + max(scene.readable_hold_seconds - 1.0, 0.0)
    clip_start = max(scene.start, scene.action_timestamp - pre_action_seconds(scene))
    clip_end = min(
        scene.end,
        max(
            anchor_time
            + result_hold_seconds(scene.spoken_line, duration, scene.scene_role)
            + explanation_hold_seconds(scene.spoken_line, scene.action_class)
            + hold_extension,
            scene.action_timestamp + minimum_post_action_seconds(scene.action_class) + hold_extension,
        ),
    )
    return bounded_action_window(scene.start, scene.end, clip_start, clip_end, scene.action_timestamp)


def step_clip_window_with_context(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
) -> tuple[float, float]:
    clip_start, clip_end = step_clip_window(scene)
    if context is None:
        return clip_start, clip_end
    action_time = scene.action_timestamp or scene.start
    signals = trim_signals(scene, context)
    scene_window_end = expanded_scene_end(scene, context, signals)
    clip_start = max(scene.start, action_time - suggested_pre_roll(scene, signals))
    clip_end = min(
        scene_window_end,
        max(
            clip_end,
            action_time + suggested_post_hold(scene, signals),
            contextual_post_end(scene, context, action_time),
        ),
    )
    if context.next_scene is not None and should_preserve_handoff(scene, context):
        bridge_gap = max(handoff_gap(scene, context), suggested_bridge(scene, signals))
        clip_end = min(scene_window_end, max(clip_end, context.next_scene.start - bridge_gap))
    if context.previous_scene is not None and context.family == AUTH_FAMILY and not context.is_first:
        clip_start = max(scene.start, min(clip_start, action_time - 0.95))
    return bounded_action_window(scene.start, scene_window_end, clip_start, clip_end, action_time, suggested_max_duration(scene, signals))


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
    max_duration: float = MAX_GUIDED_CLIP_SECONDS,
) -> tuple[float, float]:
    if clip_end - clip_start <= max_duration:
        return round(clip_start, 2), round(clip_end, 2)
    centered_start = max(scene_start, action_time - 1.5)
    centered_end = min(scene_end, max(action_time + 5.2, centered_start + max_duration))
    if centered_end - centered_start > max_duration:
        centered_end = centered_start + max_duration
    return round(centered_start, 2), round(centered_end, 2)


def contextual_pre_roll(scene: EditPlanScene, context: FlowSceneContext) -> float:
    if context.family == AUTH_FAMILY:
        return 0.45 if context.is_first else 0.3
    if context.family == SELECTION_FAMILY:
        return 0.35
    if context.family == CONFIG_FAMILY:
        return 0.22
    return 0.18


def contextual_post_end(scene: EditPlanScene, context: FlowSceneContext, action_time: float) -> float:
    anchor_time = scene.result_anchor_timestamp or action_time
    hold = result_hold_seconds(scene.spoken_line, max(scene.end - scene.start, 0.8), scene.scene_role)
    if context.family == AUTH_FAMILY:
        return anchor_time + hold + (1.1 if context.is_first else 1.45)
    if context.family == SELECTION_FAMILY:
        return anchor_time + hold + (1.55 if not context.is_last else 1.2)
    if context.family == CONFIG_FAMILY:
        return anchor_time + hold + 1.0
    if context.family == RESULT_FAMILY:
        return anchor_time + hold + 0.85
    return anchor_time + hold + 0.55


def expanded_scene_end(scene: EditPlanScene, context: FlowSceneContext, signals: TrimSignals) -> float:
    if context.next_scene is None:
        return scene.end
    gap = max(context.next_scene.start - scene.end, 0.0)
    if gap <= 0.0:
        return scene.end
    if context.family == AUTH_FAMILY:
        extension = min(gap - handoff_gap(scene, context), suggested_max_duration(scene, signals) - max(scene.end - scene.start, 0.8))
        return max(scene.end, scene.end + max(extension, 0.0))
    if context.family == SELECTION_FAMILY:
        extension = min(gap - handoff_gap(scene, context), suggested_max_duration(scene, signals) - max(scene.end - scene.start, 0.8))
        return max(scene.end, scene.end + max(extension, 0.0))
    if context.family == RESULT_FAMILY:
        extension = min(gap - handoff_gap(scene, context), suggested_max_duration(scene, signals) - max(scene.end - scene.start, 0.8))
        return max(scene.end, scene.end + max(extension, 0.0))
    return scene.end


def should_preserve_handoff(scene: EditPlanScene, context: FlowSceneContext) -> bool:
    if context.next_scene is None:
        return False
    if context.family == AUTH_FAMILY:
        return True
    if context.family == SELECTION_FAMILY and context.next_scene.start - scene.end <= 8.8:
        return True
    return scene.scene_role == "action" and context.next_scene.scene_role == "result"


def handoff_gap(scene: EditPlanScene, context: FlowSceneContext) -> float:
    if context.family == AUTH_FAMILY:
        return 0.36
    if context.family == SELECTION_FAMILY:
        return 0.42
    if context.family == CONFIG_FAMILY:
        return 0.3
    return 0.24
