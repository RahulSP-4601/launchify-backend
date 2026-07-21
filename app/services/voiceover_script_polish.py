from __future__ import annotations

from app.models.projects import EditPlanCaption, EditPlanRecord, EditPlanScene, ProjectRecord
from app.services.editorial_labels import completed_selection_target
from app.services.voiceover_pacing import fit_voice_line


def polish_voiceover_script(project: ProjectRecord, edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes: list[EditPlanScene] = []
    previous_line = ""
    descriptor = product_descriptor(project.product_description)
    for index, scene in enumerate(edit_plan.scenes):
        duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
        line = fit_voice_line(
            scene_voiceover_line(scene, project.product_name, descriptor, index == 0, previous_line),
            duration,
        )
        scenes.append(scene.model_copy(update={"spoken_line": line, "captions": synced_captions(scene, line)}))
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


def product_descriptor(description: str) -> str:
    cleaned = " ".join((description or "").split()).strip().rstrip(".")
    if not cleaned or looks_like_probe_metadata(cleaned):
        return ""
    words = cleaned.split()
    snippet = " ".join(words[:5]).strip()
    if "platform" in cleaned.lower() and "platform" not in snippet.lower():
        snippet = f"{snippet} platform".strip()
    return snippet


def scene_target(scene: EditPlanScene) -> str:
    if scene.action_class == "card_selection":
        return completed_selection_target(
            specific_target_label=scene.specific_target_label or scene.on_screen_text,
            canonical_label=scene.title or scene.on_screen_text,
            transcript_excerpt=scene.source_excerpt,
        )
    source = (scene.specific_target_label or scene.on_screen_text or scene.title or "the next step").strip().rstrip(".")
    return source if source.lower().startswith(("the ", "your ")) else source


def auth_voiceover_line(scene: EditPlanScene, target: str) -> str:
    lowered = target.lower()
    if lowered == "continue with google":
        return "Continue with Google to keep sign-in quick and move straight into the product."
    if "account" in lowered:
        return f"Choose {target} so the workspace opens right away and the walkthrough can continue."
    return f"Use {target} to finish sign-in and continue into the core product experience."


def level_voiceover_line(scene: EditPlanScene) -> str:
    label = normalized_level_target(scene.on_screen_text or scene.title)
    return f"Next, choose {label} that matches the learner's starting point before the first lesson begins."


def launch_intro_line(product_name: str, descriptor: str, target: str) -> str:
    opener = f"We're launching {product_name}"
    if descriptor:
        opener = f"{opener}, {descriptor}"
    return f"{opener} and you can start by clicking {target} to enter the product and begin the guided flow."


def looks_like_probe_metadata(description: str) -> bool:
    lowered = description.lower()
    metadata_tokens = ("probe", "replay", "artifact", "artifacts", "raw upload", "phase four", "local ")
    return sum(token in lowered for token in metadata_tokens) >= 2


def selection_voiceover_line(scene: EditPlanScene, target: str) -> str:
    if transcript_mentions(scene, "course", "courses"):
        return f"From here, open {target} to move from the course catalog into the guided setup experience."
    return f"From here, choose {target} to continue deeper into the main product experience."


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
    return line if normalize(previous_line) != normalize(line) else "That keeps the walkthrough moving into the next product step."


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split()).strip().rstrip(".")


def synced_captions(scene: EditPlanScene, spoken_line: str) -> list[EditPlanCaption]:
    if not scene.show_captions:
        return []
    text = compact_caption(spoken_line)
    if not text:
        return []
    if scene.captions:
        first = scene.captions[0]
        return [first.model_copy(update={"text": text, "start": round(scene.start, 2), "end": round(scene.end, 2)})]
    return [EditPlanCaption(start=round(scene.start, 2), end=round(scene.end, 2), text=text, emphasis_words=[], variant="minimal")]


def compact_caption(text: str) -> str:
    words = " ".join(text.split()).strip().split()
    if not words:
        return ""
    if len(words) <= 6:
        return " ".join(words)
    midpoint = min(max(len(words) // 2, 3), 6)
    first_line = " ".join(words[:midpoint])
    second_line = " ".join(words[midpoint:])
    return f"{first_line}\n{second_line}".strip()
