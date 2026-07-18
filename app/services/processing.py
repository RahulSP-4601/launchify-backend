from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

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
from app.services.guide_synthesizer import synthesize_grounded_guide
from app.services.inferred_recording_session import infer_recording_session
from app.services.job_store import job_store
from app.services.phase_four import apply_phase_four_defaults
from app.services.playback_preview import publish_grounded_preview
from app.services.project_store import StaleProjectAssetError, project_store
from app.services.script_writer import combine_transcript, generate_launch_script
from app.services.storage import download_asset_to_file
from app.services.timing import timed_stage
from app.services.transcription import transcribe_media_file
from app.services.usage_service import projected_rendered_seconds, total_rendered_seconds, usage_lock
from app.services.visual_analysis import analyze_video_scenes, visual_analysis_available

logger = logging.getLogger(__name__)


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
        asset_file = download_asset_to_file(job.asset_path)
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
    job_store.heartbeat(job.id)
    with timed_stage("transcription", settings.transcription_warn_seconds):
        transcript = transcribe_media_file(asset_file, job.content_type)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    if not transcript_is_usable(transcript):
        mark_transcript_failure(job, transcript)
        return
    job_store.heartbeat(job.id)
    with timed_stage("script_generation", settings.script_generation_warn_seconds):
        launch_script = save_scripting_step(job, asset_file, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    job_store.heartbeat(job.id)
    with timed_stage("planning", settings.planning_warn_seconds):
        save_planning_step(job, asset_file, launch_script, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    job_store.heartbeat(job.id)
    with timed_stage("preview_publish", settings.total_pipeline_warn_seconds):
        save_render_step(job, asset_file)


def mark_transcript_failure(job: ProcessingJobRecord, transcript: Sequence[TranscriptSegment]) -> None:
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
) -> LaunchScriptRecord:
    project_store.save_transcript(job.user_id, job.project_id, transcript, "scripting", asset_path=job.asset_path)
    current_project = require_project(job.user_id, job.project_id)
    fallback_script: LaunchScriptRecord | None = None
    try:
        guide = generate_grounded_guide_if_available(current_project, transcript)
        if guide is None:
            fallback_script = generate_launch_script(current_project)
            guide = generate_inferred_grounded_guide(current_project, job, asset_file, transcript, fallback_script)
    except Exception:
        logger.exception(
            "Grounded guide generation failed for project %s. Falling back to standard script generation.",
            job.project_id,
        )
        guide = None
    launch_script = persist_guide_or_script(current_project, job, guide, fallback_script)
    project_store.save_launch_script(job.user_id, job.project_id, launch_script, asset_path=job.asset_path)
    return launch_script


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
) -> tuple[GuideRecord, LaunchScriptRecord] | None:
    if project.recording_session is None or not project.recording_session.events:
        return None
    return synthesize_grounded_guide(project, transcript)


def generate_inferred_grounded_guide(
    project: ProjectRecord,
    job: ProcessingJobRecord,
    asset_file: Path,
    transcript: list[TranscriptSegment],
    base_script: LaunchScriptRecord,
) -> tuple[GuideRecord, LaunchScriptRecord] | None:
    visual_analyses = maybe_analyze_video_scenes(asset_file, base_script, transcript)
    recording_session = infer_recording_session(project, asset_file, base_script, transcript, visual_analyses)
    if recording_session is None or not recording_session.events:
        return None
    project_store.save_recording_session(job.user_id, job.project_id, recording_session, asset_path=job.asset_path)
    grounded_project = require_project(job.user_id, job.project_id)
    return synthesize_grounded_guide(grounded_project, transcript)


def save_planning_step(
    job: ProcessingJobRecord,
    asset_file: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
) -> None:
    logger.info("Planning started for project %s.", job.project_id)
    current_project = require_project(job.user_id, job.project_id)
    visual_analyses = None if current_project.guide is not None else maybe_analyze_video_scenes(asset_file, launch_script, transcript)
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
    logger.info("Planning completed for project %s.", job.project_id)


def save_render_step(job: ProcessingJobRecord, asset_file: Path) -> None:
    logger.info("Preview publish started for project %s.", job.project_id)
    heartbeat = build_job_heartbeat(job)
    with usage_lock(job.user_id, heartbeat=heartbeat):
        publish_preview_video(job, asset_file, heartbeat)
    job_store.mark_completed(job.id)
    logger.info("Preview publish completed for project %s.", job.project_id)


def publish_preview_video(
    job: ProcessingJobRecord,
    asset_file: Path,
    heartbeat: Callable[[], None],
) -> None:
    heartbeat()
    project_store.update_status_for_asset(job.user_id, job.project_id, job.asset_path, "rendering")
    current_project = require_project(job.user_id, job.project_id)
    preview_video = publish_grounded_preview(current_project, asset_file)
    preview_duration = preview_duration_seconds(current_project, preview_video.duration_seconds)
    preview_video = preview_video.model_copy(update={"duration_seconds": preview_duration})
    enforce_preview_limit(job.user_id, job.project_id, preview_video.duration_seconds)
    project_store.save_render_outputs(job.user_id, job.project_id, preview_video, asset_path=job.asset_path)


def enforce_preview_limit(user_id: str, project_id: str, duration_seconds: float) -> None:
    settings = get_settings()
    projected_seconds = projected_rendered_seconds(user_id, project_id, duration_seconds)
    limit_seconds = float(settings.trial_minutes_limit * 60)
    if projected_seconds <= limit_seconds:
        return
    remaining_seconds = max(limit_seconds - total_rendered_seconds(user_id), 0.0)
    remaining_minutes = remaining_seconds / 60
    raise RuntimeError(
        "This preview would exceed your trial limit. "
        f"Only {remaining_minutes:.1f} minutes remain before the {settings.trial_minutes_limit} minute cap."
    )


def preview_duration_seconds(project: ProjectRecord, fallback_duration_seconds: float) -> float:
    if project.edit_plan is None:
        return fallback_duration_seconds
    return max(round(project.edit_plan.total_duration_seconds, 2), 0.0) or fallback_duration_seconds


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
) -> list[VisualSceneAnalysisRecord] | None:
    settings = get_settings()
    if not settings.blocking_visual_analysis_enabled or not visual_analysis_available():
        return None
    try:
        return analyze_video_scenes(asset_file, launch_script, transcript)
    except Exception:
        logger.exception("Visual analysis failed for %s. Falling back to script-led planning.", asset_file.name)
        return None


def handle_job_failure(user_id: str, project_id: str, asset_path: str, job_id: str, error_message: str) -> None:
    try:
        project_store.update_status_for_asset(user_id, project_id, asset_path, "failed", error_message)
        job_store.mark_failed(job_id, error_message)
    except StaleProjectAssetError:
        job_store.mark_completed(job_id)


def build_job_heartbeat(job: ProcessingJobRecord) -> Callable[[], None]:
    def heartbeat() -> None:
        job_store.heartbeat(job.id)

    return heartbeat
