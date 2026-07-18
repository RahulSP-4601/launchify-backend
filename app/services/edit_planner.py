from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.models.projects import (
    EditPlanRecord,
    EditPlanScene,
    LaunchScriptScene,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    RenderSpecRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.caption_designer import build_caption_track
from app.services.motion_director import build_motion_track
from app.services.override_manager import apply_manual_overrides
from app.services.session_grounding import apply_session_grounding
from app.services.scene_alignment import align_script_scenes
from app.services.timing_sync import sync_edit_plan_timing
from app.services.visual_analysis import analysis_map
from app.services.visual_policy import build_scene_policy


def generate_edit_plan(
    project: ProjectRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    launch_script = require_launch_script(project.launch_script)
    require_scene_plan(launch_script)
    scene_ranges = align_script_scenes(launch_script.scenes, project.transcript)
    analyses_by_scene = analysis_map(visual_analyses or [])
    max_workers = min(max(get_settings().visual_analysis_concurrency, 1), max(len(launch_script.scenes), 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        planned_scenes = list(
            executor.map(
                lambda scene_inputs: build_edit_scene(
                    scene_inputs[0],
                    scene_inputs[1][0],
                    scene_inputs[1][1],
                    project,
                    analyses_by_scene.get(scene_inputs[0].scene_number),
                ),
                zip(launch_script.scenes, scene_ranges, strict=True),
            )
        )
    total_duration = round(max((scene.end for scene in planned_scenes), default=0.0), 2)
    planned_edit = EditPlanRecord(
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
    synced_edit = sync_edit_plan_timing(planned_edit, visual_analyses)
    grounded_edit = apply_session_grounding(synced_edit, project.recording_session)
    return apply_manual_overrides(grounded_edit, normalized_overrides(project.manual_overrides))


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
    project: ProjectRecord,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> EditPlanScene:
    transcript_slice = slice_transcript(project.transcript, start, end)
    policy = build_scene_policy(scene, transcript_slice, visual_analysis)
    captions = build_caption_track(transcript_slice, start, end, project.template_config)
    zooms, highlights = build_motion_track(scene, start, end, policy, project.template_config)
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
        action_timestamp=None,
        transition_style="fade",
        transition_duration_seconds=0.32,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )


def slice_transcript(
    transcript: list[TranscriptSegment],
    start: float,
    end: float,
) -> list[TranscriptSegment]:
    return [segment for segment in transcript if segment.end >= start and segment.start <= end]


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


def normalized_overrides(manual_overrides: ManualOverrideRecord | None) -> ManualOverrideRecord | None:
    if manual_overrides is None:
        return None
    return manual_overrides
