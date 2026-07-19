from __future__ import annotations

from app.models.projects import EditPlanScene, SceneRole

SCENE_ROLES = ("action", "result", "explanation")


def scene_role_from_action_class(action_class: str) -> SceneRole:
    if action_class in {"result_state", "explanatory_hold"}:
        return "result" if action_class == "result_state" else "explanation"
    return "action"


def scene_role_from_scene(scene: EditPlanScene) -> SceneRole:
    return scene.scene_role if scene.scene_role in SCENE_ROLES else scene_role_from_action_class(scene.action_class)


def scene_prefers_motion(scene: EditPlanScene) -> bool:
    role = scene_role_from_scene(scene)
    if role == "action":
        return True
    return role == "result" and scene.action_class in {"result_state", "navigation", "tab_switch"}


def scene_prefers_highlight(scene: EditPlanScene) -> bool:
    role = scene_role_from_scene(scene)
    if role != "action":
        return False
    return scene.action_class not in {"navigation", "generic_action"}
