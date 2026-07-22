from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.core.config import get_settings
from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, GuideRecord, GuideStepRecord, LaunchScriptScene, LaunchScriptRecord, ProjectRecord, RenderSpecRecord, SceneRole, SessionEventRecord, SessionEventType, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.canonical_event_scene_builder import source_scene_number
from app.services.action_classifier import classify_action, event_action_class
from app.services.caption_designer import build_caption_track
from app.services.editorial_targeting import ActionEnvelope, EditorialTargetDecision, build_action_envelope, resolve_editorial_target
from app.services.editorial_planner import apply_editorial_direction, direct_scene
from app.services.edit_plan_guardrails import finalized_edit_plan
from app.services.event_grounding import normalize_event_timestamp
from app.services.focus_tracking import smooth_focus_handoffs
from app.services.inferred_recording_support import normalize_label
from app.services.motion_director import build_motion_track
from app.services.override_manager import apply_manual_overrides
from app.services.scene_roles import scene_role_from_action_class
from app.services.session_grounding import apply_session_grounding
from app.services.scene_alignment import align_script_scenes
from app.services.selection_disambiguation import disambiguated_guide_steps
from app.services.timing_sync import sync_edit_plan_timing
from app.services.visual_analysis import analysis_map
from app.services.visual_policy import ScenePolicy, build_scene_policy
from app.services.walkthrough_windows import action_result_window
from app.services.walkthrough_text_normalizer import normalized_scene, normalized_step
from app.services.edit_planner_grounded import (
    build_grounded_overview as grounded_overview_text,
    enrich_specific_target_from_visuals,
    grounded_action_class,
    grounded_decision_summary,
    grounded_motion_assets,
    grounded_primary_event,
    grounded_scene_number,
    grounded_synthetic_scene,
    grounded_visual_summary,
    normalized_overrides,
)


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
    enriched_steps = disambiguated_guide_steps(guide.steps, analyses_by_scene)
    planned_scenes = [build_grounded_scene(step, project, analyses_by_scene) for step in enriched_steps]
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
    editorial_edit = apply_editorial_direction(edit_plan, visual_analyses)
    focus_stable_edit = smooth_focus_handoffs(editorial_edit)
    overridden_edit = apply_manual_overrides(focus_stable_edit, normalized_overrides(project.manual_overrides))
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
    scene_role: SceneRole = scene_role_from_action_class(action_class)
    target, envelope, policy = planned_scene_intelligence(scene, transcript_slice, visual_analysis, start, end, scene_role, action_class)
    captions = build_caption_track(transcript_slice, start, end, project.template_config)
    zooms, highlights = build_motion_track(scene, start, end, policy, project.template_config)
    return scripted_edit_scene(scene, start, end, action_class, scene_role, target, envelope, policy, captions, zooms, highlights)


def planned_scene_intelligence(
    scene: LaunchScriptScene,
    transcript_slice: list[TranscriptSegment],
    visual_analysis: VisualSceneAnalysisRecord | None,
    start: float,
    end: float,
    scene_role: SceneRole,
    action_class: str,
) -> tuple[EditorialTargetDecision | None, ActionEnvelope, ScenePolicy]:
    target = resolve_editorial_target(scene, transcript_slice, visual_analysis)
    envelope = build_action_envelope(scene, transcript_slice, visual_analysis, target, start_time=start, end_time=end)
    policy = build_scene_policy(
        scene,
        transcript_slice,
        visual_analysis,
        scene_role=scene_role,
        action_class=action_class,
        editorial_target=target,
        action_envelope=envelope,
    )
    return target, envelope, policy


