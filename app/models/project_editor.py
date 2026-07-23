from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProjectEditorConflictError(RuntimeError):
    pass

EditorAspectRatio = Literal["16:9", "9:16", "1:1"]
EditorEditMode = Literal["overwrite", "insert"]
EditorSceneSource = Literal["edit_plan", "launch_script", "transcript", "fallback", "inserted", "imported"]
EditorTrackKind = Literal["video", "audio", "caption", "overlay"]
EditorMediaAssetKind = Literal["audio", "video"]
EditorMediaAssetSource = Literal["project_source", "project_voiceover", "uploaded", "imported"]
EditorClipKind = Literal[
    "source_video",
    "inserted_card",
    "caption",
    "voiceover",
    "media_audio",
    "media_video",
    "text_overlay",
    "shape_overlay",
    "effect_overlay",
]


class EditorSceneRecord(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    scene_number: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=240)
    spoken_line: str = ""
    on_screen_text: str = ""
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    source: EditorSceneSource


class EditorCaptionRecord(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str = ""
    scene_id: str | None = None


class EditorCommentRecord(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    scene_id: str | None = None
    body: str = ""
    time: float = Field(ge=0.0)
    created_at: str = ""


class ProjectEditorToolState(BaseModel):
    active_shape: Literal["rectangle", "ellipse", "polygon", "star", "line", "arrow"] | None = None
    active_effect: Literal["blur", "callout", "spotlight", "zoom"] | None = None
    active_caption_preset: Literal["basic", "basic_karaoke", "highlight_box", "karaoke_highlight_box"] = "basic"
    media_tab: Literal["project", "saved", "stock"] = "project"
    pending_media_intent: Literal["upload_file", "import_project"] | None = None


class EditorMediaAssetRecord(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    project_id: str = Field(min_length=1, max_length=120)
    kind: EditorMediaAssetKind
    source: EditorMediaAssetSource
    title: str = Field(min_length=1, max_length=240)
    storage_path: str = Field(min_length=1, max_length=512)
    content_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(ge=0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    source_project_id: str | None = None
    created_at: datetime
    updated_at: datetime


class EditorClipRecord(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    track_id: str = Field(min_length=1, max_length=120)
    kind: EditorClipKind
    title: str = ""
    scene_id: str | None = None
    timeline_start: float = Field(ge=0.0)
    timeline_end: float = Field(ge=0.0)
    source_start: float | None = Field(default=None, ge=0.0)
    source_end: float | None = Field(default=None, ge=0.0)
    asset_path: str | None = None
    content_type: str | None = None
    source_project_id: str | None = None
    style_preset: str | None = None
    effect_preset: str | None = None
    text: str = ""
    locked: bool = False
    muted: bool = False
    volume_percent: int | None = Field(default=None, ge=0, le=400)
    fade_in_seconds: float | None = Field(default=None, ge=0.0)
    fade_out_seconds: float | None = Field(default=None, ge=0.0)
    loop: bool | None = None


class EditorTrackRecord(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    kind: EditorTrackKind
    name: str = Field(min_length=1, max_length=120)
    locked: bool = False
    muted: bool = False
    clips: list[EditorClipRecord] = Field(default_factory=list)


class ProjectEditorSequence(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1, default=1)
    duration_seconds: float = Field(ge=0.0, default=0.0)
    playhead_seconds: float = Field(ge=0.0, default=0.0)
    tracks: list[EditorTrackRecord] = Field(default_factory=list)


class ProjectEditorState(BaseModel):
    aspect_ratio: EditorAspectRatio = "16:9"
    edit_mode: EditorEditMode = "overwrite"
    selected_clip_id: str | None = None
    selected_scene_id: str = ""
    selected_track_id: str = ""
    show_captions: bool = True
    scenes: list[EditorSceneRecord] = Field(default_factory=list)
    captions: list[EditorCaptionRecord] = Field(default_factory=list)
    comments: list[EditorCommentRecord] = Field(default_factory=list)
    tool_state: ProjectEditorToolState | None = None
    sequence: ProjectEditorSequence | None = None


class ProjectEditorStateResponse(BaseModel):
    project_id: str
    editor_state: ProjectEditorState
    updated_at: datetime
    head_revision_id: int | None = None


class ProjectEditorRevisionSummary(BaseModel):
    id: int
    project_id: str
    created_at: datetime
    scene_count: int = Field(ge=0)
    sequence_version: int = Field(ge=1, default=1)
    parent_revision_id: int | None = None


class ProjectEditorRevisionRecord(BaseModel):
    project_id: str
    revision: ProjectEditorRevisionSummary
    editor_state: ProjectEditorState
    updated_at: datetime
    head_revision_id: int | None = None


class ProjectEditorSaveRequest(BaseModel):
    editor_state: ProjectEditorState
    base_revision_id: int | None = None


class ProjectEditorRegenerateSceneRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=120)
    editor_state: ProjectEditorState | None = None
    base_revision_id: int | None = None


class ProjectEditorMediaAssetUploadResponse(BaseModel):
    asset: EditorMediaAssetRecord


class ProjectEditorMediaAssetListResponse(BaseModel):
    assets: list[EditorMediaAssetRecord] = Field(default_factory=list)


class ProjectEditorMediaAssetImportRequest(BaseModel):
    source_project_id: str = Field(min_length=1, max_length=120)
    asset_id: str | None = Field(default=None, min_length=1, max_length=160)
    variant: Literal["source", "voiceover", "asset"] = "source"
    duration_seconds: float | None = Field(default=None, ge=0.0)
