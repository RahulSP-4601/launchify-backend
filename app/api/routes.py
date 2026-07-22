import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.core.config import get_settings
from app.models.projects import (
    CreateRecordingSessionRequest,
    CreateProjectRequest,
    ProjectDetail,
    ProjectRecord,
    ProjectSummary,
    RenderedVideoRecord,
    TranscriptResponse,
    UpdateProjectRequest,
    UpdateRecordingSessionRequest,
    UpdatePhaseFourRequest,
    UsageSummary,
)
from app.services.auth import get_authenticated_user_id
from app.services.phase_four import apply_phase_four_update
from app.services.project_store import project_store
from app.services.project_summary_store import project_summary_store
from app.services.storage import download_asset_to_file, upload_video_file
from app.services.usage_service import total_rendered_seconds
from app.services.voiceover import downloadable_voiceover_audio

router = APIRouter()
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_MB = 50


@router.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
        "process_role": settings.process_role,
        "job_runner_enabled": "true" if settings.should_run_job_runner else "false",
    }


@router.get("/projects", response_model=list[ProjectSummary], tags=["projects"])
async def list_projects(request: Request) -> list[ProjectSummary]:
    user_id = get_authenticated_user_id(request)
    return project_summary_store.list_projects(user_id)


@router.get("/usage", response_model=UsageSummary, tags=["projects"])
async def get_usage(request: Request) -> UsageSummary:
    user_id = get_authenticated_user_id(request)
    return usage_summary_for_user(user_id)


