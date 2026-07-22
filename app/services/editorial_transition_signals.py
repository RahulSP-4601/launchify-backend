from __future__ import annotations

from app.models.projects import EditPlanScene

INTERMEDIATE_STATE_TOKENS = (
    "account",
    "chooser",
    "picker",
    "loading",
    "redirect",
    "continue",
    "existing",
    "dashboard",
    "workspace",
    "level",
    "difficulty",
)


def requires_stateful_split(scene: EditPlanScene) -> bool:
    if scene.action_timestamp is None:
        return False
    if scene.result_anchor_timestamp is not None and scene.result_anchor_timestamp - scene.action_timestamp >= 0.52:
        return True
    combined = normalized_scene_text(scene)
    return any(token in combined for token in INTERMEDIATE_STATE_TOKENS) and scene.readable_hold_seconds >= 1.0


def preserve_scene_separation(left: EditPlanScene, right: EditPlanScene) -> bool:
    if state_transition_gap(left, right):
        return True
    left_text = normalized_scene_text(left)
    right_text = normalized_scene_text(right)
    if "choose an account" in right_text or "loading" in right_text:
        return True
    if "continue with google" in left_text and "dashboard" in right_text:
        return True
    if "japanese" in left_text and ("difficulty" in right_text or "level" in right_text):
        return True
    return False


def state_transition_gap(left: EditPlanScene, right: EditPlanScene) -> bool:
    if left.result_anchor_timestamp is None or right.action_timestamp is None:
        return False
    return 0.0 <= right.action_timestamp - left.result_anchor_timestamp <= 3.4


def normalized_scene_text(scene: EditPlanScene) -> str:
    return " ".join(
        part.lower()
        for part in (
            scene.title,
            scene.purpose,
            scene.on_screen_text,
            scene.spoken_line,
            scene.source_excerpt,
            scene.specific_target_label,
        )
        if part
    )
