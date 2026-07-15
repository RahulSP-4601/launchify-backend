from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models.projects import (
    EditPlanCaption,
    EditPlanHighlight,
    EditPlanRecord,
    EditPlanScene,
    EditPlanZoom,
    LaunchScriptScene,
    LaunchScriptRecord,
    ProjectRecord,
    RenderSpecRecord,
    TranscriptSegment,
)

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
CAPTION_MAX_CHARACTERS = 72
ACTION_KEYWORDS = ("click", "select", "open", "choose", "toggle", "save", "create", "edit", "invite")


def generate_edit_plan(project: ProjectRecord) -> EditPlanRecord:
    launch_script = require_launch_script(project.launch_script)
    require_scene_plan(launch_script)
    transcript = project.transcript
    scene_ranges = align_script_scenes(launch_script.scenes, transcript)
    planned_scenes = [
        build_edit_scene(script_scene, scene_range[0], scene_range[1], transcript)
        for script_scene, scene_range in zip(launch_script.scenes, scene_ranges, strict=True)
    ]
    total_duration = round(max((scene.end for scene in planned_scenes), default=0.0), 2)
    return EditPlanRecord(
        overview=build_overview(project, launch_script),
        total_duration_seconds=total_duration,
        scenes=planned_scenes,
        render_spec=RenderSpecRecord(
            title_card=launch_script.hook,
            title_options=launch_script.title_options,
            cta=launch_script.cta,
            total_duration_seconds=total_duration,
        ),
    )


def require_launch_script(launch_script: LaunchScriptRecord | None) -> LaunchScriptRecord:
    if launch_script is None:
        raise RuntimeError("Launch script is required before generating the edit plan.")
    return launch_script


def require_scene_plan(launch_script: LaunchScriptRecord) -> None:
    if not launch_script.scenes:
        raise RuntimeError("OpenAI returned a launch script without any scenes to plan.")


def align_script_scenes(
    scenes: list[LaunchScriptScene],
    transcript: list[TranscriptSegment],
) -> list[tuple[float, float]]:
    if not transcript:
        raise RuntimeError("Transcript is required before generating the edit plan.")
    ranges: list[tuple[float, float]] = []
    search_start = 0
    for scene in scenes:
        start_index, end_index = best_window_for_scene(scene, transcript, search_start)
        ranges.append((transcript[start_index].start, transcript[end_index].end))
        search_start = min(end_index + 1, len(transcript) - 1)
    return normalize_ranges(ranges, transcript[-1].end)


def best_window_for_scene(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    search_start: int,
) -> tuple[int, int]:
    desired_segments = max(1, round(scene.estimated_duration_seconds / 4))
    max_window = min(max(2, desired_segments + 1), 5)
    scene_text = scene_text_for_matching(scene)
    best_score = -1.0
    best_range = (search_start, search_start)
    for start in range(search_start, len(transcript)):
        for window_size in range(1, max_window + 1):
            end = start + window_size - 1
            if end >= len(transcript):
                break
            score = score_window(scene_text, transcript[start : end + 1])
            if score > best_score:
                best_score = score
                best_range = (start, end)
    return best_range


def scene_text_for_matching(scene: LaunchScriptScene) -> str:
    return " ".join(part for part in (scene.spoken_line, scene.source_excerpt, scene.on_screen_text) if part.strip())


def score_window(scene_text: str, window: list[TranscriptSegment]) -> float:
    window_text = " ".join(segment.text for segment in window)
    token_score = overlap_score(scene_text, window_text)
    sequence_score = SequenceMatcher(a=scene_text.lower(), b=window_text.lower()).ratio()
    return token_score * 0.7 + sequence_score * 0.3


