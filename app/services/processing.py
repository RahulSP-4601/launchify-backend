from __future__ import annotations

from pathlib import Path
from typing import Sequence

from app.models.projects import ProjectRecord, TranscriptSegment
from app.services.job_store import job_store
from app.services.project_store import StaleProjectAssetError, project_store
from app.services.script_writer import combine_transcript, generate_launch_script
from app.services.storage import download_asset_to_file
from app.services.transcription import transcribe_media_file


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
        transcript = transcribe_media_file(asset_file, job.content_type)
        if not is_latest_project_asset(job.user_id, job.project_id, job.asset_path):
            job_store.mark_completed(job.id)
            return
        if not transcript_is_usable(transcript):
            project_store.save_transcript(
                job.user_id,
                job.project_id,
                transcript,
                "failed",
                "We couldn't extract enough speech to generate a launch script.",
                job.asset_path,
            )
            job_store.mark_failed(job.id, "Transcript was too short for AI script generation.")
            return
        project_store.save_transcript(job.user_id, job.project_id, transcript, "scripting", asset_path=job.asset_path)
        launch_script = generate_launch_script(require_project(job.user_id, job.project_id))
        project_store.save_launch_script(job.user_id, job.project_id, launch_script, asset_path=job.asset_path)
        job_store.mark_completed(job.id)
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


def require_project(user_id: str, project_id: str) -> ProjectRecord:
    project = project_store.get_project(user_id, project_id)
    if project is None:
        raise RuntimeError("Project not found while generating the launch script.")
    return project


def handle_job_failure(user_id: str, project_id: str, asset_path: str, job_id: str, error_message: str) -> None:
    try:
        project_store.update_status_for_asset(user_id, project_id, asset_path, "failed", error_message)
        job_store.mark_failed(job_id, error_message)
    except StaleProjectAssetError:
        job_store.mark_completed(job_id)
