from __future__ import annotations

from app.models.projects import EditPlanScene
from app.services.scene_intent_resolver import split_clauses


def normalized_scene_text(scene: EditPlanScene) -> str:
    return " ".join(
        part.lower()
        for part in (
            scene.title,
            scene.purpose,
            scene.spoken_line,
            scene.on_screen_text,
            scene.source_excerpt,
            scene.specific_target_label,
        )
        if part
    )


def scene_profile(scene: EditPlanScene) -> str:
    combined = normalized_scene_text(scene)
    if scene.scene_role == "result":
        return "result_hold"
    if scene.action_class == "auth_action":
        if any(token in combined for token in ("account", "existing", "continue", "chooser")):
            return "auth_card"
        return "auth_button"
    if scene.action_class == "card_selection":
        return "course_card"
    if any(token in combined for token in ("difficulty", "setup", "preferences", "level")):
        return "setup_choice"
    return "generic"


def scene_hold_budget(scene: EditPlanScene) -> float:
    baseline = max(scene.readable_hold_seconds, 0.0)
    profile = scene_profile(scene)
    if profile == "result_hold":
        return round(max(baseline, 1.25), 2)
    if profile == "auth_card":
        return round(max(baseline, 1.0), 2)
    if profile in {"course_card", "setup_choice"}:
        return round(max(baseline, 0.9), 2)
    if scene.action_class in {"focus", "button_click"}:
        return round(max(baseline, 0.55), 2)
    return round(max(baseline, 0.4), 2)


def readability_floor_seconds(scene: EditPlanScene) -> float:
    profile = scene_profile(scene)
    if profile == "result_hold":
        return 1.5
    if profile == "auth_card":
        return 1.7
    if profile == "auth_button":
        return 1.45
    if profile in {"course_card", "setup_choice"}:
        return 1.85
    return 1.2 if scene.scene_role != "action" else 1.35


def minimum_scene_seconds(scene: EditPlanScene) -> float:
    base = readability_floor_seconds(scene)
    if scene.action_class == "navigation":
        return round(max(base, 1.4), 2)
    if scene.action_class == "tab_switch":
        return round(max(base, 1.3), 2)
    return round(base, 2)


def target_scene_seconds(scene: EditPlanScene) -> float:
    target = scene.render_duration_seconds or (scene.end - scene.start)
    floor = minimum_scene_seconds(scene)
    if scene.scene_role == "result":
        floor = max(floor, 2.1)
    if scene.action_class == "auth_action":
        floor = max(floor, 2.8)
    if scene.action_class == "card_selection":
        floor = max(floor, 3.2)
    return round(max(target, floor, scene_hold_budget(scene) + 0.95), 2)


def scene_weight(scene: EditPlanScene) -> float:
    profile = scene_profile(scene)
    if profile == "result_hold":
        return 1.34
    if profile == "auth_card":
        return 1.28
    if profile in {"auth_button", "course_card", "setup_choice"}:
        return 1.18
    return 1.0


def chapter_lead_seconds(scene: EditPlanScene) -> float:
    profile = scene_profile(scene)
    if profile == "auth_card":
        return 0.6
    if profile == "auth_button":
        return 0.5
    if profile in {"course_card", "setup_choice"}:
        return 0.55
    if profile == "result_hold":
        return 0.22
    return 0.35


def chapter_tail_seconds(scene: EditPlanScene) -> float:
    profile = scene_profile(scene)
    if profile == "result_hold":
        return 1.25
    if profile == "auth_card":
        return 1.55
    if profile == "auth_button":
        return 1.4
    if profile == "course_card":
        return 1.7
    if profile == "setup_choice":
        return 1.35
    return 0.9


def dense_intent_scene(scene: EditPlanScene, clauses: list[str]) -> bool:
    combined = normalized_scene_text(scene)
    semantic_hits = sum(
        1
        for token_group in (
            ("login", "account", "google"),
            ("dashboard", "home", "workspace"),
            ("course", "card", "lesson"),
            ("level", "difficulty", "setup"),
            ("result", "opened", "ready", "loaded"),
        )
        if any(token in combined for token in token_group)
    )
    return semantic_hits >= 2 or len(clauses) >= 3


def semantic_clauses(scene: EditPlanScene, voiceover: object | None) -> list[str]:
    text = " ".join(
        part.strip()
        for part in (
            getattr(voiceover, "text", ""),
            scene.spoken_line,
            scene.purpose,
            scene.source_excerpt,
        )
        if part and part.strip()
    )
    clauses = [clause for clause in split_clauses(text) if clause.strip()]
    return clauses[:4] or [scene.spoken_line or scene.purpose or scene.title]


def scene_split_weights(scene_type: str, split_count: int) -> tuple[float, ...]:
    if split_count <= 2:
        if scene_type == "course_card":
            return (0.4, 0.6)
        if scene_type == "setup_choice":
            return (0.44, 0.56)
        return (0.48, 0.52)
    if split_count == 3:
        if scene_type == "auth_button":
            return (0.28, 0.4, 0.32)
        if scene_type == "auth_card":
            return (0.34, 0.31, 0.35)
        if scene_type == "course_card":
            return (0.3, 0.4, 0.3)
        if scene_type == "setup_choice":
            return (0.3, 0.33, 0.37)
        return (0.33, 0.37, 0.3)
    if scene_type in {"course_card", "setup_choice"}:
        return (0.24, 0.24, 0.26, 0.26)
    return (0.22, 0.26, 0.26, 0.26)
