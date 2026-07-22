from __future__ import annotations

import re

from app.models.projects import EditPlanRecord, EditPlanScene, ProjectRecord, RenderSpecRecord
from app.services.editorial_labels import completed_selection_target
from app.services.editorial_state_machine import semantic_voice_line
from app.services.scene_beat_enrichment import (
    SceneBeatPlan,
    build_scene_beat_plan,
    enrich_scene_motion,
    launches_product,
)
from app.services.voiceover_pacing import estimated_duration, fit_voice_line
from app.services.walkthrough_voice_style import (
    auth_voiceover_line,
    concise_auth_voiceover,
    concise_level_voiceover,
    concise_selection_voiceover,
    level_voiceover_line,
    polished_auth_transcript,
    polished_selection_transcript,
    selection_voiceover_line,
)


def polish_voiceover_script(project: ProjectRecord, edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes: list[EditPlanScene] = []
    previous_line = ""
    descriptor = product_descriptor(project.product_description, project.product_name)
    for index, scene in enumerate(edit_plan.scenes):
        duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
        context_excerpt = contextual_scene_excerpt(project, edit_plan.scenes, index)
        beat_plan = transcript_beat_plan(scene, context_excerpt, index == 0, duration)
        tuned_scene = tuned_scene_with_beat_plan(scene, duration, beat_plan)
        tuned_duration = max(tuned_scene.render_duration_seconds or duration, 0.8)
        draft = scene_voiceover_line(
            tuned_scene,
            project.product_name,
            descriptor,
            index == 0,
            previous_line,
            tuned_duration,
            context_excerpt,
            beat_plan,
        )
        line = fit_voice_line(duration_safe_scene_line(tuned_scene, draft, tuned_duration, context_excerpt), tuned_duration)
        scenes.append(
            tuned_scene.model_copy(
                update={
                    "spoken_line": line,
                    "show_captions": False,
                    "captions": [],
                }
            )
        )
        previous_line = line
    total_duration = round(sum(scene.render_duration_seconds or max(scene.end - scene.start, 0.8) for scene in scenes), 2)
    return edit_plan.model_copy(
        update={
            "scenes": scenes,
            "total_duration_seconds": total_duration,
            "render_spec": updated_render_spec(edit_plan.render_spec, total_duration),
        }
    )


def duration_safe_scene_line(scene: EditPlanScene, line: str, duration: float, context_excerpt: str = "") -> str:
    if estimated_duration(line) <= max(duration - 0.06, 0.95):
        return line
    semantic_line = semantic_voice_line(scene)
    if semantic_line and estimated_duration(semantic_line) <= max(duration - 0.06, 0.95):
        return semantic_line
    if scene.action_class == "auth_action":
        return concise_auth_voiceover(scene, clean_click_target(scene_target(scene)), context_excerpt)
    if scene.action_class == "card_selection":
        return concise_selection_voiceover(
            premium_selection_target(scene_target(scene)),
            transcript_mentions(scene, "coming soon", "course", "courses"),
        )
    if "level" in scene_title(scene):
        return concise_level_voiceover(scene, context_excerpt)
    return line


def scene_voiceover_line(
    scene: EditPlanScene,
    product_name: str,
    descriptor: str,
    is_first: bool,
    previous_line: str,
    duration: float,
    context_excerpt: str,
    beat_plan: SceneBeatPlan | None = None,
) -> str:
    target = scene_target(scene)
    semantic_line = semantic_voice_line(scene)
    transcript_line = transcript_guided_line(scene, product_name, descriptor, is_first, duration, context_excerpt, beat_plan)
    if transcript_line:
        if is_first and not opener_mentions_target(transcript_line, target):
            transcript_line = launch_intro_line(product_name, descriptor, target)
        return duplicate_safe(previous_line, transcript_line)
    if is_first and should_use_launch_intro(scene, target):
        return launch_intro_line(product_name, descriptor, target)
    if scene.action_class == "auth_action":
        return duplicate_safe(previous_line, auth_voiceover_line(scene, target))
    if scene.action_class == "card_selection":
        return duplicate_safe(
            previous_line,
            selection_voiceover_line(scene, premium_selection_target(target), transcript_mentions(scene, "course", "courses")),
        )
    if semantic_line:
        return duplicate_safe(previous_line, semantic_line)
    if "level" in scene_title(scene):
        return duplicate_safe(previous_line, level_voiceover_line(scene))
    if any(word in scene_title(scene) for word in ("setup", "preferences", "settings", "difficulty")):
        return duplicate_safe(previous_line, "These setup choices shape the experience before the first lesson begins.")
    if scene.scene_role == "result":
        return duplicate_safe(previous_line, "The next product state is now in view, and the walkthrough is ready to continue.")
    return duplicate_safe(previous_line, scene.spoken_line or scene.purpose or scene.title)


def transcript_guided_line(
    scene: EditPlanScene,
    product_name: str,
    descriptor: str,
    is_first: bool,
    duration: float,
    context_excerpt: str,
    beat_plan: SceneBeatPlan | None = None,
) -> str:
    plan = beat_plan or transcript_beat_plan(scene, context_excerpt, is_first, duration)
    beats = list(plan.lines) if plan else []
    if not beats:
        return semantic_voice_line(scene)
    beats = remap_beats_from_scene_states(scene, beats)
    polished = polish_transcript_line(" ".join(beats), scene, product_name, descriptor, is_first, context_excerpt)
    if len(polished.split()) >= 5:
        return polished
    return semantic_voice_line(scene)


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


def polish_transcript_line(
    line: str,
    scene: EditPlanScene,
    product_name: str,
    descriptor: str,
    is_first: bool,
    context_excerpt: str,
) -> str:
    cleaned = normalize_transcript_phrase(line)
    lowered = cleaned.lower()
    target = clean_click_target(scene_target(scene))
    if is_first and launches_product(cleaned):
        launch_line = f"We are launching {product_name}"
        if descriptor:
            launch_line = f"{launch_line}, {descriptor}"
        if "google login" not in lowered:
            return f"{launch_line}. To get started, go to the top right and click {target}."
        auth_line = polished_auth_transcript(cleaned, scene, context_excerpt, target)
        if normalize(auth_line).startswith("to get started"):
            return f"{launch_line}. {auth_line}"
        return f"{launch_line}. To get started, go to the top right and click {target}."
    if scene.action_class == "auth_action":
        return polished_auth_transcript(cleaned, scene, context_excerpt, target)
    if scene.action_class == "card_selection":
        return polished_selection_transcript(cleaned, scene, context_excerpt, premium_selection_target(scene_target(scene)))
    if "level" in scene_title(scene):
        return level_voiceover_line(scene, context_excerpt)
    return cleaned


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


def normalize_transcript_phrase(text: str) -> str:
    cleaned = " ".join(text.split()).strip().rstrip(".")
    replacements = {
        "Hey. ": "",
        "Hey, ": "",
        "So ": "",
        "so ": "",
        "go on the ": "go to the ",
        "Google login button that's on the top right": "the top right Google Login button",
        "Google login button that is on the top right": "the top right Google Login button",
        "Once you click it, just you need to ": "Once you click it, ",
        "Once you click it, you need to ": "Once you click it, ",
        "Since I've already created the account,": "If you already have an account,",
        "After log in,": "After you log in,",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)
    cleaned = re.sub(r"\bjust\s+you\s+need to\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwe\s+give\s+for\s+free\b", "the platform is live", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip().rstrip(".") + "."


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


def launch_intro_line(product_name: str, descriptor: str, target: str) -> str:
    opener = f"This is {product_name}"
    if descriptor:
        opener = f"{opener}, {descriptor}"
    return f"{opener}. To get started, click {clean_click_target(target)}."


def looks_like_probe_metadata(description: str) -> bool:
    lowered = description.lower()
    metadata_tokens = ("probe", "replay", "artifact", "artifacts", "raw upload", "phase four", "local ")
    return sum(token in lowered for token in metadata_tokens) >= 2


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
    if lowered.endswith(" course card"):
        cleaned = cleaned[:-12].strip()
    elif lowered.endswith(" course"):
        cleaned = cleaned[:-7].strip()
    return cleaned or "the next course"


def transcript_beat_plan(scene: EditPlanScene, context_excerpt: str, is_first: bool, duration: float) -> SceneBeatPlan | None:
    return build_scene_beat_plan(
        scene,
        context_excerpt,
        is_first=is_first,
        available_duration=duration,
        target_text=scene_target(scene),
        title_text=scene_title(scene),
    )


def tuned_scene_with_beat_plan(scene: EditPlanScene, duration: float, beat_plan: SceneBeatPlan | None) -> EditPlanScene:
    target_duration = beat_plan.target_duration if beat_plan is not None else duration
    tuned = scene.model_copy(update={"render_duration_seconds": max(duration, target_duration, semantic_duration_floor(scene))})
    if beat_plan is None:
        return tuned
    return enrich_scene_motion(tuned, beat_plan.phase_count)


def contextual_scene_excerpt(project: ProjectRecord, scenes: list[EditPlanScene], index: int) -> str:
    scene = scenes[index]
    excerpts = [scene.source_excerpt]
    guide_step = guide_step_excerpt(project, scene.scene_number)
    if guide_step:
        excerpts.append(guide_step)
    launch_scene = launch_script_excerpt(project, scene.scene_number)
    if launch_scene:
        excerpts.append(launch_scene)
    if same_flow_neighbor(scenes, index - 1, scene):
        excerpts.append(scenes[index - 1].source_excerpt)
    if same_flow_neighbor(scenes, index + 1, scene):
        excerpts.append(scenes[index + 1].source_excerpt)
    return merge_excerpts(excerpts)


def guide_step_excerpt(project: ProjectRecord, scene_number: int) -> str:
    if project.guide is None:
        return ""
    for step in project.guide.steps:
        if step.step_index == scene_number and step.source_excerpt.strip():
            return step.source_excerpt
    return ""


def launch_script_excerpt(project: ProjectRecord, scene_number: int) -> str:
    if project.launch_script is None:
        return ""
    for scene in project.launch_script.scenes:
        if scene.scene_number == scene_number and scene.source_excerpt.strip():
            return scene.source_excerpt
    return ""


def same_flow_neighbor(scenes: list[EditPlanScene], index: int, current: EditPlanScene) -> bool:
    if index < 0 or index >= len(scenes):
        return False
    neighbor = scenes[index]
    if neighbor.scene_number == current.scene_number:
        return False
    shared_auth = neighbor.action_class == current.action_class == "auth_action"
    shared_setup = "level" in scene_title(current) and neighbor.scene_role in {"result", "setup"} and "level" in scene_title(neighbor)
    shared_catalog = current.action_class == neighbor.action_class == "card_selection"
    return shared_auth or shared_setup or shared_catalog


def merge_excerpts(excerpts: list[str]) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for excerpt in excerpts:
        cleaned = " ".join((excerpt or "").split()).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        merged.append(cleaned)
        seen.add(key)
    return " ".join(merged)


def semantic_duration_floor(scene: EditPlanScene) -> float:
    target = scene_target(scene).lower()
    if scene.action_class == "auth_action":
        if "continue with google" in target:
            return 5.8
        return 9.6 if scene.scene_number == 1 else 4.2
    if scene.action_class == "card_selection":
        return 8.6
    if "level" in scene_title(scene):
        return 5.4
    if scene.response_state_kind == "response" and scene.final_destination_label:
        return 3.6
    return 0.8


def remap_beats_from_scene_states(scene: EditPlanScene, beats: list[str]) -> list[str]:
    if not beats:
        return beats
    remapped = beats[:]
    if scene.response_state_kind == "waiting":
        filtered = [beat for beat in remapped if "loading" not in normalize(beat) and "wait" not in normalize(beat)]
        remapped = filtered or remapped[:1]
    if scene.action_target_label and not opener_mentions_target(remapped[0], scene.action_target_label):
        remapped[0] = inject_target_phrase(scene, remapped[0], scene.action_target_label)
    if scene.after_state_label and scene.before_state_label and normalize(scene.after_state_label) != normalize(scene.before_state_label):
        remapped[-1] = inject_outcome_phrase(remapped[-1], scene.final_destination_label or scene.after_state_label)
    return remapped


def inject_outcome_phrase(beat: str, outcome: str) -> str:
    cleaned = " ".join(beat.split()).strip().rstrip(".")
    if not cleaned:
        return f"You'll land on {outcome}."
    lowered = normalize(cleaned)
    if any(marker in lowered for marker in ("you'll see", "you can see", "land on", "opens", "open", "ready")):
        return cleaned if cleaned.endswith(".") else f"{cleaned}."
    return f"{cleaned}, and you'll land on {outcome}."


def inject_target_phrase(scene: EditPlanScene, beat: str, target: str) -> str:
    cleaned = " ".join(beat.split()).strip().rstrip(".")
    target_clean = clean_click_target(target)
    verb = target_intro_verb(scene)
    if not cleaned:
        return f"{verb.capitalize()} {target_clean}."
    if normalize(target_clean) in normalize(cleaned):
        return cleaned if cleaned.endswith(".") else f"{cleaned}."
    starter = cleaned[0].lower() + cleaned[1:] if len(cleaned) > 1 else cleaned.lower()
    return f"{verb.capitalize()} {target_clean}, then {starter}."


def target_intro_verb(scene: EditPlanScene) -> str:
    if scene.action_class in {"auth_action", "button_click"}:
        return "click"
    if scene.action_class == "card_selection":
        return "choose"
    if scene.action_class in {"navigation", "tab_switch"}:
        return "open"
    if scene.action_class == "focus":
        return "focus on"
    if scene.scene_role == "result":
        return "view"
    return "open"


def updated_render_spec(render_spec: RenderSpecRecord, total_duration: float) -> RenderSpecRecord:
    return render_spec.model_copy(update={"total_duration_seconds": total_duration})


def normalize(text: str) -> str:
    return " ".join((text or "").lower().split()).strip().rstrip(".")


def opener_mentions_target(line: str, target: str) -> bool:
    normalized_line = normalize(line)
    normalized_target = normalize(clean_click_target(target))
    if not normalized_target:
        return False
    if normalized_target in normalized_line:
        return True
    target_tokens = meaningful_tokens(normalized_target)
    line_tokens = meaningful_tokens(normalized_line)
    if not target_tokens or not line_tokens:
        return False
    shared = target_tokens & line_tokens
    if len(shared) >= min(2, len(target_tokens)):
        return True
    return "top right" in normalized_line and bool(shared)


def meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in {"the", "a", "an", "button", "cta", "action", "to", "with"}
    }


def repeated_launch_phrase(text: str) -> bool:
    lowered = normalize(text)
    return lowered.count("we are launching") > 1 or lowered.count("we're launching") > 1


def strip_repeated_launch_phrase(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    duplicate = "We are launching"
    if duplicate not in cleaned:
        return cleaned
    first, _, remainder = cleaned.partition(duplicate)
    fallback = f"{first.strip().rstrip(',')}. {remainder.strip()}".replace("..", ".")
    return " ".join(fallback.split()).strip()
