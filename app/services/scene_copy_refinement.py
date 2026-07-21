from __future__ import annotations

from app.models.projects import EditPlanCaption, EditPlanRecord, EditPlanScene
from app.services.editorial_labels import completed_selection_target
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, SELECTION_FAMILY, scene_contexts
from app.services.voiceover_pacing import fit_voice_line
from app.services.walkthrough_narration import scene_voice_line


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
    if scene.layout_mode == "screen-only":
        return purpose
    if scene.layout_mode == "dashboard-wide" and purpose:
        return purpose
    return scene.on_screen_text or purpose


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
        return "Continue with Google to move through sign-in in one step."
    if any(token in label for token in ("google login", "login", "sign in")) and context.is_first:
        return "Click Google Login to start the flow from the landing page."
    if "account" in label:
        return "Choose the existing account so the product opens right away."
    if context.is_first:
        return "Start with the primary login action."
    return "Finish sign-in and continue into the product."


def selection_spoken_line(scene: EditPlanScene, context: FlowSceneContext, label: str) -> str:
    if any(token in label for token in ("japanese", "course", "workspace", "template", "plan", "project")):
        if context.next_scene is not None:
            return f"Open {spoken_target(scene)} to move directly into setup."
        return f"Choose {spoken_target(scene)} to enter the learning path."
    if context.previous_scene is not None and context.previous_scene.action_class == "auth_action":
        return "Choose the course that carries the signup flow into onboarding."
    return "Choose the course that opens the guided journey."


def configuration_spoken_line(scene: EditPlanScene, label: str) -> str:
    if "level" in label:
        return "Select the Japanese level that matches your starting point."
    if any(token in label for token in ("difficulty", "setup", "preferences")):
        return "Set the starting options before the lesson begins."
    return scene_voice_line(scene)


def diversify_neighbor_line(scene: EditPlanScene, context: FlowSceneContext, label: str) -> str:
    if context.family == AUTH_FAMILY and "continue with google" in label:
        return "Use Continue with Google to complete the login cleanly."
    if context.family == SELECTION_FAMILY:
        if any(token in label for token in ("course", "japanese", "workspace", "template", "plan", "project")):
            return f"Open {spoken_target(scene)} to start the guided setup."
        return "Choose the next course option to keep the journey moving."
    if context.family == CONFIG_FAMILY:
        return "Choose the option that fits the starting setup."
    return scene_voice_line(scene)


def spoken_target(scene: EditPlanScene) -> str:
    return completed_selection_target(
        specific_target_label=scene.specific_target_label or scene.on_screen_text or "",
        canonical_label=scene.title or scene.on_screen_text,
        transcript_excerpt=scene.source_excerpt,
    )


def normalize_compare(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")
