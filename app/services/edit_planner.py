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
    SessionEventType,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.canonical_event_scene_builder import source_scene_number
from app.services.action_classifier import classify_action, event_action_class
from app.services.caption_designer import build_caption_track
from app.services.editorial_planner import apply_editorial_direction, direct_scene
from app.services.edit_plan_guardrails import finalized_edit_plan
from app.services.event_grounding import focus_box_for_event, normalize_event_timestamp, primary_event_for_window, region_for_box
from app.services.focus_tracking import smooth_focus_handoffs
from app.services.motion_director import build_motion_track, offset_for_box
from app.services.override_manager import apply_manual_overrides
from app.services.scene_roles import scene_role_from_action_class
from app.services.session_grounding import apply_session_grounding
from app.services.scene_alignment import align_script_scenes
from app.services.timing_sync import sync_edit_plan_timing
from app.services.visual_analysis import analysis_map
from app.services.visual_policy import ScenePolicy, build_scene_policy
from app.services.walkthrough_windows import action_result_window
from app.services.walkthrough_text_normalizer import normalized_scene, normalized_step


def generate_edit_plan(
    project: ProjectRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    if project.guide is not None and project.guide.steps:
        return generate_grounded_edit_plan(project, visual_analyses)
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
                    analyses_by_scene.get(scene_inputs[0].scene_number)
                    or analyses_by_scene.get(source_scene_number(scene_inputs[0].scene_number)),
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
    editorial_edit = apply_editorial_direction(grounded_edit, visual_analyses)
    focus_stable_edit = smooth_focus_handoffs(editorial_edit)
    overridden_edit = apply_manual_overrides(focus_stable_edit, normalized_overrides(project.manual_overrides))
    return finalized_edit_plan(project, overridden_edit)

def generate_grounded_edit_plan(
    project: ProjectRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    guide = require_guide(project.guide)
    analyses_by_scene = analysis_map(visual_analyses or [])
    planned_scenes = [build_grounded_scene(step, project, analyses_by_scene) for step in guide.steps]
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
    overridden_edit = apply_manual_overrides(edit_plan, normalized_overrides(project.manual_overrides))
    return finalized_edit_plan(project, overridden_edit)
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
    scene = normalized_scene(scene)
    transcript_slice = slice_transcript(project.transcript, start, end)
    action_class = scene_action_class(scene, transcript_slice)
    scene_role = scene_role_from_action_class(action_class)
    policy = build_scene_policy(scene, transcript_slice, visual_analysis, scene_role=scene_role, action_class=action_class)
    captions = build_caption_track(transcript_slice, start, end, project.template_config)
    zooms, highlights = build_motion_track(scene, start, end, policy, project.template_config)
    planned_scene = EditPlanScene(
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
        action_class=action_class,
        scene_role=scene_role,
        action_timestamp=None,
        transition_style="fade",
        transition_duration_seconds=0.32,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )
    return direct_scene(planned_scene, visual_analysis)


def scene_action_class(
    scene: LaunchScriptScene,
    transcript_slice: list[TranscriptSegment],
) -> str:
    event_type = inferred_scene_event_type(scene, transcript_slice)
    transcript_text = " ".join(segment.text for segment in transcript_slice)
    return classify_action(event_type, scene.on_screen_text or scene.purpose, transcript_text, scene.source_excerpt)


def inferred_scene_event_type(
    scene: LaunchScriptScene,
    transcript_slice: list[TranscriptSegment],
) -> SessionEventType:
    transcript_text = " ".join(segment.text.lower() for segment in transcript_slice)
    combined = f"{scene.purpose} {scene.spoken_line} {scene.on_screen_text} {scene.source_excerpt} {transcript_text}".lower()
    if any(token in combined for token in ("type", "enter", "write", "email", "password", "search")):
        return "input"
    if any(token in combined for token in ("focus", "review", "notice", "look at")):
        return "focus"
    if any(token in combined for token in ("navigate", "go to", "redirect", "takes you to")):
        return "navigation"
    return "click"


def build_grounded_scene(
    step: GuideStepRecord,
    project: ProjectRecord,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> EditPlanScene:
    step = normalized_step(step)
    start = round(step.start, 2)
    end = round(max(step.end, start + 0.8), 2)
    transcript_slice = slice_transcript(project.transcript, start, end)
    primary_event = grounded_primary_event(project, step, start, end)
    scene_number = grounded_scene_number(primary_event, step)
    visual_analysis = analyses_by_scene.get(scene_number) or analyses_by_scene.get(step.step_index)
    synthetic_scene = grounded_synthetic_scene(step, start, end)
    policy, captions, zooms, highlights = grounded_motion_assets(
        project, step, synthetic_scene, transcript_slice, start, end, visual_analysis, primary_event,
    )
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else start
    action_class = grounded_action_class(step, primary_event)
    scene_role = scene_role_from_action_class(action_class)
    planned_scene = EditPlanScene(
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
        action_class=action_class,
        scene_role=scene_role,
        action_timestamp=event_time,
        transition_style="focus-push",
        transition_duration_seconds=0.24,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )
    return direct_scene(planned_scene, visual_analysis)


def grounded_primary_event(
    project: ProjectRecord,
    step: GuideStepRecord,
    start: float,
    end: float,
) -> SessionEventRecord | None:
    preferred = " ".join(part for part in (step.focus_label, step.title, step.instruction, step.narration) if part)
    return primary_event_for_window(project.recording_session, start, end, preferred)


def grounded_scene_number(primary_event: SessionEventRecord | None, step: GuideStepRecord) -> int:
    return int(primary_event.metadata.get("scene_number", "0")) if primary_event is not None else step.step_index


def grounded_synthetic_scene(step: GuideStepRecord, start: float, end: float) -> LaunchScriptScene:
    return LaunchScriptScene(
        scene_number=step.step_index,
        purpose=step.instruction,
        spoken_line=step.narration,
        on_screen_text=step.on_screen_text,
        source_excerpt=step.source_excerpt or step.focus_label or step.title,
        estimated_duration_seconds=max(end - start, 0.8),
    )


def grounded_action_class(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> str:
    if primary_event is not None:
        return event_action_class(primary_event)
    return step.action_class or classify_action("click", step.focus_label or step.title, step.narration, step.source_excerpt)


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
    action_class = grounded_action_class(step, primary_event)
    scene_role = scene_role_from_action_class(action_class)
    policy = build_scene_policy(synthetic_scene, transcript_slice, visual_analysis, scene_role=scene_role, action_class=action_class)
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
    grounded_zooms = grounded_zoom_track(step, primary_event, event_focus_box, focus_region, zooms)
    grounded_highlights = grounded_highlight_track(step, primary_event, event_focus_box, focus_region, highlights)
    return grounded_zooms, grounded_highlights


def grounded_zoom_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    zooms: list[EditPlanZoom],
) -> list[EditPlanZoom]:
    hydrated = [hydrate_grounded_zoom(zoom, event_focus_box, focus_region) for zoom in zooms]
    if primary_event is not None:
        return segmented_grounded_zooms(step, primary_event, event_focus_box, focus_region)
    return hydrated or [seed_grounded_zoom(step, event_focus_box, focus_region)]


def grounded_highlight_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    highlights: list[EditPlanHighlight],
) -> list[EditPlanHighlight]:
    hydrated = [hydrate_grounded_highlight(highlight, step, event_focus_box, focus_region) for highlight in highlights]
    if primary_event is not None:
        return [segmented_grounded_highlight(step, primary_event, event_focus_box, focus_region)]
    return hydrated or [seed_grounded_highlight(step, event_focus_box, focus_region)]


def segmented_grounded_zooms(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> list[EditPlanZoom]:
    focus_start, focus_peak_end, settle_end = grounded_focus_windows(step, primary_event)
    lead_end = max(min(focus_start, step.end), min(step.start + 0.34, focus_start))
    zooms: list[EditPlanZoom] = []
    if lead_end - step.start >= 0.35:
        zooms.append(build_zoom_segment(step.start, lead_end, 1.04, "grounded lead-in", 0.74, event_focus_box, focus_region, 0.35, 0.3, 0.08))
    zooms.append(build_zoom_segment(focus_start, focus_peak_end, 1.24, "grounded action focus", 0.9, event_focus_box, focus_region, 1.0, 0.72, 0.12))
    if settle_end - focus_peak_end >= 0.35:
        zooms.append(build_zoom_segment(focus_peak_end, settle_end, 1.12, "grounded settle hold", 0.82, event_focus_box, focus_region, 0.7, 0.58, 0.14))
    return zooms


def build_zoom_segment(
    start: float,
    end: float,
    scale: float,
    reason: str,
    confidence: float,
    focus_box: FocusBox,
    focus_region: str,
    offset_multiplier: float,
    hold_ratio: float,
    smoothing: float,
) -> EditPlanZoom:
    return EditPlanZoom(
        start=round(start, 2),
        end=round(end, 2),
        scale=scale,
        focus_region=focus_region,
        reason=reason,
        confidence=confidence,
        focus_box=focus_box,
        x_offset=offset_for_box(focus_box, focus_region, axis="x") * offset_multiplier,
        y_offset=offset_for_box(focus_box, focus_region, axis="y") * offset_multiplier,
        hold_ratio=hold_ratio,
        smoothing=smoothing,
    )


def segmented_grounded_highlight(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start = max(step.start, event_time - 0.14)
    focus_peak_end = min(step.end, focus_start + 1.35)
    if focus_peak_end - focus_start < 0.8:
        focus_peak_end = min(step.end, focus_start + 0.8)
    return EditPlanHighlight(
        start=round(focus_start, 2),
        end=round(focus_peak_end, 2),
        label=step.highlight_label or step.focus_label or step.title,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.92,
        focus_box=event_focus_box,
        ui_label=step.highlight_label or step.focus_label or step.title,
    )


def grounded_focus_windows(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
) -> tuple[float, float, float]:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start, focus_peak_end, settle_end = action_result_window(step.start, step.end, event_time, step.narration)
    if focus_peak_end - focus_start < 0.7:
        focus_peak_end = min(step.end, focus_start + 0.7)
    return focus_start, focus_peak_end, settle_end


def hydrate_grounded_zoom(zoom: EditPlanZoom, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    resolved_focus_box = zoom.focus_box or event_focus_box
    return zoom.model_copy(update={
        "focus_box": resolved_focus_box,
        "focus_region": focus_region if zoom.focus_region == "center" else zoom.focus_region,
        "confidence": max(zoom.confidence, 0.82),
        "scale": max(zoom.scale, 1.2),
        "x_offset": offset_for_box(resolved_focus_box, focus_region, axis="x"),
        "y_offset": offset_for_box(resolved_focus_box, focus_region, axis="y"),
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
        scale=1.22,
        focus_region=focus_region,
        reason="grounded session focus",
        confidence=0.86,
        focus_box=event_focus_box,
        x_offset=offset_for_box(event_focus_box, focus_region, axis="x"),
        y_offset=offset_for_box(event_focus_box, focus_region, axis="y"),
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
    return f"Launchify tightened the recording for {audience}, aligned {len(launch_script.scenes)} scenes to the source walkthrough, and prepared captions, zooms, and highlights using {visual_note}."


def build_grounded_overview(project: ProjectRecord, guide: GuideRecord) -> str:
    audience = project.target_audience or "the intended product audience"
    return f"Launchify grounded {len(guide.steps)} captured product actions for {audience}, turned them into synchronized steps, and prepared captions, zooms, and highlights from the event-backed guide."


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
