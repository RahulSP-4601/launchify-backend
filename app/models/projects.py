from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["draft", "queued", "uploading", "transcribing", "scripting", "planning", "ready", "failed"]
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


class LaunchScriptScene(BaseModel):
    scene_number: int
    purpose: str
    spoken_line: str
    on_screen_text: str
    source_excerpt: str
    estimated_duration_seconds: float


class LaunchScriptRecord(BaseModel):
    hook: str
    summary: str
    title_options: list[str] = Field(default_factory=list)
    scenes: list[LaunchScriptScene] = Field(default_factory=list)
    cta: str
    notes: list[str] = Field(default_factory=list)


class EditPlanCaption(BaseModel):
    start: float
    end: float
    text: str


class EditPlanZoom(BaseModel):
    start: float
    end: float
    scale: float
    focus_region: str
    reason: str


class EditPlanHighlight(BaseModel):
    start: float
    end: float
    label: str
    style: str


class EditPlanScene(BaseModel):
    scene_number: int
    title: str
    purpose: str
    start: float
    end: float
    spoken_line: str
    on_screen_text: str
    source_excerpt: str
    captions: list[EditPlanCaption] = Field(default_factory=list)
    zooms: list[EditPlanZoom] = Field(default_factory=list)
    highlights: list[EditPlanHighlight] = Field(default_factory=list)


class RenderSpecRecord(BaseModel):
    title_card: str
    title_options: list[str] = Field(default_factory=list)
    cta: str
    total_duration_seconds: float


class EditPlanRecord(BaseModel):
    overview: str
    total_duration_seconds: float
    scenes: list[EditPlanScene] = Field(default_factory=list)
    render_spec: RenderSpecRecord


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
    launch_script: LaunchScriptRecord | None = None
    edit_plan: EditPlanRecord | None = None
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
    has_launch_script: bool
    has_edit_plan: bool


class ProjectDetail(ProjectSummary):
    product_description: str
    target_audience: str
    asset: AssetRecord | None = None
    launch_script: LaunchScriptRecord | None = None
    edit_plan: EditPlanRecord | None = None
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
