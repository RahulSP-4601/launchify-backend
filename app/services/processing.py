from __future__ import annotations

from pathlib import Path
from typing import Sequence

from app.models.projects import (
    BenchmarkReportRecord,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProcessingJobRecord,
    ProjectRecord,
    TemplateConfigRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
    VoiceoverRecord,
)
from app.services.benchmarking import build_benchmark_report
from app.services.edit_planner import generate_edit_plan
from app.services.job_store import job_store
from app.services.phase_four import apply_phase_four_defaults
from app.services.project_store import StaleProjectAssetError, project_store
from app.services.rendering import render_project_videos
from app.services.script_writer import combine_transcript, generate_launch_script
from app.services.storage import download_asset_to_file
from app.services.transcription import transcribe_media_file
from app.services.usage_service import usage_lock
from app.services.visual_analysis import analyze_video_scenes, visual_analysis_available


def process_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        raise RuntimeError("Processing job not found.")
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
    finally:
        if asset_file is not None:
            asset_file.unlink(missing_ok=True)


def is_latest_project_asset(user_id: str, project_id: str, asset_path: str) -> bool:
    project = project_store.get_project(user_id, project_id)
    if project is None or project.asset is None:
        return False
    return project.asset.storage_path == asset_path


def transcript_is_usable(transcript: Sequence[TranscriptSegment]) -> bool:
    return len(combine_transcript(transcript).strip()) >= 40


def run_processing_pipeline(job: ProcessingJobRecord, asset_file: Path) -> None:
    transcript = transcribe_media_file(asset_file, job.content_type)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    if not transcript_is_usable(transcript):
        mark_transcript_failure(job, transcript)
        return
    launch_script = save_scripting_step(job, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    save_planning_step(job, asset_file, launch_script, transcript)
    if stale_asset_detected(job.user_id, job.project_id, job.asset_path, job.id):
        return
    save_render_step(job)


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


def save_scripting_step(job: ProcessingJobRecord, transcript: list[TranscriptSegment]) -> LaunchScriptRecord:
    project_store.save_transcript(job.user_id, job.project_id, transcript, "scripting", asset_path=job.asset_path)
    launch_script = generate_launch_script(require_project(job.user_id, job.project_id))
    project_store.save_launch_script(job.user_id, job.project_id, launch_script, asset_path=job.asset_path)
    return launch_script


def save_planning_step(
    job: ProcessingJobRecord,
    asset_file: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
) -> None:
    visual_analyses = maybe_analyze_video_scenes(asset_file, launch_script, transcript)
    current_project = require_project(job.user_id, job.project_id)
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


def save_render_step(job: ProcessingJobRecord) -> None:
    with usage_lock(job.user_id):
        preview_video, final_video, refined_edit_plan, refined_quality_report = render_project_videos(
            job.user_id,
            require_project(job.user_id, job.project_id),
        )
        project_store.save_refined_edit_plan(
            job.user_id,
            job.project_id,
            refined_edit_plan,
            asset_path=job.asset_path,
        )
        current_project = require_project(job.user_id, job.project_id)
        benchmark_report = build_benchmark_report(current_project, refined_edit_plan, refined_quality_report)
        project_store.save_phase_four_state(
            job.user_id,
            job.project_id,
            refined_quality_report,
            benchmark_report,
            require_voiceover(current_project),
            require_template_config(current_project),
            require_manual_overrides(current_project),
            asset_path=job.asset_path,
        )
        project_store.save_render_outputs(job.user_id, job.project_id, preview_video, final_video, asset_path=job.asset_path)
    job_store.mark_completed(job.id)


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
    if not visual_analysis_available():
        return None
    return analyze_video_scenes(asset_file, launch_script, transcript)


def handle_job_failure(user_id: str, project_id: str, asset_path: str, job_id: str, error_message: str) -> None:
    try:
        project_store.update_status_for_asset(user_id, project_id, asset_path, "failed", error_message)
        job_store.mark_failed(job_id, error_message)
    except StaleProjectAssetError:
        job_store.mark_completed(job_id)


def require_voiceover(project: ProjectRecord) -> VoiceoverRecord:
    if project.voiceover is None:
        raise RuntimeError("Voiceover state is missing before render review update.")
    return project.voiceover


def require_template_config(project: ProjectRecord) -> TemplateConfigRecord:
    if project.template_config is None:
        raise RuntimeError("Template config is missing before render review update.")
    return project.template_config


def require_manual_overrides(project: ProjectRecord) -> ManualOverrideRecord:
    if project.manual_overrides is None:
        raise RuntimeError("Manual overrides are missing before render review update.")
    return project.manual_overrides
