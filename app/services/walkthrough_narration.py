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
        return sentence(join_phrases(f"Enter details in {focus}", progress_phrase(scene)))
    if action_class in {"navigation", "tab_switch"}:
        return sentence(join_phrases(f"Open {focus}", progress_phrase(scene)))
    if action_class == "auth_action":
        return auth_line(focus, scene)
    lead = f"Choose {focus}" if action_class == "card_selection" else f"Select {focus}"
    return sentence(join_phrases(lead, progress_phrase(scene)))


def result_line(scene: EditPlanScene, label: str) -> str:
    if not label:
        return explanation_line(scene, label)
    focus = target_phrase(label)
    if is_dashboard_scene(scene):
        return sentence("Land on the course dashboard so every available learning path is clear.")
    if is_level_scene(scene):
        return sentence("Open the Japanese path and review the starting level before the lesson begins.")
    if scene.action_class in {"navigation", "auth_action", "tab_switch"}:
        return sentence(join_phrases(f"You'll land on {focus}", progress_phrase(scene)))
    if scene.purpose and not transcript_like_label(scene.purpose):
        return sentence(f"This opens {compact_phrase(scene.purpose)}")
    return sentence(join_phrases(f"You'll see {article_label(focus)}", progress_phrase(scene)))


def explanation_line(scene: EditPlanScene, label: str) -> str:
    if label and label.lower() not in scene.title.lower():
        return sentence(join_phrases(f"Review {article_label(target_phrase(label))}", scene_context(scene)))
    return sentence(compact_phrase(scene.spoken_line or scene.purpose or scene.title or scene_context(scene)))


def auth_line(label: str, scene: EditPlanScene) -> str:
    lowered = label.lower()
    if "account" in scene.on_screen_text.lower() or "account" in scene.source_excerpt.lower():
        return sentence("Pick the existing Google account to continue straight into the product.")
    if "google" in lowered:
        return sentence(join_phrases("Continue with Google for a clean login", progress_phrase(scene)))
    if "sign up" in lowered and "google" not in lowered:
        return sentence(join_phrases(f"Use {label} to create the account", progress_phrase(scene)))
    return sentence(join_phrases(f"Use {label} to sign in", progress_phrase(scene)))


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


def outcome_phrase(scene: EditPlanScene) -> str:
    if scene.purpose and not transcript_like_label(scene.purpose):
        return compact_phrase(scene.purpose)
    return scene_context(scene)


def progress_phrase(scene: EditPlanScene) -> str:
    if scene.action_class == "auth_action":
        return "then move straight into the dashboard"
    if is_dashboard_scene(scene):
        return "so the course options are easy to scan"
    if scene.action_class == "card_selection":
        return "to enter the guided learning path"
    if is_level_scene(scene):
        return "so the next setup step is ready"
    if scene.scene_role == "result":
        return "so the next product state is clear"
    return outcome_phrase(scene)


def scene_context(scene: EditPlanScene) -> str:
    for candidate in (scene.purpose, scene.title, scene.on_screen_text):
        compact = compact_phrase(candidate)
        if compact and not transcript_like_label(compact):
            return compact
    return ""


def join_phrases(primary: str, secondary: str) -> str:
    secondary = secondary.strip()
    if not secondary:
        return primary
    if secondary.lower().startswith(primary.lower()):
        return primary
    return f"{primary}, then {secondary}"


def is_dashboard_scene(scene: EditPlanScene) -> bool:
    combined = f"{scene.on_screen_text} {scene.source_excerpt} {scene.purpose}".lower()
    return "select a course" in combined or "dashboard" in combined or "courses" in combined


def is_level_scene(scene: EditPlanScene) -> bool:
    combined = f"{scene.on_screen_text} {scene.source_excerpt} {scene.purpose}".lower()
    return "pick your japanese level" in combined or "level" in combined
