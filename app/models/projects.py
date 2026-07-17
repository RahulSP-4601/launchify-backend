from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProjectStatus = Literal["draft", "queued", "uploading", "transcribing", "scripting", "planning", "rendering", "ready", "failed"]
JobStatus = Literal["pending", "processing", "completed", "failed"]
VoiceoverMode = Literal["original", "voiceover", "mixed"]
VoiceoverStatus = Literal["disabled", "script_only", "ready"]
ThemeName = Literal["clean", "spotlight", "bold"]
CaptionProfile = Literal["product", "minimal", "cinematic"]
MotionProfile = Literal["balanced", "dynamic", "calm"]
IssueSeverity = Literal["low", "medium", "high"]
TransitionStyle = Literal["cut", "fade", "slide-up", "focus-push"]


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


class FocusBox(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)


class UiElementRecord(BaseModel):
    label: str
    role: str = "control"
    confidence: float = Field(ge=0.0, le=1.0)
    box: FocusBox


class FrameSignalRecord(BaseModel):
    timestamp: float
    summary: str = ""
    cursor_box: FocusBox | None = None
    click_target_box: FocusBox | None = None
    dominant_box: FocusBox | None = None
    click_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    diff_score: float = Field(ge=0.0, le=1.0, default=0.0)
    importance_score: float = Field(ge=0.0, le=1.0, default=0.0)
    ui_elements: list[UiElementRecord] = Field(default_factory=list)
    ocr_labels: list[str] = Field(default_factory=list)
    ocr_confidence: float = Field(ge=0.0, le=1.0, default=0.0)


class VisualSceneAnalysisRecord(BaseModel):
    scene_number: int
    start: float
    end: float
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    motion_score: float = Field(ge=0.0, le=1.0)
    click_detected: bool = False
    visible_labels: list[str] = Field(default_factory=list)
    primary_focus_box: FocusBox | None = None
    cursor_box: FocusBox | None = None
    click_target_box: FocusBox | None = None
    frames: list[FrameSignalRecord] = Field(default_factory=list)
    frame_diff_score: float = Field(ge=0.0, le=1.0, default=0.0)
    frame_diff_available: bool = True
    cursor_path_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    ocr_match_score: float = Field(ge=0.0, le=1.0, default=0.0)
    ocr_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    anchor_box: FocusBox | None = None


class EditPlanCaption(BaseModel):
    start: float
    end: float
    text: str
    emphasis_words: list[str] = Field(default_factory=list)
    variant: str = "body"


class EditPlanZoom(BaseModel):
    start: float
    end: float
    scale: float
    focus_region: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    focus_box: FocusBox | None = None
    easing: str = "ease-in-out"
    x_offset: float = 0.0
    y_offset: float = 0.0
    smoothing: float = 0.0
    hold_ratio: float = 0.0


class EditPlanHighlight(BaseModel):
    start: float
    end: float
    label: str
    style: str
    anchor_region: str
    confidence: float = Field(ge=0.0, le=1.0)
    focus_box: FocusBox | None = None
    placement_preference: str = "avoid-ui-cover"
    ui_label: str = ""


class EditPlanScene(BaseModel):
    scene_number: int
    title: str
    purpose: str
    start: float
    end: float
    confidence: float = Field(ge=0.0, le=1.0)
    camera_mode: Literal["static", "focus"]
    decision_summary: str
    visual_summary: str
    spoken_line: str
    on_screen_text: str
    source_excerpt: str
    action_timestamp: float | None = None
    transition_style: TransitionStyle = "fade"
    transition_duration_seconds: float = 0.32
    captions: list[EditPlanCaption] = Field(default_factory=list)
    zooms: list[EditPlanZoom] = Field(default_factory=list)
    highlights: list[EditPlanHighlight] = Field(default_factory=list)


class RenderSpecRecord(BaseModel):
    title_card: str
    title_options: list[str] = Field(default_factory=list)
    cta: str
    total_duration_seconds: float


class TemplateConfigRecord(BaseModel):
    theme: ThemeName = "spotlight"
    caption_profile: CaptionProfile = "minimal"
    motion_profile: MotionProfile = "dynamic"


class SceneOverrideRecord(BaseModel):
    scene_number: int
    title: str = ""
    spoken_line: str = ""
    on_screen_text: str = ""
    caption_override: str = ""
    force_zoom: bool | None = None
    force_highlight: bool | None = None
    notes: str = ""


class ManualOverrideRecord(BaseModel):
    scenes: list[SceneOverrideRecord] = Field(default_factory=list)
    updated_at: str = ""


class QualityIssueRecord(BaseModel):
    code: str
    severity: IssueSeverity
    scene_number: int | None = None
    message: str
    suggestion: str


class QualityReportRecord(BaseModel):
    score: int = Field(ge=0, le=100)
    summary: str
    issues: list[QualityIssueRecord] = Field(default_factory=list)
    ready_for_export: bool = False


class BenchmarkMetricRecord(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    detail: str


class BenchmarkReportRecord(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    verdict: str
    metrics: list[BenchmarkMetricRecord] = Field(default_factory=list)


class VoiceoverCueRecord(BaseModel):
    scene_number: int
    start: float
    end: float
    text: str
    duration_seconds: float


class VoiceoverRecord(BaseModel):
    provider: str = "deepgram"
    model: str = "aura-2-thalia-en"
    mode: VoiceoverMode = "original"
    status: VoiceoverStatus = "disabled"
    script: str = ""
    cues: list[VoiceoverCueRecord] = Field(default_factory=list)
    audio_storage_path: str = ""
    duration_seconds: float = 0.0


class EditPlanRecord(BaseModel):
    overview: str
    total_duration_seconds: float
    scenes: list[EditPlanScene] = Field(default_factory=list)
    render_spec: RenderSpecRecord


class RenderedVideoRecord(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
    storage_path: str
    duration_seconds: float
    variant: Literal["preview", "final"]


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
    template_config: TemplateConfigRecord | None = None
    manual_overrides: ManualOverrideRecord | None = None
    quality_report: QualityReportRecord | None = None
    benchmark_report: BenchmarkReportRecord | None = None
    voiceover: VoiceoverRecord | None = None
    preview_video: RenderedVideoRecord | None = None
    final_video: RenderedVideoRecord | None = None
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
    has_quality_report: bool
    has_benchmark_report: bool
    has_voiceover: bool
    has_preview_video: bool
    has_final_video: bool


class ProjectDetail(ProjectSummary):
    product_description: str
    target_audience: str
    asset: AssetRecord | None = None
    launch_script: LaunchScriptRecord | None = None
    edit_plan: EditPlanRecord | None = None
    template_config: TemplateConfigRecord | None = None
    manual_overrides: ManualOverrideRecord | None = None
    quality_report: QualityReportRecord | None = None
    benchmark_report: BenchmarkReportRecord | None = None
    voiceover: VoiceoverRecord | None = None
    preview_video: RenderedVideoRecord | None = None
    final_video: RenderedVideoRecord | None = None
    error_message: str = ""


class UpdatePhaseFourRequest(BaseModel):
    template_config: TemplateConfigRecord = Field(default_factory=TemplateConfigRecord)
    manual_overrides: ManualOverrideRecord = Field(default_factory=ManualOverrideRecord)
    voiceover_mode: VoiceoverMode = "original"


class TranscriptResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    transcript: list[TranscriptSegment]


class UsageSummary(BaseModel):
    limit_seconds: float
    used_seconds: float
    remaining_seconds: float
    blocked: bool


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