def scripted_edit_scene(
    scene: LaunchScriptScene,
    start: float,
    end: float,
    action_class: str,
    scene_role: SceneRole,
    target: EditorialTargetDecision | None,
    envelope: ActionEnvelope,
    policy: ScenePolicy,
    captions: list[EditPlanCaption],
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
) -> EditPlanScene:
    scene_end = max(end, envelope.recommended_end)
    readable_hold = readable_hold_seconds(scene_end, envelope, start)
    return EditPlanScene(
        scene_number=scene.scene_number,
        title=f"Scene {scene.scene_number}",
        purpose=scene.purpose,
        start=start,
        end=scene_end,
        render_duration_seconds=round(scene_end - start, 2),
        confidence=max(policy.scene_confidence, envelope.completeness_score),
        camera_mode=policy.camera_mode,
        decision_summary=policy.decision_summary,
        visual_summary=policy.visual_summary,
        spoken_line=scene.spoken_line,
        on_screen_text=scene.on_screen_text,
        specific_target_label=target.label if target is not None else scene.specific_target_label,
        source_excerpt=scene.source_excerpt,
        action_class=action_class,
        scene_role=scene_role,
        action_timestamp=envelope.action_time,
        result_anchor_timestamp=envelope.response_time,
        readable_hold_seconds=readable_hold,
        establish_end_timestamp=envelope.focus_start,
        focus_start_timestamp=envelope.focus_start,
        focus_end_timestamp=envelope.focus_end,
        settle_end_timestamp=envelope.settle_end,
        transition_style="fade",
        transition_duration_seconds=0.32,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )


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
    step = enrich_specific_target_from_visuals(step, visual_analysis, primary_event)
    synthetic_scene = grounded_synthetic_scene(step, start, end)
    policy, captions, zooms, highlights, envelope = grounded_motion_assets(
        project, step, synthetic_scene, transcript_slice, start, end, visual_analysis, primary_event,
    )
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else start
    action_class = grounded_action_class(step, primary_event)
    scene_role: SceneRole = scene_role_from_action_class(action_class)
    return grounded_edit_scene(step, start, end, primary_event, event_time, action_class, scene_role, policy, captions, zooms, highlights, envelope)


def grounded_edit_scene(
    step: GuideStepRecord,
    start: float,
    end: float,
    primary_event: SessionEventRecord | None,
    event_time: float,
    action_class: str,
    scene_role: SceneRole,
    policy: ScenePolicy,
    captions: list[EditPlanCaption],
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
    envelope: ActionEnvelope,
) -> EditPlanScene:
    scene_end = max(end, envelope.recommended_end)
    readable_hold = readable_hold_seconds(scene_end, envelope, start)
    return EditPlanScene(
        scene_number=step.step_index,
        title=step.title or f"Step {step.step_index}",
        purpose=step.instruction,
        start=start,
        end=scene_end,
        render_duration_seconds=round(scene_end - start, 2),
        confidence=max(policy.scene_confidence, envelope.completeness_score, 0.82 if primary_event is not None else 0.78),
        camera_mode="focus" if step.focus_selector or step.focus_label or primary_event is not None else policy.camera_mode,
        decision_summary=grounded_decision_summary(step, primary_event),
        visual_summary=grounded_visual_summary(step, primary_event, policy.visual_summary),
        spoken_line=step.narration,
        on_screen_text=step.on_screen_text,
        specific_target_label=step.specific_target_label or envelope.target_label,
        source_excerpt=step.source_excerpt or step.focus_label or step.title,
        action_class=action_class,
        scene_role=scene_role,
        action_timestamp=envelope.action_time or event_time,
        result_anchor_timestamp=envelope.response_time,
        readable_hold_seconds=readable_hold,
        establish_end_timestamp=envelope.focus_start,
        focus_start_timestamp=envelope.focus_start,
        focus_end_timestamp=envelope.focus_end,
        settle_end_timestamp=envelope.settle_end,
        transition_style="focus-push",
        transition_duration_seconds=0.24,
        captions=captions,
        zooms=zooms,
        highlights=highlights,
    )


def readable_hold_seconds(scene_end: float, envelope: ActionEnvelope, scene_start: float) -> float:
    anchor = envelope.response_time or envelope.action_time or scene_start
    return max(0.0, round(scene_end - max(anchor, scene_start), 2))


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
    return grounded_overview_text(project, guide)
