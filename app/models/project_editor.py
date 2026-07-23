from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProjectEditorConflictError(RuntimeError):
    pass

EditorAspectRatio = Literal["16:9", "9:16", "1:1"]
EditorEditMode = Literal["overwrite", "insert"]
EditorSceneSource = Literal["edit_plan", "launch_script", "transcript", "fallback", "inserted"]
EditorTrackKind = Literal["video", "audio", "caption", "overlay"]
EditorClipKind = Literal["source_video", "inserted_card", "caption", "voiceover"]


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
    text: str = ""
    locked: bool = False
    muted: bool = False


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
    selected_scene_id: str = ""
    selected_track_id: str = ""
    show_captions: bool = True
    scenes: list[EditorSceneRecord] = Field(default_factory=list)
    captions: list[EditorCaptionRecord] = Field(default_factory=list)
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
