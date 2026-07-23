from __future__ import annotations

from app.core.config import get_settings
from app.models.project_editor import ProjectEditorState
from app.models.projects import EditPlanRecord, ProjectRecord, TemplateConfigRecord, VoiceoverRecord
from app.services.project_editor_render_timeline import build_render_timeline
from app.services.preview_manifest import manifest_edit_plan
INTRO_DURATION_SECONDS = 1.0
OUTRO_DURATION_SECONDS = 1.4


def build_render_payload(
    project: ProjectRecord,
    quality: str,
    editor_state: ProjectEditorState | None = None,
    voiceover_audio_path: str = "",
) -> dict[str, object]:
    edit_plan = manifest_edit_plan(project, quality) if quality == "preview" else require_edit_plan(project.edit_plan)
    timeline = build_render_timeline(project, editor_state)
    dimensions = render_dimensions(quality)
    return {
        "projectId": project.id,
        "projectName": project.project_name,
        "productName": project.project_name,
        "quality": quality,
        "dimensions": dimensions,
        "introDurationSeconds": INTRO_DURATION_SECONDS,
        "outroDurationSeconds": OUTRO_DURATION_SECONDS,
        "editPlan": edit_plan.model_dump(mode="json"),
        "timeline": timeline,
        "templateConfig": template_config(project).model_dump(mode="json"),
        "voiceover": voiceover(project).model_dump(mode="json"),
        "voiceoverAudioPath": voiceover_audio_path,
    }


def require_edit_plan(edit_plan: EditPlanRecord | None) -> EditPlanRecord:
    if edit_plan is None:
        raise RuntimeError("Edit plan is required before rendering video outputs.")
    return edit_plan


def render_dimensions(quality: str) -> dict[str, int]:
    settings = get_settings()
    if quality == "final":
        return {
            "width": settings.final_render_width,
            "height": settings.final_render_height,
            "fps": settings.final_render_fps,
        }
    return {
        "width": settings.preview_proxy_width,
        "height": settings.preview_proxy_height,
        "fps": settings.preview_proxy_fps,
    }


def total_render_duration(content_duration_seconds: float) -> float:
    return round(INTRO_DURATION_SECONDS + content_duration_seconds + OUTRO_DURATION_SECONDS, 2)


def template_config(project: ProjectRecord) -> TemplateConfigRecord:
    return project.template_config or TemplateConfigRecord()


def voiceover(project: ProjectRecord) -> VoiceoverRecord:
    return project.voiceover or VoiceoverRecord()
