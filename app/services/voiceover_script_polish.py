from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, ProjectRecord
from app.services.editorial_labels import completed_selection_target
from app.services.voiceover_pacing import fit_voice_line


def polish_voiceover_script(project: ProjectRecord, edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes: list[EditPlanScene] = []
    previous_line = ""
    descriptor = product_descriptor(project.product_description, project.product_name)
    for index, scene in enumerate(edit_plan.scenes):
        duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
        line = fit_voice_line(
            scene_voiceover_line(scene, project.product_name, descriptor, index == 0, previous_line),
            duration,
        )
        scenes.append(
            scene.model_copy(
                update={
                    "spoken_line": line,
                    "show_captions": False,
                    "captions": [],
                }
            )
        )
        previous_line = line
    return edit_plan.model_copy(update={"scenes": scenes})


def scene_voiceover_line(
    scene: EditPlanScene,
    product_name: str,
    descriptor: str,
    is_first: bool,
    previous_line: str,
) -> str:
    target = scene_target(scene)
    if is_first and should_use_launch_intro(scene, target):
        return launch_intro_line(product_name, descriptor, target)
    if scene.action_class == "auth_action":
        return duplicate_safe(previous_line, auth_voiceover_line(scene, target))
    if scene.action_class == "card_selection":
        return duplicate_safe(previous_line, selection_voiceover_line(scene, target))
    if "level" in scene_title(scene):
        return duplicate_safe(previous_line, level_voiceover_line(scene))
    if any(word in scene_title(scene) for word in ("setup", "preferences", "settings", "difficulty")):
        return duplicate_safe(previous_line, "These setup choices shape the experience before the first lesson begins.")
    if scene.scene_role == "result":
        return duplicate_safe(previous_line, "The next product state is now in view, and the walkthrough is ready to continue.")
    return duplicate_safe(previous_line, scene.spoken_line or scene.purpose or scene.title)


def product_descriptor(description: str, product_name: str) -> str:
    cleaned = " ".join((description or "").split()).strip().rstrip(".")
    lowered = cleaned.lower()
    product_lower = product_name.lower().strip()
    if not cleaned or looks_like_probe_metadata(cleaned):
        return ""
    if any(token in lowered for token in ("we are launching", "we're launching", "to get started", "click", "google login")):
        return ""
    if product_lower and product_lower in lowered:
        return ""
    words = cleaned.split()
    snippet = " ".join(words[:6]).strip(" ,-")
    if len(words) > 6 and "platform" not in snippet.lower() and "platform" in lowered:
        snippet = f"{snippet} platform".strip()
    return snippet if 1 <= len(snippet.split()) <= 7 else ""


def scene_target(scene: EditPlanScene) -> str:
    if scene.action_class == "auth_action":
        return auth_scene_target(scene)
    if scene.action_class == "card_selection":
        return completed_selection_target(
            specific_target_label=scene.specific_target_label or scene.on_screen_text,
            canonical_label=scene.title or scene.on_screen_text,
            transcript_excerpt=scene.source_excerpt,
        )
    source = (scene.specific_target_label or scene.on_screen_text or scene.title or "the next step").strip().rstrip(".")
    return source if source.lower().startswith(("the ", "your ")) else source


def auth_scene_target(scene: EditPlanScene) -> str:
    for candidate in (
        scene.specific_target_label,
        scene.on_screen_text,
        scene.title,
    ):
        cleaned = " ".join((candidate or "").split()).strip().rstrip(".")
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if any(token in lowered for token in ("google login", "continue with google", "account", "sign in", "login")):
            return cleaned
    return "Google Login"


def auth_voiceover_line(scene: EditPlanScene, target: str) -> str:
    lowered = target.lower()
    if lowered == "continue with google":
        return "Continue with Google to enter the workspace."
    if "account" in lowered:
        return f"Choose {target} so the workspace opens right away."
    return f"Use {target} to sign in and continue into the product."


def level_voiceover_line(scene: EditPlanScene) -> str:
    label = normalized_level_target(scene.on_screen_text or scene.title)
    return f"Next, choose {label} that matches the learner's starting point before the first lesson begins."


def launch_intro_line(product_name: str, descriptor: str, target: str) -> str:
    opener = f"This is {product_name}"
    if descriptor:
        opener = f"{opener}, {descriptor}"
    return f"{opener}. Start by clicking {clean_click_target(target)} to enter the product and begin the guided flow."


def looks_like_probe_metadata(description: str) -> bool:
    lowered = description.lower()
    metadata_tokens = ("probe", "replay", "artifact", "artifacts", "raw upload", "phase four", "local ")
    return sum(token in lowered for token in metadata_tokens) >= 2


def selection_voiceover_line(scene: EditPlanScene, target: str) -> str:
    refined_target = premium_selection_target(target)
    if transcript_mentions(scene, "course", "courses"):
        return f"From the course library, choose {refined_target} to open the guided learning flow."
    return f"From here, choose {refined_target} to open the next part of the product flow."


def should_use_launch_intro(scene: EditPlanScene, target: str) -> bool:
    if scene.action_class != "auth_action" or scene.scene_role != "action":
        return False
    lowered = target.lower()
    if "continue with google" in lowered or "account" in lowered:
        return False
    return any(token in lowered for token in ("google login", "login", "sign in"))


def transcript_mentions(scene: EditPlanScene, *terms: str) -> bool:
    excerpt = (scene.source_excerpt or "").lower()
    return any(term in excerpt for term in terms)


def normalized_level_target(source: str) -> str:
    cleaned = " ".join(source.split()).strip().rstrip(".")
    lowered = cleaned.lower()
    if "japanese level" in lowered:
        return "the Japanese level"
    if lowered.startswith(("pick your ", "choose your ", "select your ")):
        lowered = lowered.split(" ", 2)[-1]
        return f"the {lowered}".strip()
    return cleaned if lowered.startswith(("the ", "your ")) else f"the {cleaned}".strip()


def scene_title(scene: EditPlanScene) -> str:
    return " ".join((scene.title, scene.on_screen_text, scene.purpose)).lower()


def duplicate_safe(previous_line: str, line: str) -> str:
    if normalize(previous_line) == normalize(line):
        return "That keeps the walkthrough moving into the next product step."
    if repeated_launch_phrase(line):
        return strip_repeated_launch_phrase(line)
    return line


def clean_click_target(target: str) -> str:
    cleaned = " ".join(target.split()).strip().rstrip(".")
    lowered = cleaned.lower()
    if lowered.startswith("click "):
        return cleaned[6:].strip()
    if lowered.startswith("choose "):
        return cleaned[7:].strip()
    return cleaned


def premium_selection_target(target: str) -> str:
    cleaned = " ".join(target.split()).strip().rstrip(".")
    lowered = cleaned.lower()
    if lowered.startswith("the "):
        cleaned = cleaned[4:].strip()
        lowered = cleaned.lower()
    if lowered.endswith(" course"):
        cleaned = cleaned[:-7].strip()
    return cleaned or "the next course"


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split()).strip().rstrip(".")

def repeated_launch_phrase(text: str) -> bool:
    lowered = normalize(text)
    return "we are launching" in lowered and "we're launching" in lowered


def strip_repeated_launch_phrase(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    duplicate = "We are launching"
    if duplicate not in cleaned:
        return cleaned
    first, _, remainder = cleaned.partition(duplicate)
    fallback = f"{first.strip().rstrip(',')}. {remainder.strip()}".replace("..", ".")
    return " ".join(fallback.split()).strip()