def overlap_score(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    common = len(left_tokens & right_tokens)
    return common / max(len(left_tokens), 1)


def tokenize(value: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(value.lower()) if len(token) > 1}


def normalize_ranges(ranges: list[tuple[float, float]], max_end: float) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    previous_end = 0.0
    for start, end in ranges:
        bounded_start = max(previous_end, round(start, 2))
        bounded_end = max(bounded_start + 0.25, round(min(end, max_end), 2))
        normalized.append((bounded_start, bounded_end))
        previous_end = bounded_end
    return normalized


def build_edit_scene(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    transcript: list[TranscriptSegment],
) -> EditPlanScene:
    transcript_slice = slice_transcript(transcript, start, end)
    return EditPlanScene(
        scene_number=scene.scene_number,
        title=f"Scene {scene.scene_number}",
        purpose=scene.purpose,
        start=start,
        end=end,
        spoken_line=scene.spoken_line,
        on_screen_text=scene.on_screen_text,
        source_excerpt=scene.source_excerpt,
        captions=build_captions(transcript_slice, start, end),
        zooms=build_zooms(scene, start, end),
        highlights=build_highlights(scene, transcript_slice, start, end),
    )


def slice_transcript(transcript: list[TranscriptSegment], start: float, end: float) -> list[TranscriptSegment]:
    return [segment for segment in transcript if segment.end >= start and segment.start <= end]


def build_captions(
    transcript: list[TranscriptSegment],
    start: float,
    end: float,
) -> list[EditPlanCaption]:
    if not transcript:
        return [EditPlanCaption(start=start, end=end, text="Narration begins here.")]
    captions: list[EditPlanCaption] = []
    current_text: list[str] = []
    current_start = transcript[0].start
    current_end = transcript[0].end
    for segment in transcript:
        candidate = " ".join([*current_text, segment.text.strip()]).strip()
        if current_text and len(candidate) > CAPTION_MAX_CHARACTERS:
            captions.append(EditPlanCaption(start=round(current_start, 2), end=round(current_end, 2), text=" ".join(current_text)))
            current_text = [segment.text.strip()]
            current_start = segment.start
            current_end = segment.end
            continue
        current_text.append(segment.text.strip())
        current_end = segment.end
    if current_text:
        captions.append(EditPlanCaption(start=round(current_start, 2), end=round(current_end, 2), text=" ".join(current_text)))
    return captions


def build_zooms(scene: LaunchScriptScene, start: float, end: float) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    if not should_zoom(scene):
        return []
    focus_region = infer_focus_region(scene)
    midpoint = round(start + duration * 0.18, 2)
    return [
        EditPlanZoom(
            start=midpoint,
            end=round(min(end, midpoint + duration * 0.62), 2),
            scale=1.18 if focus_region == "center" else 1.24,
            focus_region=focus_region,
            reason="Auto-zoom around the primary UI action in this scene.",
        )
    ]


def should_zoom(scene: LaunchScriptScene) -> bool:
    haystack = f"{scene.purpose} {scene.spoken_line} {scene.on_screen_text}".lower()
    return any(keyword in haystack for keyword in ACTION_KEYWORDS)


def infer_focus_region(scene: LaunchScriptScene) -> str:
    text = f"{scene.on_screen_text} {scene.spoken_line}".lower()
    if "settings" in text or "profile" in text:
        return "top-right"
    if "create" in text or "new" in text or "add" in text:
        return "top-left"
    if "search" in text or "filter" in text:
        return "top-center"
    if "save" in text or "publish" in text or "continue" in text:
        return "bottom-right"
    return "center"


def build_highlights(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    start: float,
    end: float,
) -> list[EditPlanHighlight]:
    if not should_zoom(scene):
        return []
    label = scene.on_screen_text.strip() or scene.purpose.strip()
    marker_start = transcript[0].start if transcript else start
    marker_end = min(end, marker_start + 1.6)
    return [
        EditPlanHighlight(
            start=round(marker_start, 2),
            end=round(marker_end, 2),
            label=label[:80],
            style=infer_highlight_style(scene),
        )
    ]


def infer_highlight_style(scene: LaunchScriptScene) -> str:
    text = f"{scene.purpose} {scene.spoken_line}".lower()
    if "click" in text or "select" in text:
        return "pulse-ring"
    if "review" in text or "notice" in text:
        return "spotlight"
    return "outline"


def build_overview(project: ProjectRecord, launch_script: LaunchScriptRecord) -> str:
    audience = project.target_audience or "the intended product audience"
    return (
        f"Launchify tightened the recording for {audience}, aligned {len(launch_script.scenes)} scenes "
        f"to the source walkthrough, and prepared captions, zooms, and highlights for rendering."
    )