@router.post("/projects", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_project(payload: CreateProjectRequest, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    project = project_store.create_project(user_id, payload)
    return to_project_detail(user_id, project.id)


@router.get("/projects/{project_id}", response_model=ProjectDetail, tags=["projects"])
async def get_project(project_id: str, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    return to_project_detail(user_id, project_id)


@router.put("/projects/{project_id}", response_model=ProjectDetail, tags=["projects"])
async def update_project(project_id: str, payload: UpdateProjectRequest, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    must_get_project(user_id, project_id)
    project_store.rename_project(user_id, project_id, payload.project_name)
    return to_project_detail(user_id, project_id)


@router.put("/projects/{project_id}/phase4", response_model=ProjectDetail, tags=["projects"])
async def update_phase_four(project_id: str, payload: UpdatePhaseFourRequest, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    if project.edit_plan is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Edit plan is required before Phase 4 updates.")
    refined_edit_plan, quality_report, benchmark_report, voiceover = apply_phase_four_update(
        user_id,
        project,
        project.edit_plan,
        payload.template_config,
        payload.manual_overrides,
        payload.voiceover_mode,
    )
    project_store.save_refined_edit_plan(user_id, project.id, refined_edit_plan)
    project_store.save_phase_four_state(
        user_id,
        project.id,
        quality_report,
        benchmark_report,
        voiceover,
        payload.template_config,
        payload.manual_overrides,
    )
    return to_project_detail(user_id, project.id)


@router.put("/projects/{project_id}/session", response_model=ProjectDetail, tags=["projects"])
async def update_recording_session(project_id: str, payload: UpdateRecordingSessionRequest, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    must_get_project(user_id, project_id)
    project_store.save_recording_session(user_id, project_id, payload.recording_session)
    return to_project_detail(user_id, project_id)


@router.post("/projects/{project_id}/sessions", response_model=ProjectDetail, tags=["projects"])
async def create_recording_session(
    project_id: str,
    request: Request,
    file: UploadFile = File(),
    recording_session: str = Form(),
    filename: str | None = Form(default=None),
) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    usage = usage_summary_for_user(user_id)
    if usage.blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your 10 minute trial limit has been reached. Uploads are blocked for now.",
        )
    upload_name = (filename or file.filename or "").strip()
    if not upload_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required.")
    project = must_get_project(user_id, project_id)
    try:
        session_payload = CreateRecordingSessionRequest.model_validate_json(recording_session)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Recording session payload is invalid JSON.") from exc
    temp_path = await write_upload_to_temp_file(file)
    try:
        asset = upload_video_file(
            user_id,
            project.id,
            upload_name,
            file.content_type or "application/octet-stream",
            temp_path,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    project_store.attach_session_asset_and_queue_job(user_id, project.id, asset, session_payload.recording_session)
    return to_project_detail(user_id, project.id)


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse, tags=["projects"])
async def get_transcript(project_id: str, request: Request) -> TranscriptResponse:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    return TranscriptResponse(
        project_id=project.id,
        status=project.status,
        transcript=project.transcript,
    )


@router.get("/projects/{project_id}/renders/{variant}", tags=["projects"])
async def get_render_output(project_id: str, variant: str, request: Request) -> FileResponse:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    rendered_video = require_render_output(project, variant)
    output_path = download_asset_to_file(rendered_video.storage_path)
    return FileResponse(
        path=output_path,
        media_type=rendered_video.content_type,
        filename=rendered_video.filename,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
        headers={"Content-Disposition": f'inline; filename="{rendered_video.filename}"'},
    )


@router.get("/projects/{project_id}/assets/source", tags=["projects"])
async def get_source_asset(project_id: str, request: Request) -> FileResponse:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    if project.asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source asset not found.")
    output_path = download_asset_to_file(project.asset.storage_path)
    return FileResponse(
        path=output_path,
        media_type=project.asset.content_type,
        filename=project.asset.filename,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
        headers={"Content-Disposition": f'inline; filename="{project.asset.filename}"'},
    )


@router.get("/projects/{project_id}/assets/voiceover", tags=["projects"])
async def get_voiceover_asset(project_id: str, request: Request) -> FileResponse:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    if project.voiceover is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voiceover asset not found.")
    output_path = downloadable_voiceover_audio(project.voiceover)
    if output_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voiceover asset not found.")
    filename = f"{project.project_name.lower().replace(' ', '-')}-voiceover.mp3"
    return FileResponse(
        path=output_path,
        media_type="audio/mpeg",
        filename=filename,
        background=BackgroundTask(output_path.unlink, missing_ok=True),
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.post("/projects/{project_id}/upload", response_model=ProjectDetail, tags=["projects"])
async def upload_project_video(
    project_id: str,
    request: Request,
    file: UploadFile = File(),
    filename: str | None = Form(default=None),
) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    usage = usage_summary_for_user(user_id)
    if usage.blocked:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your 10 minute trial limit has been reached. Uploads are blocked for now.",
        )
    upload_name = (filename or file.filename or "").strip()
    if not upload_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename is required.")
    project = must_get_project(user_id, project_id)
    temp_path = await write_upload_to_temp_file(file)
    try:
        asset = upload_video_file(
            user_id,
            project.id,
            upload_name,
            file.content_type or "application/octet-stream",
            temp_path,
        )
    finally:
        temp_path.unlink(missing_ok=True)
    project_store.attach_asset_and_queue_job(user_id, project.id, asset)
    return to_project_detail(user_id, project.id)


def to_project_detail(user_id: str, project_id: str) -> ProjectDetail:
    project = must_get_project(user_id, project_id)
    return ProjectDetail(
        id=project.id,
        project_name=project.project_name,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        has_transcript=bool(project.transcript),
        has_guide=project.guide is not None and bool(project.guide.steps),
        has_launch_script=project.launch_script is not None,
        has_edit_plan=project.edit_plan is not None,
        has_quality_report=project.quality_report is not None,
        has_benchmark_report=project.benchmark_report is not None,
        has_voiceover=project.voiceover is not None and bool(project.voiceover.script),
        has_preview_video=project.preview_video is not None,
        asset=project.asset,
        recording_session=project.recording_session,
        guide=project.guide,
        launch_script=project.launch_script,
        edit_plan=project.edit_plan,
        template_config=project.template_config,
        manual_overrides=project.manual_overrides,
        quality_report=project.quality_report,
        benchmark_report=project.benchmark_report,
        voiceover=project.voiceover,
        preview_video=project.preview_video,
        error_message=project.error_message,
    )


def must_get_project(user_id: str, project_id: str) -> ProjectRecord:
    project = project_store.get_project(user_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


def require_render_output(project: ProjectRecord, variant: str) -> RenderedVideoRecord:
    if variant in {"preview", "final"} and project.preview_video is not None:
        return project.preview_video
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rendered video not found.")


def usage_summary_for_user(user_id: str) -> UsageSummary:
    settings = get_settings()
    limit_seconds = float(settings.trial_minutes_limit * 60)
    used_seconds = total_rendered_seconds(user_id)
    remaining_seconds = max(limit_seconds - used_seconds, 0.0)
    return UsageSummary(
        limit_seconds=limit_seconds,
        used_seconds=used_seconds,
        remaining_seconds=remaining_seconds,
        blocked=remaining_seconds <= 0,
    )


async def write_upload_to_temp_file(upload: UploadFile) -> Path:
    with NamedTemporaryFile(delete=False) as temp_file:
        total_bytes = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > MAX_UPLOAD_BYTES:
                await upload.close()
                temp_file.close()
                os.unlink(temp_file.name)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Uploaded file must be {MAX_UPLOAD_MB} MB or smaller.",
                )
            temp_file.write(chunk)
    await upload.close()
    if total_bytes == 0:
        os.unlink(temp_file.name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    return Path(temp_file.name)
