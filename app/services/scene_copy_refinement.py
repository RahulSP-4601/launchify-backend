from __future__ import annotations

import re

from app.models.projects import EditPlanCaption, EditPlanRecord, EditPlanScene
from app.services.editorial_labels import completed_selection_target
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, SELECTION_FAMILY, scene_contexts
from app.services.voiceover_pacing import fit_voice_line
from app.services.walkthrough_narration import scene_voice_line, transcript_like_label

GENERIC_SETUP_WORDS = {
    "before",
    "begin",
    "choose",
    "continue",
    "course",
    "difficulty",
    "lesson",
    "level",
    "option",
    "options",
    "pick",
    "preferences",
    "role",
    "select",
    "set",
    "setup",
    "settings",
    "starting",
    "template",
    "the",
    "to",
    "up",
    "workspace",
    "your",
}


def apply_scene_copy_refinement(edit_plan: EditPlanRecord) -> EditPlanRecord:
    contexts = scene_contexts(edit_plan.scenes)
    scenes: list[EditPlanScene] = []
    previous_scene: EditPlanScene | None = None
    for scene in edit_plan.scenes:
        refined = refine_scene_copy(scene, contexts.get(scene.scene_number), previous_scene)
        scenes.append(refined)
        previous_scene = refined
    return edit_plan.model_copy(update={"scenes": scenes})


def refine_scene_copy(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
    previous_scene: EditPlanScene | None,
) -> EditPlanScene:
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    purpose = refined_purpose(scene)
    on_screen_text = refined_on_screen_text(scene, purpose)
    refined_scene = scene.model_copy(update={"purpose": purpose, "on_screen_text": on_screen_text})
    spoken_line = fit_voice_line(context_aware_spoken_line(refined_scene, context, previous_scene), duration)
    captions = refined_captions(refined_scene, spoken_line)
    return refined_scene.model_copy(update={"spoken_line": spoken_line, "captions": captions})


def refined_captions(scene: EditPlanScene, spoken_line: str) -> list[EditPlanCaption]:
    if not scene.show_captions:
        return []
    if scene.captions:
        first = scene.captions[0]
        text = compact_caption(spoken_line if scene.layout_mode in {"feature-center", "split-right"} else first.text or spoken_line)
        return [first.model_copy(update={"text": text, "start": round(scene.start, 2), "end": round(scene.end, 2), "variant": "minimal"})]
    text = compact_caption(spoken_line)
    if not text:
        return []
    return [EditPlanCaption(start=round(scene.start, 2), end=round(scene.end, 2), text=text, emphasis_words=[], variant="minimal")]


