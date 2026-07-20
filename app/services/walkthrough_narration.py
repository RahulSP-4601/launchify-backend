from __future__ import annotations

from app.models.projects import EditPlanScene
from app.services.scene_roles import scene_role_from_scene

MAX_LABEL_WORDS = 8
MAX_LABEL_CHARACTERS = 72
LEADING_ACTION_WORDS = ("click ", "tap ", "select ", "open ", "choose ", "pick ", "press ")


def scene_voice_line(scene: EditPlanScene) -> str:
    role = scene_role_from_scene(scene)
    label = preferred_label(scene, role)
    if role == "action":
        return action_line(scene, label)
    if role == "result":
        return result_line(scene, label)
    return explanation_line(scene, label)


def preferred_label(scene: EditPlanScene, role: str) -> str:
    if role == "action":
        return compact_label(scene.on_screen_text, scene.title, scene.purpose, scene.source_excerpt)
    if role == "result":
        return compact_label(scene.on_screen_text, scene.title, scene.purpose, scene.source_excerpt)
    return compact_label(scene.purpose, scene.on_screen_text, scene.title, scene.source_excerpt)


def action_line(scene: EditPlanScene, label: str) -> str:
    if not label:
        return explanation_line(scene, label)
    focus = target_phrase(label)
    action_class = scene.action_class
    if action_class == "input_entry":
        return sentence(f"Enter details in {focus}")
    if action_class in {"navigation", "tab_switch"}:
        return sentence(f"Open {focus}")
    if action_class == "auth_action":
        return auth_line(focus)
    return sentence(f"Choose {focus}" if action_class == "card_selection" else f"Select {focus}")


def result_line(scene: EditPlanScene, label: str) -> str:
    if not label:
        return explanation_line(scene, label)
    focus = target_phrase(label)
    if scene.action_class in {"navigation", "auth_action", "tab_switch"}:
        return sentence(f"You'll land on {focus}")
    if scene.purpose and not transcript_like_label(scene.purpose):
        return sentence(f"This opens {compact_phrase(scene.purpose)}")
    return sentence(f"You'll see {article_label(focus)}")


def explanation_line(scene: EditPlanScene, label: str) -> str:
    if label and label.lower() not in scene.title.lower():
        return sentence(f"Review {article_label(target_phrase(label))}")
    return sentence(compact_phrase(scene.spoken_line or scene.purpose or scene.title))


def auth_line(label: str) -> str:
    lowered = label.lower()
    if "google" in lowered:
        return "Continue with Google to sign in."
    if "sign up" in lowered and "google" not in lowered:
        return sentence(f"Use {label} to create the account")
    return sentence(f"Use {label} to sign in")


def target_phrase(label: str) -> str:
    cleaned = article_label(trim_leading_action(label))
    return cleaned if cleaned else "this step"


def trim_leading_action(label: str) -> str:
    cleaned = " ".join(label.split()).strip().rstrip(".")
    lowered = cleaned.lower()
    for prefix in LEADING_ACTION_WORDS:
        if lowered.startswith(prefix):
            return cleaned[len(prefix) :].strip()
    return cleaned


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


def compact_label(*candidates: str) -> str:
    for candidate in candidates:
        cleaned = first_text(candidate)
        if not cleaned or transcript_like_label(cleaned):
            continue
        words = cleaned.split()
        compact = " ".join(words[:MAX_LABEL_WORDS]).strip()
        return compact[:MAX_LABEL_CHARACTERS].rstrip(" ,")
    return ""


def compact_phrase(value: str) -> str:
    cleaned = first_text(value)
    if not cleaned:
        return ""
    words = cleaned.split()
    short = " ".join(words[:8]).strip().rstrip(" ,")
    return short[:88]


def transcript_like_label(value: str) -> bool:
    lowered = value.lower()
    if len(value) > MAX_LABEL_CHARACTERS:
        return True
    if len(value.split()) > MAX_LABEL_WORDS:
        return True
    transcript_markers = (
        "so to get started",
        "once you click",
        "since i've already",
        "after log in",
        "right now",
        "coming soon",
        "officially",
    )
    return any(marker in lowered for marker in transcript_markers)


def sentence(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."
