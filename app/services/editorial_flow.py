from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanScene

AUTH_FAMILY = "auth"
SELECTION_FAMILY = "selection"
CONFIG_FAMILY = "configuration"
RESULT_FAMILY = "result"
GENERIC_FAMILY = "generic"


@dataclass(frozen=True)
class FlowSceneContext:
    family: str
    position: int
    size: int
    previous_scene: EditPlanScene | None
    next_scene: EditPlanScene | None

    @property
    def is_first(self) -> bool:
        return self.position == 0

    @property
    def is_last(self) -> bool:
        return self.position == self.size - 1


def scene_contexts(scenes: list[EditPlanScene]) -> dict[int, FlowSceneContext]:
    ordered = sorted(scenes, key=lambda scene: (scene.start, scene.scene_number))
    contexts: dict[int, FlowSceneContext] = {}
    current_flow: list[EditPlanScene] = []
    for scene in ordered:
        if not current_flow or should_join_flow(current_flow[-1], scene):
            current_flow.append(scene)
            continue
        assign_flow_contexts(current_flow, contexts)
        current_flow = [scene]
    if current_flow:
        assign_flow_contexts(current_flow, contexts)
    return contexts


def assign_flow_contexts(flow: list[EditPlanScene], contexts: dict[int, FlowSceneContext]) -> None:
    size = len(flow)
    for index, scene in enumerate(flow):
        contexts[scene.scene_number] = FlowSceneContext(
            family=flow_family(scene),
            position=index,
            size=size,
            previous_scene=flow[index - 1] if index > 0 else None,
            next_scene=flow[index + 1] if index + 1 < size else None,
        )


def flow_family(scene: EditPlanScene) -> str:
    combined = " ".join(
        part.lower()
        for part in (scene.on_screen_text, scene.purpose, scene.source_excerpt, scene.title)
        if part
    )
    if scene.action_class in {"button_click", "focus"}:
        return CONFIG_FAMILY if scene.scene_role == "action" else RESULT_FAMILY
    if scene.action_class == "card_selection":
        return SELECTION_FAMILY
    if scene.action_class == "auth_action":
        return AUTH_FAMILY
    if any(token in combined for token in ("course", "catalog", "dashboard", "japanese")):
        return SELECTION_FAMILY
    if any(token in combined for token in ("google login", "continue with google", "choose an account", "sign in", "log in")):
        return AUTH_FAMILY
    if any(token in combined for token in ("level", "difficulty", "configure", "setup")):
        return CONFIG_FAMILY if scene.scene_role == "action" else RESULT_FAMILY
    if scene.scene_role == "result":
        return RESULT_FAMILY
    return GENERIC_FAMILY


def should_join_flow(left: EditPlanScene, right: EditPlanScene) -> bool:
    gap = max(right.start - left.end, 0.0)
    left_family = flow_family(left)
    right_family = flow_family(right)
    if gap <= 0.85:
        return True
    if left_family == right_family and gap <= shared_family_gap(left_family):
        return True
    if left_family == AUTH_FAMILY and right_family == SELECTION_FAMILY and gap <= 4.2:
        return True
    if left_family == SELECTION_FAMILY and right_family == CONFIG_FAMILY and gap <= 8.4:
        return True
    return False


def shared_family_gap(family: str) -> float:
    if family == AUTH_FAMILY:
        return 10.0
    if family == SELECTION_FAMILY:
        return 8.0
    if family == CONFIG_FAMILY:
        return 6.0
    return 3.0