def compact_caption(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    if len(words) <= 6:
        return cleaned[:76]
    midpoint = min(max(len(words) // 2, 3), 6)
    first_line = " ".join(words[:midpoint]).strip()
    second_line = " ".join(words[midpoint : midpoint + 5]).strip()
    if len(words) > midpoint + 5 and not second_line.endswith(("...", ".", "!", "?")):
        second_line = f"{second_line}..."
    combined = f"{first_line}\n{second_line}".strip()
    return combined[:76]


def refined_purpose(scene: EditPlanScene) -> str:
    for candidate in (scene.purpose, scene.on_screen_text, scene.title):
        refined = normalized_scene_copy(candidate)
        if refined:
            return refined
    return scene.purpose


def refined_on_screen_text(scene: EditPlanScene, purpose: str) -> str:
    action_label = concise_action_label(scene)
    setup_label = specific_setup_label(scene)
    if scene.layout_mode == "screen-only" and action_label:
        return action_label
    if scene.layout_mode == "screen-only" and setup_label:
        return setup_label
    if scene.layout_mode == "screen-only":
        return purpose
    if scene.layout_mode == "dashboard-wide" and purpose:
        return purpose
    return scene.on_screen_text or purpose


def concise_action_label(scene: EditPlanScene) -> str:
    for candidate in (
        scene.specific_target_label,
        scene.on_screen_text,
        scene.title,
    ):
        refined = normalized_scene_copy(candidate)
        if not refined or transcript_like_label(refined):
            continue
        if scene.action_class == "auth_action" and any(token in refined.lower() for token in ("login", "google", "sign in", "account")):
            return refined
        if scene.action_class == "card_selection":
            return spoken_target(scene)
    return ""


def normalized_scene_copy(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    if cleaned.endswith(("...", ".", "!", "?")):
        cleaned = cleaned.rstrip(".!? ")
    words = cleaned.split()
    if len(words) > 10:
        cleaned = " ".join(words[:10]).strip()
    return cleaned[:88]


def context_aware_spoken_line(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
    previous_scene: EditPlanScene | None,
) -> str:
    if context is None:
        return scene_voice_line(scene)
    label = normalized_scene_copy(scene.specific_target_label or scene.on_screen_text or scene.title or scene.purpose).lower()
    if context.family == AUTH_FAMILY:
        spoken = auth_spoken_line(scene, context, label)
    elif context.family == SELECTION_FAMILY:
        spoken = selection_spoken_line(scene, context, label)
    elif context.family == CONFIG_FAMILY:
        spoken = configuration_spoken_line(scene, label)
    else:
        spoken = scene_voice_line(scene)
    if previous_scene is not None and normalize_compare(spoken) == normalize_compare(previous_scene.spoken_line):
        spoken = diversify_neighbor_line(scene, context, label)
    return spoken


def auth_spoken_line(scene: EditPlanScene, context: FlowSceneContext, label: str) -> str:
    if "continue with google" in label:
        return "Continue with Google to keep sign-in fast and familiar."
    if any(token in label for token in ("google login", "login", "sign in")) and context.is_first:
        return "The walkthrough opens with a direct sign-in path from the landing page."
    if "account" in label:
        return "Choose the existing account so the product opens right away."
    if context.is_first:
        return "This first interaction clears the way into the main product experience."
    target = auth_target(scene)
    return f"Use {target} to finish sign-in and move into the product."


def selection_spoken_line(scene: EditPlanScene, context: FlowSceneContext, label: str) -> str:
    if any(token in label for token in ("japanese", "course", "workspace", "template", "plan", "project")):
        target = premium_selection_target(scene)
        if context.next_scene is not None:
            return f"From the course library, choose {target} to enter the guided setup."
        return f"From the course library, choose {target} to set the direction for the rest of the journey."
    if context.previous_scene is not None and context.previous_scene.action_class == "auth_action":
        return "This selection carries the user from login into the onboarding flow."
    return "This selection defines the guided path the product opens next."


def configuration_spoken_line(scene: EditPlanScene, label: str) -> str:
    if "level" in label:
        target = spoken_configuration_target(scene)
        return f"{target.capitalize()} personalizes the experience before the lesson begins."
    if any(token in label for token in ("difficulty", "setup", "preferences")):
        return "These setup choices shape the product around the user's starting point."
    return scene_voice_line(scene)


def diversify_neighbor_line(scene: EditPlanScene, context: FlowSceneContext, label: str) -> str:
    if context.family == AUTH_FAMILY and "continue with google" in label:
        return "That Google sign-in step keeps the login flow lightweight and quick."
    if context.family == AUTH_FAMILY and "account" in label:
        return "Pick the existing account to complete login without repeating setup."
    if context.family == SELECTION_FAMILY:
        if any(token in label for token in ("course", "japanese", "workspace", "template", "plan", "project")):
            return f"{spoken_target(scene)} carries the viewer into the next guided setup screen."
        return "This next choice keeps the guided flow moving without breaking momentum."
    if context.family == CONFIG_FAMILY:
        return "This final setup choice makes the first session feel tailored from the start."
    return scene_voice_line(scene)


def spoken_target(scene: EditPlanScene) -> str:
    return completed_selection_target(
        specific_target_label=scene.specific_target_label or scene.on_screen_text or "",
        canonical_label=scene.title or scene.on_screen_text,
        transcript_excerpt=scene.source_excerpt,
    )


def premium_selection_target(scene: EditPlanScene) -> str:
    target = spoken_target(scene).strip()
    lowered = target.lower()
    if lowered.startswith("the "):
        target = target[4:].strip()
        lowered = target.lower()
    if lowered.endswith(" course"):
        target = target[:-7].strip()
    return target or "the next course"


def auth_target(scene: EditPlanScene) -> str:
    source = (scene.on_screen_text or scene.title or "the sign-in option").strip().rstrip(".")
    lowered = source.lower()
    if lowered.startswith("continue with "):
        return source[14:].strip()
    return source


def normalize_compare(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")


def specific_setup_label(scene: EditPlanScene) -> str:
    candidates = [
        scene.specific_target_label,
        *(highlight.label for highlight in scene.highlights),
        scene.on_screen_text,
        scene.title,
    ]
    ranked = [normalized_scene_copy(candidate) for candidate in candidates if setup_candidate_is_specific(candidate)]
    return ranked[0] if ranked else ""


def setup_candidate_is_specific(text: str) -> bool:
    normalized = normalized_scene_copy(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if "level" not in lowered and not any(token in lowered for token in ("settings", "preferences", "role", "template", "workspace", "setup")):
        return False
    return bool(specific_setup_tokens(normalized))


def specific_setup_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if (len(token) >= 3 or any(char.isdigit() for char in token)) and token not in GENERIC_SETUP_WORDS
    }


def spoken_configuration_target(scene: EditPlanScene) -> str:
    source = specific_setup_label(scene) or normalized_scene_copy(scene.on_screen_text or scene.title)
    lowered = source.lower()
    if lowered.startswith(("your ", "the ", "a ", "an ")):
        return source
    return f"the {source}".strip()
