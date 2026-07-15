from __future__ import annotations

from pathlib import Path

from app.services.job_store import job_store
from app.services.project_store import project_store
from app.services.storage import download_asset_to_file
from app.services.transcription import transcribe_media_file


def process_job(job_id: str) -> None:
    job = job_store.get_job(job_id)
    if job is None:
        raise RuntimeError("Processing job not found.")
    if not is_latest_project_asset(job.user_id, job.project_id, job.asset_path):
        job_store.mark_completed(job.id)
        return
    project_store.update_status(job.user_id, job.project_id, "transcribing")
    asset_file: Path | None = None
    try:
        asset_file = download_asset_to_file(job.asset_path)
        transcript = transcribe_media_file(asset_file, job.content_type)
        if not is_latest_project_asset(job.user_id, job.project_id, job.asset_path):
            job_store.mark_completed(job.id)
            return
        project_store.save_transcript(job.user_id, job.project_id, transcript)
        job_store.mark_completed(job.id)
    except RuntimeError as exc:
        project_store.update_status(job.user_id, job.project_id, "failed", str(exc))
        job_store.mark_failed(job.id, str(exc))
    finally:
        if asset_file is not None:
            asset_file.unlink(missing_ok=True)


def is_latest_project_asset(user_id: str, project_id: str, asset_path: str) -> bool:
    project = project_store.get_project(user_id, project_id)
    if project is None or project.asset is None:
        return False
    return project.asset.storage_path == asset_path
