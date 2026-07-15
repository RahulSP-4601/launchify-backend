import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from app.core.config import get_settings
from app.models.projects import (
    CreateProjectRequest,
    ProjectDetail,
    ProjectRecord,
    ProjectSummary,
    TranscriptResponse,
)
from app.services.auth import get_authenticated_user_id
from app.services.project_store import project_store
from app.services.storage import upload_video_file

router = APIRouter()


@router.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }


@router.get("/projects", response_model=list[ProjectSummary], tags=["projects"])
async def list_projects(request: Request) -> list[ProjectSummary]:
    user_id = get_authenticated_user_id(request)
    projects = project_store.list_projects(user_id)
    return [
        ProjectSummary(
            id=project.id,
            project_name=project.project_name,
            product_name=project.product_name,
            video_goal=project.video_goal,
            status=project.status,
            created_at=project.created_at,
            updated_at=project.updated_at,
            has_transcript=bool(project.transcript),
            has_launch_script=project.launch_script is not None,
            has_edit_plan=project.edit_plan is not None,
        )
        for project in projects
    ]


@router.post("/projects", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_project(payload: CreateProjectRequest, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    project = project_store.create_project(user_id, payload)
    return to_project_detail(user_id, project.id)


@router.get("/projects/{project_id}", response_model=ProjectDetail, tags=["projects"])
async def get_project(project_id: str, request: Request) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
    return to_project_detail(user_id, project_id)


@router.get("/projects/{project_id}/transcript", response_model=TranscriptResponse, tags=["projects"])
async def get_transcript(project_id: str, request: Request) -> TranscriptResponse:
    user_id = get_authenticated_user_id(request)
    project = must_get_project(user_id, project_id)
    return TranscriptResponse(
        project_id=project.id,
        status=project.status,
        transcript=project.transcript,
    )


@router.post("/projects/{project_id}/upload", response_model=ProjectDetail, tags=["projects"])
async def upload_project_video(
    project_id: str,
    request: Request,
    file: UploadFile = File(),
    filename: str | None = Form(default=None),
) -> ProjectDetail:
    user_id = get_authenticated_user_id(request)
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
        product_name=project.product_name,
        product_description=project.product_description,
        target_audience=project.target_audience,
        video_goal=project.video_goal,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        has_transcript=bool(project.transcript),
        has_launch_script=project.launch_script is not None,
        has_edit_plan=project.edit_plan is not None,
        asset=project.asset,
        launch_script=project.launch_script,
        edit_plan=project.edit_plan,
        error_message=project.error_message,
    )


def must_get_project(user_id: str, project_id: str) -> ProjectRecord:
    project = project_store.get_project(user_id, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    return project


async def write_upload_to_temp_file(upload: UploadFile) -> Path:
    with NamedTemporaryFile(delete=False) as temp_file:
        total_bytes = 0
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            total_bytes += len(chunk)
            temp_file.write(chunk)
    await upload.close()
    if total_bytes == 0:
        os.unlink(temp_file.name)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")
    return Path(temp_file.name)
