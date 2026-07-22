from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

EditorAspectRatio = Literal["16:9", "9:16", "1:1"]
EditorSceneSource = Literal["edit_plan", "launch_script", "transcript", "fallback"]


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


class ProjectEditorState(BaseModel):
    aspect_ratio: EditorAspectRatio = "16:9"
    selected_scene_id: str = ""
    show_captions: bool = True
    scenes: list[EditorSceneRecord] = Field(default_factory=list)
    captions: list[EditorCaptionRecord] = Field(default_factory=list)


class ProjectEditorStateResponse(BaseModel):
    project_id: str
    editor_state: ProjectEditorState
    updated_at: datetime


class ProjectEditorRegenerateSceneRequest(BaseModel):
    scene_id: str = Field(min_length=1, max_length=120)
    editor_state: ProjectEditorState | None = None
