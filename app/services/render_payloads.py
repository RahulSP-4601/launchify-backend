from __future__ import annotations

from app.models.projects import EditPlanRecord, ProjectRecord, TemplateConfigRecord, VoiceoverRecord

PREVIEW_DIMENSIONS = {"width": 640, "height": 360, "fps": 20}
FINAL_DIMENSIONS = {"width": 1280, "height": 720, "fps": 30}
INTRO_DURATION_SECONDS = 1.8
OUTRO_DURATION_SECONDS = 2.2


def build_render_payload(
    project: ProjectRecord,
    quality: str,
    voiceover_audio_path: str = "",
) -> dict[str, object]:
    edit_plan = require_edit_plan(project.edit_plan)
    dimensions = render_dimensions(quality)
    return {
        "projectId": project.id,
        "projectName": project.project_name,
        "productName": project.product_name,
        "quality": quality,
        "dimensions": dimensions,
        "introDurationSeconds": INTRO_DURATION_SECONDS,
        "outroDurationSeconds": OUTRO_DURATION_SECONDS,
        "editPlan": edit_plan.model_dump(mode="json"),
        "templateConfig": template_config(project).model_dump(mode="json"),
        "voiceover": voiceover(project).model_dump(mode="json"),
        "voiceoverAudioPath": voiceover_audio_path,
    }


def require_edit_plan(edit_plan: EditPlanRecord | None) -> EditPlanRecord:
    if edit_plan is None:
        raise RuntimeError("Edit plan is required before rendering video outputs.")
    return edit_plan


def render_dimensions(quality: str) -> dict[str, int]:
    if quality == "final":
        return FINAL_DIMENSIONS
    return PREVIEW_DIMENSIONS


def total_render_duration(content_duration_seconds: float) -> float:
    return round(INTRO_DURATION_SECONDS + content_duration_seconds + OUTRO_DURATION_SECONDS, 2)


def template_config(project: ProjectRecord) -> TemplateConfigRecord:
    return project.template_config or TemplateConfigRecord()


def voiceover(project: ProjectRecord) -> VoiceoverRecord:
    return project.voiceover or VoiceoverRecord()
