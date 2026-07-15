from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["draft", "queued", "uploading", "transcribing", "ready", "failed"]
JobStatus = Literal["pending", "processing", "completed", "failed"]


class CreateProjectRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=120)
    product_description: str = Field(default="", max_length=1000)
    target_audience: str = Field(default="", max_length=240)
    video_goal: str = Field(default="launch_video", max_length=120)


class AssetRecord(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class ProjectRecord(BaseModel):
    id: str
    project_name: str
    product_name: str
    product_description: str
    target_audience: str
    video_goal: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime
    asset: AssetRecord | None = None
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    error_message: str = ""


class ProjectSummary(BaseModel):
    id: str
    project_name: str
    product_name: str
    video_goal: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime
    has_transcript: bool


class ProjectDetail(ProjectSummary):
    product_description: str
    target_audience: str
    asset: AssetRecord | None = None
    error_message: str = ""


class TranscriptResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    transcript: list[TranscriptSegment]


class ProcessingJobRecord(BaseModel):
    id: str
    user_id: str
    project_id: str
    asset_path: str
    content_type: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    error_message: str = ""
