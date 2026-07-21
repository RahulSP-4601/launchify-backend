from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanScene
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, RESULT_FAMILY, SELECTION_FAMILY


@dataclass(frozen=True)
class TrimSignals:
    state_change: float
    action_value: float
    outcome_value: float
    continuity_value: float
    readability_value: float
    redundancy_penalty: float

    @property
    def importance(self) -> float:
        score = self.state_change + self.action_value + self.outcome_value + self.continuity_value + self.readability_value - self.redundancy_penalty
        return round(max(score, 0.0), 2)


def trim_signals(scene: EditPlanScene, context: FlowSceneContext | None) -> TrimSignals:
    return TrimSignals(
        state_change=state_change_value(scene, context),
        action_value=action_value(scene),
        outcome_value=outcome_value(scene, context),
        continuity_value=continuity_value(scene, context),
        readability_value=readability_value(scene),
        redundancy_penalty=redundancy_penalty(scene, context),
    )


def state_change_value(scene: EditPlanScene, context: FlowSceneContext | None) -> float:
    combined = normalize(" ".join(part for part in (scene.on_screen_text, scene.purpose, scene.title) if part))
    score = 0.3
    if scene.scene_role == "result":
        score += 0.28
    if scene.result_anchor_timestamp is not None:
        score += 0.18
    if context is not None and context.family in {AUTH_FAMILY, SELECTION_FAMILY, CONFIG_FAMILY}:
        score += 0.14
    if any(token in combined for token in ("dashboard", "course", "level", "account", "google")):
        score += 0.12
    return min(score, 1.0)


def action_value(scene: EditPlanScene) -> float:
    if scene.action_timestamp is None:
        return 0.18
    if scene.action_class == "auth_action":
        return 0.86
    if scene.action_class == "card_selection":
        return 0.8
    if scene.action_class in {"button_click", "navigation", "tab_switch"}:
        return 0.72
    return 0.58


def outcome_value(scene: EditPlanScene, context: FlowSceneContext | None) -> float:
    anchor = scene.result_anchor_timestamp
    if anchor is None:
        return 0.22
    visible_gap = max(scene.end - anchor, 0.0)
    score = 0.45 + min(visible_gap / 4.0, 0.35)
    if context is not None and context.next_scene is not None:
        score += 0.08
    return min(score, 1.0)


def continuity_value(scene: EditPlanScene, context: FlowSceneContext | None) -> float:
    if context is None:
        return 0.18
    score = 0.18
    if context.next_scene is not None:
        gap = max(context.next_scene.start - scene.end, 0.0)
        score += 0.26 if gap <= 1.0 else 0.18 if gap <= 4.0 else 0.08
    if context.family == AUTH_FAMILY:
        score += 0.18
    if context.family == SELECTION_FAMILY and context.next_scene is not None:
        score += 0.16
    if context.family == CONFIG_FAMILY:
        score += 0.08
    return min(score, 1.0)


def readability_value(scene: EditPlanScene) -> float:
    dense_words = word_count(scene.on_screen_text) + word_count(scene.purpose)
    score = min(dense_words / 20.0, 0.45)
    score += min(max(scene.readable_hold_seconds, 0.0) / 3.0, 0.35)
    if scene.layout_mode in {"split-right", "feature-center"}:
        score += 0.08
    return min(score, 1.0)


def redundancy_penalty(scene: EditPlanScene, context: FlowSceneContext | None) -> float:
    if context is None or context.previous_scene is None:
        return 0.0
    previous = context.previous_scene
    penalty = 0.0
    if normalize(previous.spoken_line) == normalize(scene.spoken_line):
        penalty += 0.42
    if normalize(previous.on_screen_text) == normalize(scene.on_screen_text):
        penalty += 0.22
    if context.family == context_for(previous, context):
        penalty += 0.08
    return min(penalty, 0.65)


def context_for(previous: EditPlanScene, context: FlowSceneContext) -> str:
    return context.family if previous.scene_role == "action" else RESULT_FAMILY


def suggested_pre_roll(scene: EditPlanScene, signals: TrimSignals) -> float:
    base = 0.22 + signals.action_value * 0.44 + signals.continuity_value * 0.26
    if scene.action_class == "auth_action":
        base += 0.12
    if scene.action_class == "card_selection":
        base += 0.08
    return round(min(max(base, 0.18), 1.35), 2)


def suggested_post_hold(scene: EditPlanScene, signals: TrimSignals) -> float:
    base = 0.55 + signals.outcome_value * 1.4 + signals.readability_value * 1.15 + signals.continuity_value * 0.75
    if scene.scene_role == "result":
        base += 0.25
    return round(min(max(base, 1.1), 4.8), 2)


def suggested_bridge(scene: EditPlanScene, signals: TrimSignals) -> float:
    base = 0.18 + signals.continuity_value * 0.95 - signals.redundancy_penalty * 0.45
    if scene.action_class == "auth_action":
        base += 0.18
    if scene.action_class == "card_selection":
        base += 0.22
    return round(min(max(base, 0.18), 2.4), 2)


def suggested_max_duration(scene: EditPlanScene, signals: TrimSignals) -> float:
    base = 3.2 + signals.importance * 3.1 + signals.readability_value * 1.1
    if scene.action_class == "auth_action":
        base += 1.4
    if scene.action_class == "card_selection":
        base += 1.8
    if scene.scene_role == "result":
        base += 0.4
    return round(min(max(base, 4.2), 13.2), 2)


def normalize(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")


def word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip(".,")])
