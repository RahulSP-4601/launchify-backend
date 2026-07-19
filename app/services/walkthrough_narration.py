from __future__ import annotations

from app.models.projects import EditPlanScene
from app.services.scene_roles import scene_role_from_scene


def scene_voice_line(scene: EditPlanScene) -> str:
    role = scene_role_from_scene(scene)
    label = preferred_label(scene, role)
    if role == "action":
        return action_line(scene.action_class, label)
    if role == "result":
        return result_line(label)
    return explanation_line(scene, label)


def preferred_label(scene: EditPlanScene, role: str) -> str:
    if role == "action":
        return first_text(scene.source_excerpt, scene.on_screen_text, scene.purpose, scene.title)
    if role == "result":
        return first_text(scene.on_screen_text, scene.source_excerpt, scene.purpose, scene.title)
    return first_text(scene.purpose, scene.on_screen_text, scene.source_excerpt, scene.title)


def action_line(action_class: str, label: str) -> str:
    if action_class == "input_entry":
        return sentence(f"Enter {label}")
    if action_class in {"navigation", "tab_switch"}:
        return sentence(f"Open {label}")
    if action_class == "auth_action":
        return sentence(f"Click {label}")
    return sentence(f"Select {label}" if action_class == "card_selection" else f"Click {label}")


def result_line(label: str) -> str:
    return sentence(f"Now you'll see {article_label(label)}")


def explanation_line(scene: EditPlanScene, label: str) -> str:
    if label and label.lower() not in scene.title.lower():
        return sentence(f"Here you can review {article_label(label)}")
    return sentence(scene.spoken_line or scene.purpose or scene.title)


def article_label(label: str) -> str:
    cleaned = " ".join(label.split()).strip().rstrip(".")
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    if lowered.startswith(("your ", "the ", "a ", "an ")):
        return cleaned
    return f"the {cleaned}"


def first_text(*candidates: str) -> str:
    for candidate in candidates:
        cleaned = " ".join(candidate.split()).strip().rstrip(".")
        if cleaned:
            return cleaned
    return ""


def sentence(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."
