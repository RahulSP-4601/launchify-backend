from __future__ import annotations

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
    VisualSceneAnalysisRecord,
)
from app.services.scene_alignment import align_script_scenes
from app.services.visual_analysis import analysis_map
from app.services.visual_policy import ScenePolicy, build_scene_policy

CAPTION_MAX_CHARACTERS = 72


def generate_edit_plan(
    project: ProjectRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    launch_script = require_launch_script(project.launch_script)
    require_scene_plan(launch_script)
    scene_ranges = align_script_scenes(launch_script.scenes, project.transcript)
    analyses_by_scene = analysis_map(visual_analyses or [])
    planned_scenes = [
        build_edit_scene(scene, scene_range[0], scene_range[1], project.transcript, analyses_by_scene.get(scene.scene_number))
        for scene, scene_range in zip(launch_script.scenes, scene_ranges, strict=True)
    ]
    total_duration = round(max((scene.end for scene in planned_scenes), default=0.0), 2)
    return EditPlanRecord(
        overview=build_overview(project, launch_script, bool(visual_analyses)),
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


def build_edit_scene(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    transcript: list[TranscriptSegment],
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> EditPlanScene:
    transcript_slice = slice_transcript(transcript, start, end)
    policy = build_scene_policy(scene, transcript_slice, visual_analysis)
    return EditPlanScene(
        scene_number=scene.scene_number,
        title=f"Scene {scene.scene_number}",
        purpose=scene.purpose,
        start=start,
        end=end,
        confidence=policy.scene_confidence,
        camera_mode=policy.camera_mode,
        decision_summary=policy.decision_summary,
        visual_summary=policy.visual_summary,
        spoken_line=scene.spoken_line,
        on_screen_text=scene.on_screen_text,
        source_excerpt=scene.source_excerpt,
        captions=build_captions(transcript_slice, start, end),
        zooms=build_zooms(start, end, policy),
        highlights=build_highlights(scene, transcript_slice, start, end, policy),
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
        current_start, current_end, current_text = append_caption_segment(
            captions,
            current_start,
            current_end,
            current_text,
            segment,
        )
    if current_text:
        captions.append(caption_record(current_start, current_end, current_text))
    return captions


def append_caption_segment(
    captions: list[EditPlanCaption],
    current_start: float,
    current_end: float,
    current_text: list[str],
    segment: TranscriptSegment,
) -> tuple[float, float, list[str]]:
    segment_text = segment.text.strip()
    candidate = " ".join([*current_text, segment_text]).strip()
    if current_text and len(candidate) > CAPTION_MAX_CHARACTERS:
        captions.append(caption_record(current_start, current_end, current_text))
        return segment.start, segment.end, [segment_text]
    return current_start, segment.end, [*current_text, segment_text]


def caption_record(start: float, end: float, text_parts: list[str]) -> EditPlanCaption:
    return EditPlanCaption(start=round(start, 2), end=round(end, 2), text=" ".join(text_parts))


def build_zooms(start: float, end: float, policy: ScenePolicy) -> list[EditPlanZoom]:
    duration = max(end - start, 0.5)
    if not policy.should_zoom:
        return []
    midpoint = round(start + duration * 0.18, 2)
    return [
        EditPlanZoom(
            start=midpoint,
            end=round(min(end, midpoint + duration * 0.62), 2),
            scale=1.14 if policy.focus_region == "center" else 1.2,
            focus_region=policy.focus_region,
            reason="Confidence-approved focus move around the strongest UI action.",
            confidence=policy.zoom_confidence,
            focus_box=policy.focus_box,
        )
    ]


def build_highlights(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    start: float,
    end: float,
    policy: ScenePolicy,
) -> list[EditPlanHighlight]:
    if not policy.should_highlight:
        return []
    label = scene.on_screen_text.strip() or scene.purpose.strip()
    marker_start = transcript[0].start if transcript else start
    marker_end = min(end, marker_start + 1.6)
    focus_box = policy.click_target_box or policy.cursor_box or policy.focus_box
    return [
        EditPlanHighlight(
            start=round(marker_start, 2),
            end=round(marker_end, 2),
            label=label[:80],
            style=policy.highlight_style,
            anchor_region=policy.anchor_region,
            confidence=policy.highlight_confidence,
            focus_box=focus_box,
        )
    ]


def build_overview(
    project: ProjectRecord,
    launch_script: LaunchScriptRecord,
    used_visual_analysis: bool,
) -> str:
    audience = project.target_audience or "the intended product audience"
    visual_note = "frame-level focus analysis" if used_visual_analysis else "script-led motion planning"
    return (
        f"Launchify tightened the recording for {audience}, aligned {len(launch_script.scenes)} scenes "
        f"to the source walkthrough, and prepared captions, zooms, and highlights using {visual_note}."
    )
