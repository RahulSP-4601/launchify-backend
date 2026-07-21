from __future__ import annotations

from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene
from app.services.editorial_labels import completed_selection_target
from app.services.editorial_flow import AUTH_FAMILY, CONFIG_FAMILY, FlowSceneContext, SELECTION_FAMILY, scene_contexts


def repair_editorial_sequence(edit_plan: EditPlanRecord) -> EditPlanRecord:
    contexts = scene_contexts(edit_plan.scenes)
    scenes: list[EditPlanScene] = []
    previous_scene: EditPlanScene | None = None
    for scene in edit_plan.scenes:
        repaired = repair_scene(scene, contexts.get(scene.scene_number), previous_scene)
        scenes.append(repaired)
        previous_scene = repaired
    total_duration = round(sum(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8) for scene in scenes), 2)
    return edit_plan.model_copy(
        update={
            "scenes": scenes,
            "total_duration_seconds": total_duration,
            "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": total_duration}),
        }
    )


def repair_scene(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
    previous_scene: EditPlanScene | None,
) -> EditPlanScene:
    spoken_line = repaired_spoken_line(scene, context, previous_scene)
    captions = repaired_captions(scene, spoken_line)
    scene_end = repaired_scene_end(scene, context)
    render_duration = round(max(scene_end - scene.start, 0.8), 2)
    layout_mode = repaired_layout_mode(scene, context)
    return scene.model_copy(
        update={
            "spoken_line": spoken_line,
            "captions": captions,
            "end": scene_end,
            "render_duration_seconds": render_duration,
            "highlights": repaired_highlights(scene, context),
            "layout_mode": layout_mode,
        }
    )


def repaired_spoken_line(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
    previous_scene: EditPlanScene | None,
) -> str:
    line = scene.spoken_line.strip()
    if context is None:
        return line
    if context.family == AUTH_FAMILY:
        if context.is_first:
            line = f"Click {action_target(scene)} to get started."
        elif "continue with" in normalize(scene.on_screen_text):
            line = f"Continue with {action_target(scene, drop_continue=True)} to sign in quickly."
        else:
            line = f"Choose {action_target(scene)} to keep sign-in moving."
    elif context.family == SELECTION_FAMILY:
        line = (
            f"Open {selection_target(scene)} {selection_outcome_phrase(scene, context)}."
            if context.next_scene is not None
            else f"Open {selection_target(scene)} to continue."
        )
    elif context.family == CONFIG_FAMILY:
        line = configuration_line(scene, context)
    if previous_scene is not None and normalize(previous_scene.spoken_line) == normalize(line):
        line = diversified_line(scene, context)
    return line


def diversified_line(scene: EditPlanScene, context: FlowSceneContext) -> str:
    if context.family == AUTH_FAMILY:
        if "continue with" in normalize(scene.on_screen_text):
            return f"Use Continue with {action_target(scene, drop_continue=True)} to finish signing in."
        return "Finish the sign-in step to enter the product."
    if context.family == SELECTION_FAMILY:
        return f"Choose {selection_target(scene)} {selection_outcome_phrase(scene, context)}."
    if context.family == CONFIG_FAMILY:
        return configuration_line(scene, context)
    return "Continue to the next step."


def repaired_captions(scene: EditPlanScene, spoken_line: str) -> list[EditPlanCaption]:
    if not scene.show_captions:
        return []
    text = balanced_caption(spoken_line)
    if scene.captions:
        first = scene.captions[0]
        return [first.model_copy(update={"text": text, "start": round(scene.start, 2), "end": round(scene.end, 2), "variant": "minimal"})]
    return [EditPlanCaption(start=round(scene.start, 2), end=round(scene.end, 2), text=text, emphasis_words=[], variant="minimal")]


def repaired_scene_end(scene: EditPlanScene, context: FlowSceneContext | None) -> float:
    if context is None or context.next_scene is None:
        return round(scene.end, 2)
    base_end = scene.end
    anchor = scene.result_anchor_timestamp or scene.action_timestamp or scene.end
    next_gap = max(context.next_scene.start - scene.end, 0.0)
    if context.family == AUTH_FAMILY and context.is_first:
        target = max(base_end, anchor + 3.2 + scene.readable_hold_seconds * 0.25)
        bridge = min(next_gap * 0.35, 1.6)
        return bounded_scene_end(scene, min(base_end + 3.6, target + bridge), context.next_scene.start)
    if context.family == AUTH_FAMILY:
        target = max(base_end, anchor + 2.2 + scene.readable_hold_seconds * 0.2)
        return bounded_scene_end(scene, min(base_end + 0.9, target), context.next_scene.start)
    if context.family == SELECTION_FAMILY:
        target = max(base_end, anchor + 3.0 + scene.readable_hold_seconds * 0.35)
        bridge = min(next_gap * 0.4, 2.2)
        return bounded_scene_end(scene, min(base_end + 3.2, target + bridge), context.next_scene.start)
    if context.family == CONFIG_FAMILY:
        target = max(base_end, anchor + 2.1 + scene.readable_hold_seconds * 0.25)
        return bounded_scene_end(scene, min(base_end + 1.2, target), context.next_scene.start)
    return bounded_scene_end(scene, base_end, context.next_scene.start)


