from __future__ import annotations

from app.models.projects import EditPlanCaption, EditPlanRecord, EditPlanScene
from app.services.voiceover_pacing import fit_voice_line
from app.services.walkthrough_narration import scene_voice_line


def apply_scene_copy_refinement(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [refine_scene_copy(scene) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def refine_scene_copy(scene: EditPlanScene) -> EditPlanScene:
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    purpose = refined_purpose(scene)
    on_screen_text = refined_on_screen_text(scene, purpose)
    refined_scene = scene.model_copy(update={"purpose": purpose, "on_screen_text": on_screen_text})
    spoken_line = fit_voice_line(scene_voice_line(refined_scene), duration)
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
    short = " ".join(words[:10]).strip()
    if len(words) > 10 and not short.endswith(("...", ".", "!", "?")):
        short = f"{short}..."
    return short[:76]


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
