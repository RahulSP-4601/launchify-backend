from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from app.core.config import get_settings
from app.models.projects import (
    GuideRecord,
    LaunchScriptRecord,
    ProcessingJobRecord,
    ProjectRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.edit_planner import generate_edit_plan
from app.services.grounded_script_refinement import refine_launch_script_with_events, refine_launch_script_with_visuals
from app.services.guide_synthesizer import synthesize_grounded_guide
from app.services.inference_step_builder import build_inference_script
from app.services.inferred_recording_session import infer_recording_session
from app.services.job_store import job_store
from app.services.phase_four import apply_phase_four_defaults
from app.services.playback_preview import publish_grounded_preview
from app.services.preview_delivery import preview_delivery_diagnostics
from app.services.project_store import StaleProjectAssetError, project_store
from app.services.script_writer import combine_transcript, generate_launch_script
from app.services.storage import download_asset_to_file
from app.services.timing import timed_stage
from app.services.transcription import transcribe_media_file
from app.services.visual_analysis import analyze_video_scenes, visual_analysis_available
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScriptingArtifacts:
    launch_script: LaunchScriptRecord
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None


def pipeline_log(project_id: str, stage: str, **details: object) -> None:
    payload = ", ".join(f"{key}={details[key]!r}" for key in sorted(details))
    logger.info("Pipeline stage [%s] for project %s: %s", stage, project_id, payload or "ok")


def process_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        raise RuntimeError("Processing job not found.")
    logger.info("Processing job %s started for project %s.", job.id, job.project_id)
    if not is_latest_project_asset(job.user_id, job.project_id, job.asset_path):
        job_store.mark_completed(job.id)
        return
    try:
        project_store.update_status_for_asset(job.user_id, job.project_id, job.asset_path, "transcribing")
    except StaleProjectAssetError:
        job_store.mark_completed(job.id)
        return
    asset_file: Path | None = None
    try:
        logger.info("Processing job %s: downloading uploaded asset %s.", job.id, job.asset_path)
        asset_file = download_asset_to_file(job.asset_path, heartbeat=lambda: job_store.heartbeat(job.id))
        logger.info("Processing job %s: downloaded uploaded asset to %s.", job.id, asset_file)
        run_processing_pipeline(job, asset_file)
    except StaleProjectAssetError:
        job_store.mark_completed(job.id)
    except RuntimeError as exc:
        handle_job_failure(job.user_id, job.project_id, job.asset_path, job.id, str(exc))
    except Exception as exc:
        logger.exception("Unexpected processing failure for job %s", job.id)
        handle_job_failure(
            job.user_id,
            job.project_id,
            job.asset_path,
            job.id,
            f"Unexpected processing failure: {exc}",
        )
    finally:
        if asset_file is not None:
            asset_file.unlink(missing_ok=True)
        logger.info("Processing job %s finished.", job.id)


def is_latest_project_asset(user_id: str, project_id: str, asset_path: str) -> bool:
    project = project_store.get_project(user_id, project_id)
    if project is None or project.asset is None:
        return False
    return project.asset.storage_path == asset_path


def transcript_is_usable(transcript: Sequence[TranscriptSegment]) -> bool:
    return len(combine_transcript(transcript).strip()) >= 40


def run_processing_pipeline(job: ProcessingJobRecord, asset_file: Path) -> None:
    settings = get_settings()
    pipeline_log(job.project_id, "asset_downloaded", asset_file=asset_file.name, asset_path=job.asset_path, content_type=job.content_type)
    job_store.heartbeat(job.id)
    with timed_stage("transcription", settings.transcription_warn_seconds):
        logger.info("Processing job %s: transcription started for %s.", job.id, asset_file.name)
        transcript = transcribe_media_file(asset_file, job.content_type, heartbeat=lambda: job_store.heartbeat(job.id))
        logger.info("Processing job %s: transcription finished with %s segments.", job.id, len(transcript))
    pipeline_log(
        job.project_id,
        "transcription_complete",
        usable=transcript_is_usable(transcript),
        segments=len(transcript),
        transcript_end=max((segment.end for segment in transcript), default=0.0),
    )
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    if not transcript_is_usable(transcript):
        mark_transcript_failure(job, transcript)
        return
    job_store.heartbeat(job.id)
    with timed_stage("script_generation", settings.script_generation_warn_seconds):
        scripting = save_scripting_step(job, asset_file, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    job_store.heartbeat(job.id)
    with timed_stage("planning", settings.planning_warn_seconds):
        save_planning_step(job, asset_file, scripting, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    job_store.heartbeat(job.id)
    with timed_stage("preview_publish", settings.total_pipeline_warn_seconds):
        save_render_step(job, asset_file)


def mark_transcript_failure(job: ProcessingJobRecord, transcript: Sequence[TranscriptSegment]) -> None:
    pipeline_log(job.project_id, "transcription_failed", reason="transcript_too_short", segments=len(transcript))
    project_store.save_transcript(
        job.user_id,
        job.project_id,
        list(transcript),
        "failed",
        "We couldn't extract enough speech to generate a launch script.",
        job.asset_path,
    )
    job_store.mark_failed(job.id, "Transcript was too short for AI script generation.")


def save_scripting_step(
    job: ProcessingJobRecord,
    asset_file: Path,
    transcript: list[TranscriptSegment],
) -> ScriptingArtifacts:
    project_store.save_transcript(job.user_id, job.project_id, transcript, "scripting", asset_path=job.asset_path)
    current_project = require_project(job.user_id, job.project_id)
    fallback_script = inferred_walkthrough_fallback(current_project, transcript)
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None
    inferred_script = fallback_script
    try:
        guide, visual_analyses = generate_grounded_guide_if_available(current_project, transcript)
        if guide is None:
            guide, visual_analyses, inferred_script = generate_inferred_grounded_guide(current_project, job, asset_file, transcript)
        guide = acceptable_grounded_guide(guide, require_project(job.user_id, job.project_id), transcript, job.project_id)
    except Exception:
        logger.exception(
            "Grounded guide generation failed for project %s. Falling back to standard script generation.",
            job.project_id,
        )
        guide = None
    launch_script = persist_guide_or_script(current_project, job, guide, inferred_script or fallback_script)
    project_store.save_launch_script(job.user_id, job.project_id, launch_script, asset_path=job.asset_path)
    pipeline_log(
        job.project_id,
        "scripting_complete",
        launch_script_scenes=len(launch_script.scenes),
        visual_analyses=0 if visual_analyses is None else len(visual_analyses),
        guide_saved=bool(guide and guide[0].steps),
        fallback_used=guide is None,
    )
    return ScriptingArtifacts(launch_script=launch_script, visual_analyses=visual_analyses)


def acceptable_grounded_guide(
    guide: tuple[GuideRecord, LaunchScriptRecord] | None,
    project: ProjectRecord,
    transcript: list[TranscriptSegment],
    project_id: str,
) -> tuple[GuideRecord, LaunchScriptRecord] | None:
    if guide is None:
        return None
    duration_seconds = recording_duration_seconds(project.recording_session, transcript)
    if not guide_is_under_grounded(guide[0], duration_seconds):
        pipeline_log(project_id, "guide_grounding", accepted=True, steps=len(guide[0].steps), duration_seconds=duration_seconds)
        return guide
    logger.warning(
        "Grounded guide for project %s is under-grounded for a %.2fs walkthrough; falling back to inferred multi-scene script.",
        project_id,
        duration_seconds,
    )
    pipeline_log(project_id, "guide_grounding", accepted=False, steps=len(guide[0].steps), duration_seconds=duration_seconds, reason="under_grounded")
    return None


def persist_guide_or_script(
    project: ProjectRecord,
    job: ProcessingJobRecord,
    guide: tuple[GuideRecord, LaunchScriptRecord] | None,
    fallback_script: LaunchScriptRecord | None,
) -> LaunchScriptRecord:
    if guide is None:
        return fallback_script or generate_launch_script(project)
    grounded_guide, grounded_script = guide
    project_store.save_guide(job.user_id, job.project_id, grounded_guide, "planning", asset_path=job.asset_path)
    return grounded_script


def generate_grounded_guide_if_available(
    project: ProjectRecord,
    transcript: list[TranscriptSegment],
) -> tuple[tuple[GuideRecord, LaunchScriptRecord] | None, list[VisualSceneAnalysisRecord] | None]:
    if project.recording_session is None or not project.recording_session.events:
        return None, None
    return synthesize_grounded_guide(project, transcript), None


def generate_inferred_grounded_guide(
    project: ProjectRecord,
    job: ProcessingJobRecord,
    asset_file: Path,
    transcript: list[TranscriptSegment],
) -> tuple[tuple[GuideRecord, LaunchScriptRecord] | None, list[VisualSceneAnalysisRecord] | None, LaunchScriptRecord]:
    inference_script, scene_ranges = build_inference_script(project, transcript)
    pipeline_log(
        job.project_id,
        "inference_script_built",
        scenes=len(inference_script.scenes),
        scene_ranges=len(scene_ranges),
    )
    visual_analyses = maybe_analyze_video_scenes(asset_file, inference_script, transcript, scene_ranges)
    refined_script = refine_launch_script_with_visuals(inference_script, visual_analyses)
    recording_session = infer_recording_session(project, asset_file, inference_script, transcript, visual_analyses)
    if recording_session is None or not recording_session.events:
        pipeline_log(
            job.project_id,
            "recording_session_inference",
            recovered=False,
            events=0 if recording_session is None else len(recording_session.events),
            visual_analyses=0 if visual_analyses is None else len(visual_analyses),
        )
        return None, visual_analyses, refined_script
    project_store.save_recording_session(job.user_id, job.project_id, recording_session, asset_path=job.asset_path)
    pipeline_log(
        job.project_id,
        "recording_session_inference",
        recovered=True,
        events=len(recording_session.events),
        under_grounded=recording_session.grounding_diagnostics.get("under_grounded", ""),
        timeline_coverage_ratio=recording_session.grounding_diagnostics.get("timeline_coverage_ratio", ""),
    )
    refined_script = refine_launch_script_with_events(refined_script, recording_session.events, visual_analyses)
    grounded_project = require_project(job.user_id, job.project_id)
    return synthesize_grounded_guide(grounded_project, transcript, visual_analyses), visual_analyses, refined_script


def inferred_walkthrough_fallback(
    project: ProjectRecord,
    transcript: list[TranscriptSegment],
) -> LaunchScriptRecord:
    script, _scene_ranges = build_inference_script(project, transcript)
    return script if script.scenes else generate_launch_script(project)


def save_planning_step(
    job: ProcessingJobRecord,
    asset_file: Path,
    scripting: ScriptingArtifacts,
    transcript: list[TranscriptSegment],
) -> None:
    logger.info("Planning started for project %s.", job.project_id)
    current_project = require_project(job.user_id, job.project_id)
    visual_analyses = scripting.visual_analyses
    if visual_analyses is None:
        visual_analyses = maybe_analyze_video_scenes(asset_file, scripting.launch_script, transcript)
    else:
        logger.info("Planning reused %s cached scene analyses from grounded extraction for project %s.", len(visual_analyses), job.project_id)
    edit_plan = generate_edit_plan(current_project, visual_analyses)
    edit_plan, quality_report, benchmark_report, voiceover, template_config, manual_overrides = apply_phase_four_defaults(
        job.user_id,
        current_project,
        edit_plan,
    )
    project_store.save_edit_plan(job.user_id, job.project_id, edit_plan, asset_path=job.asset_path)
    project_store.save_phase_four_state(
        job.user_id,
        job.project_id,
        quality_report,
        benchmark_report,
        voiceover,
        template_config,
        manual_overrides,
        asset_path=job.asset_path,
    )
    delivery = preview_delivery_diagnostics(edit_plan, voiceover)
    pipeline_log(
        job.project_id,
        "planning_complete",
        edit_plan_scenes=len(edit_plan.scenes),
        total_duration_seconds=edit_plan.total_duration_seconds,
        zoom_scenes=sum(1 for scene in edit_plan.scenes if scene.zooms),
        highlight_scenes=sum(1 for scene in edit_plan.scenes if scene.highlights),
        voiceover_status=voiceover.status,
        voiceover_mode=voiceover.mode,
        quality_score=quality_report.score,
        ready_for_export=quality_report.ready_for_export,
        dynamic_scene_ratio=delivery.dynamic_scene_ratio,
        highlight_scene_ratio=delivery.highlight_scene_ratio,
        voiced_scene_ratio=delivery.voiced_scene_ratio,
        avg_voice_words=delivery.avg_voice_words,
        delivery_issues=list(delivery.issues),
    )
    logger.info("Planning completed for project %s.", job.project_id)


def save_render_step(job: ProcessingJobRecord, _asset_file: Path) -> None:
    project = require_project(job.user_id, job.project_id)
    logger.info("Publishing grounded preview render for project %s.", job.project_id)
    preview_video = publish_grounded_preview(job.user_id, project, _asset_file, heartbeat=lambda: job_store.heartbeat(job.id))
    project_store.save_render_outputs(job.user_id, job.project_id, preview_video, asset_path=job.asset_path)
    pipeline_log(
        job.project_id,
        "preview_stored",
        variant=preview_video.variant,
        duration_seconds=preview_video.duration_seconds,
        size_bytes=preview_video.size_bytes,
        storage_path=preview_video.storage_path,
    )
    job_store.mark_completed(job.id)
    logger.info("Walkthrough preview completed for project %s.", job.project_id)


def require_project(user_id: str, project_id: str) -> ProjectRecord:
    project = project_store.get_project(user_id, project_id)
    if project is None:
        raise RuntimeError("Project not found while generating the launch script.")
    return project


def stale_asset_detected(user_id: str, project_id: str, asset_path: str, job_id: str) -> bool:
    if is_latest_project_asset(user_id, project_id, asset_path):
        return False
    job_store.mark_completed(job_id)
    return True


def maybe_analyze_video_scenes(
    asset_file: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    scene_ranges: list[tuple[float, float]] | None = None,
) -> list[VisualSceneAnalysisRecord] | None:
    settings = get_settings()
    if not settings.blocking_visual_analysis_enabled or not visual_analysis_available():
        return None
    try:
        analyses = analyze_video_scenes(asset_file, launch_script, transcript, scene_ranges)
        logger.info(
            "Visual analysis summary for %s: scenes=%s, with_frames=%s, with_visible_labels=%s, with_click_signal=%s.",
            asset_file.name,
            len(analyses),
            sum(1 for analysis in analyses if analysis.frames),
            sum(1 for analysis in analyses if analysis.visible_labels),
            sum(1 for analysis in analyses if analysis.click_detected),
        )
        return analyses
    except Exception:
        logger.exception("Visual analysis failed for %s. Falling back to script-led planning.", asset_file.name)
        return None


def handle_job_failure(user_id: str, project_id: str, asset_path: str, job_id: str, error_message: str) -> None:
    try:
        project_store.update_status_for_asset(user_id, project_id, asset_path, "failed", error_message)
        job_store.mark_failed(job_id, error_message)
    except StaleProjectAssetError:
        job_store.mark_completed(job_id)