def balanced_caption(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    words = cleaned.split()
    if len(words) <= 5:
        return cleaned
    midpoint = min(max(len(words) // 2, 3), 6)
    first = " ".join(words[:midpoint]).strip()
    second = " ".join(words[midpoint:]).strip()
    return f"{first}\n{second}".strip()


def normalize(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")


def repaired_highlights(
    scene: EditPlanScene,
    context: FlowSceneContext | None,
) -> list[EditPlanHighlight]:
    highlights = scene.highlights
    if not highlights:
        if context is not None and context.family == CONFIG_FAMILY and scene.layout_mode == "screen-only" and scene.zooms and scene.zooms[0].focus_box is not None:
            fallback = scene.zooms[0]
            return [
                EditPlanHighlight(
                    start=round(scene.focus_start_timestamp or scene.start, 2),
                    end=round(min(scene.focus_end_timestamp or scene.end, scene.start + 0.9), 2),
                    label=(scene.on_screen_text or scene.title)[:48],
                    style="soft-glow",
                    anchor_region=fallback.focus_region,
                    confidence=0.8,
                    focus_box=fallback.focus_box,
                    placement_preference="avoid-ui-cover",
                    ui_label=scene.on_screen_text or scene.title,
                )
            ]
        return []
    limit = highlight_limit_seconds(context)
    repaired: list[EditPlanHighlight] = []
    for highlight in highlights:
        start = round(highlight.start, 2)
        end = round(min(highlight.end, start + limit), 2)
        repaired.append(highlight.model_copy(update={"start": start, "end": max(end, round(start + 0.35, 2))}))
    return repaired


def highlight_limit_seconds(context: FlowSceneContext | None) -> float:
    if context is None:
        return 1.2
    if context.family == AUTH_FAMILY:
        return 1.35
    if context.family == SELECTION_FAMILY:
        return 1.25
    if context.family == CONFIG_FAMILY:
        return 1.1
    return 1.2


def repaired_layout_mode(scene: EditPlanScene, context: FlowSceneContext | None) -> str:
    if context is None:
        return scene.layout_mode
    if context.family == CONFIG_FAMILY and setup_like(scene):
        return "screen-only" if is_stable_setup_scene(scene) else "feature-center"
    return scene.layout_mode


def configuration_line(scene: EditPlanScene, context: FlowSceneContext) -> str:
    target = configuration_target(scene)
    if target:
        return f"Choose {target} before you begin."
    if context.next_scene is None:
        return "Choose the starting setup before you begin."
    return "Set the starting options before continuing."


def action_target(scene: EditPlanScene, *, drop_continue: bool = False) -> str:
    source = scene.on_screen_text or scene.title or "the sign-in option"
    normalized_target = source.strip().rstrip(".")
    lowered = normalized_target.lower()
    if drop_continue and lowered.startswith("continue with "):
        normalized_target = normalized_target[14:].strip()
    return normalized_target


def selection_target(scene: EditPlanScene) -> str:
    if scene.specific_target_label.strip():
        return completed_selection_target(
            specific_target_label=scene.specific_target_label,
            canonical_label=scene.title or scene.on_screen_text,
            transcript_excerpt=scene.source_excerpt,
        )
    source = scene.on_screen_text or scene.title or "the selected option"
    lowered = source.lower()
    if lowered.startswith("select "):
        source = source[7:].strip()
    if lowered.startswith("choose "):
        source = source[7:].strip()
    normalized_source = normalize(source)
    if normalized_source in {"a course", "course", "selected course", "an option", "option", "selected option"}:
        return completed_selection_target(
            specific_target_label="",
            canonical_label=source,
            transcript_excerpt=scene.source_excerpt,
        )
    return source.rstrip(".")


def selection_outcome_phrase(scene: EditPlanScene, context: FlowSceneContext) -> str:
    next_scene = context.next_scene
    if next_scene is None:
        return "to continue"
    combined = normalize(f"{next_scene.title} {next_scene.on_screen_text} {next_scene.purpose} {next_scene.source_excerpt}")
    if any(token in combined for token in ("level", "difficulty", "pick your", "choose your")):
        return "to choose your level next"
    if any(token in combined for token in ("setup", "preferences", "settings", "workspace", "role", "template", "plan")):
        return "to start setup"
    if "dashboard" in combined:
        return "to open the dashboard"
    return "to open the next step"


def configuration_target(scene: EditPlanScene) -> str:
    source = (scene.title or scene.on_screen_text or "").strip().rstrip(".")
    lowered = source.lower()
    prefixes = ("choose ", "select ", "pick ", "set ")
    for prefix in prefixes:
        if lowered.startswith(prefix):
            source = source[len(prefix) :].strip()
            lowered = source.lower()
            break
    if lowered.startswith("your "):
        return source
    if source:
        return lower_leading_word(source)
    return "the setup option"


def setup_like(scene: EditPlanScene) -> bool:
    combined = f"{scene.title} {scene.on_screen_text} {scene.purpose}".lower()
    return any(token in combined for token in ("level", "settings", "preferences", "plan", "workspace", "role", "template", "setup"))


def is_stable_setup_scene(scene: EditPlanScene) -> bool:
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    return duration >= 3.0 or scene.scene_role != "action"


def bounded_scene_end(scene: EditPlanScene, candidate_end: float, next_scene_start: float) -> float:
    if next_scene_start <= scene.start:
        return round(scene.start, 2)
    latest_safe_end = max(scene.start, next_scene_start - 0.01)
    return round(min(candidate_end, latest_safe_end), 2)


def lower_leading_word(text: str) -> str:
    words = text.split()
    if not words:
        return text
    return " ".join([words[0].lower(), *words[1:]]).strip()
