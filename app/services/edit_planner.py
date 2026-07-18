from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.models.projects import (
    EditPlanCaption,
    EditPlanHighlight,
    EditPlanRecord,
    EditPlanScene,
    EditPlanZoom,
    FocusBox,
    GuideRecord,
    GuideStepRecord,
    LaunchScriptScene,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    RenderSpecRecord,
    SessionEventRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.caption_designer import build_caption_track
from app.services.event_grounding import focus_box_for_event, normalize_event_timestamp, primary_event_for_window, region_for_box
from app.services.motion_director import build_motion_track
from app.services.override_manager import apply_manual_overrides
from app.services.session_grounding import apply_session_grounding
from app.services.scene_alignment import align_script_scenes
from app.services.timing_sync import sync_edit_plan_timing
from app.services.visual_analysis import analysis_map
from app.services.visual_policy import ScenePolicy, build_scene_policy


def generate_edit_plan(
    project: ProjectRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    if project.guide is not None and project.guide.steps:
        return generate_grounded_edit_plan(project)
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


def generate_grounded_edit_plan(project: ProjectRecord) -> EditPlanRecord:
    guide = require_guide(project.guide)
    analyses_by_scene = analysis_map([])
    planned_scenes = [
        build_grounded_scene(step, project, analyses_by_scene.get(step.step_index))
        for step in guide.steps
    ]
    total_duration = round(max((scene.end for scene in planned_scenes), default=0.0), 2)
    edit_plan = EditPlanRecord(
        overview=build_grounded_overview(project, guide),
        total_duration_seconds=total_duration,
        scenes=planned_scenes,
        render_spec=RenderSpecRecord(
            title_card=guide.title,
            title_options=[guide.title],
            cta="Turn rough recordings into polished launch videos.",
            total_duration_seconds=total_duration,
        ),
    )
    return apply_manual_overrides(edit_plan, normalized_overrides(project.manual_overrides))


def require_launch_script(launch_script: LaunchScriptRecord | None) -> LaunchScriptRecord:
    if launch_script is None:
        raise RuntimeError("Launch script is required before generating the edit plan.")
    return launch_script


def require_scene_plan(launch_script: LaunchScriptRecord) -> None:
    if not launch_script.scenes:
        raise RuntimeError("OpenAI returned a launch script without any scenes to plan.")


def require_guide(guide: GuideRecord | None) -> GuideRecord:
    if guide is None or not guide.steps:
        raise RuntimeError("Grounded guide is required before generating the grounded edit plan.")
    return guide


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


def build_grounded_scene(
    step: GuideStepRecord,
    project: ProjectRecord,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> EditPlanScene:
    start = round(step.start, 2)
    end = round(max(step.end, start + 0.8), 2)
    transcript_slice = slice_transcript(project.transcript, start, end)
    primary_event = primary_event_for_window(project.recording_session, start, end, step.focus_label or step.title)
    synthetic_scene = LaunchScriptScene(
        scene_number=step.step_index,
        purpose=step.instruction,
        spoken_line=step.narration,
        on_screen_text=step.on_screen_text,
        source_excerpt=step.source_excerpt or step.focus_label or step.title,
        estimated_duration_seconds=max(end - start, 0.8),
    )
    policy, captions, zooms, highlights = grounded_motion_assets(
        project, step, synthetic_scene, transcript_slice, start, end, visual_analysis, primary_event,
    )
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else start
    return EditPlanScene(
        scene_number=step.step_index,
        title=step.title or f"Step {step.step_index}",
        purpose=step.instruction,
        start=start,
        end=end,
        confidence=max(policy.scene_confidence, 0.82 if primary_event is not None else 0.78),
        camera_mode="focus" if step.focus_selector or step.focus_label or primary_event is not None else policy.camera_mode,
        decision_summary=grounded_decision_summary(step, primary_event),
        visual_summary=grounded_visual_summary(step, primary_event, policy.visual_summary),
        spoken_line=step.narration,
        on_screen_text=step.on_screen_text,
        source_excerpt=step.source_excerpt or step.focus_label or step.title,
        action_timestamp=event_time,
        transition_style="focus-push",
        transition_duration_seconds=0.24,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )


def grounded_motion_assets(
    project: ProjectRecord,
    step: GuideStepRecord,
    synthetic_scene: LaunchScriptScene,
    transcript_slice: list[TranscriptSegment],
    start: float,
    end: float,
    visual_analysis: VisualSceneAnalysisRecord | None,
    primary_event: SessionEventRecord | None,
) -> tuple[ScenePolicy, list[EditPlanCaption], list[EditPlanZoom], list[EditPlanHighlight]]:
    policy = build_scene_policy(synthetic_scene, transcript_slice, visual_analysis)
    captions = build_caption_track(
        transcript_slice or [TranscriptSegment(start=start, end=end, text=step.narration)],
        start,
        end,
        project.template_config,
    )
    zooms, highlights = build_motion_track(synthetic_scene, start, end, policy, project.template_config)
    return policy, captions, *apply_grounded_focus(project, step, primary_event, zooms, highlights)


def apply_grounded_focus(
    project: ProjectRecord,
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
) -> tuple[list[EditPlanZoom], list[EditPlanHighlight]]:
    event_focus_box = focus_box_for_event(project.recording_session, primary_event)
    if event_focus_box is None:
        return zooms, highlights
    focus_region = region_for_box(event_focus_box)
    grounded_zooms = [hydrate_grounded_zoom(zoom, event_focus_box, focus_region) for zoom in zooms]
    grounded_highlights = [hydrate_grounded_highlight(highlight, step, event_focus_box, focus_region) for highlight in highlights]
    if not grounded_zooms:
        grounded_zooms = [seed_grounded_zoom(step, event_focus_box, focus_region)]
    if not grounded_highlights:
        grounded_highlights = [seed_grounded_highlight(step, event_focus_box, focus_region)]
    return grounded_zooms, grounded_highlights


def hydrate_grounded_zoom(zoom: EditPlanZoom, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    return zoom.model_copy(update={
        "focus_box": zoom.focus_box or event_focus_box,
        "focus_region": focus_region if zoom.focus_region == "center" else zoom.focus_region,
        "confidence": max(zoom.confidence, 0.82),
        "scale": max(zoom.scale, 1.16),
    })


def hydrate_grounded_highlight(
    highlight: EditPlanHighlight,
    step: GuideStepRecord,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    return highlight.model_copy(update={
        "focus_box": highlight.focus_box or event_focus_box,
        "anchor_region": focus_region if highlight.anchor_region == "center" else highlight.anchor_region,
        "confidence": max(highlight.confidence, 0.84),
        "ui_label": step.highlight_label or step.focus_label or highlight.ui_label,
        "label": step.highlight_label or highlight.label,
    })


def seed_grounded_zoom(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    return EditPlanZoom(
        start=step.start,
        end=step.end,
        scale=1.18,
        focus_region=focus_region,
        reason="grounded session focus",
        confidence=0.86,
        focus_box=event_focus_box,
        hold_ratio=0.68,
        smoothing=0.14,
    )


def seed_grounded_highlight(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanHighlight:
    return EditPlanHighlight(
        start=step.start,
        end=step.end,
        label=step.highlight_label or step.focus_label or step.title,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.88,
        focus_box=event_focus_box,
        ui_label=step.highlight_label or step.focus_label or step.title,
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


def build_grounded_overview(project: ProjectRecord, guide: GuideRecord) -> str:
    audience = project.target_audience or "the intended product audience"
    return (
        f"Launchify grounded {len(guide.steps)} captured product actions for {audience}, "
        "turned them into synchronized steps, and prepared captions, zooms, and highlights from the event-backed guide."
    )


def grounded_decision_summary(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> str:
    if primary_event is None:
        return f"Grounded from synthesized step timing around {step.focus_label or step.focus_selector or 'the active element'}."
    label = primary_event.target.label or primary_event.target.text or primary_event.target.selector
    return (
        f"Grounded from captured {primary_event.type} event near {label or 'the active element'} "
        f"at {normalize_event_timestamp(primary_event.timestamp):.2f}s with event-led camera timing."
    )


def grounded_visual_summary(step: GuideStepRecord, primary_event: SessionEventRecord | None, fallback: str) -> str:
    if primary_event is None:
        return fallback or f"Focus attention on {step.focus_label or step.focus_selector or 'the active control'}."
    label = primary_event.target.label or primary_event.target.text or primary_event.target.selector
    return f"Spotlight the real UI action around {label or 'the active control'} and keep surrounding context subdued."


def normalized_overrides(manual_overrides: ManualOverrideRecord | None) -> ManualOverrideRecord | None:
    if manual_overrides is None:
        return None
    return manual_overrides
