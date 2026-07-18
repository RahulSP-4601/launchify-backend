from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.core.config import Settings
from app.models.projects import EditPlanRecord, ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.quality_assessor import build_quality_report
from app.services.render_hardening import RenderStageUpdate
from app.services.render_runtime_helpers import require_edit_plan

type GroundedFinalPipelineArgs = tuple[
    str,
    ProjectRecord,
    Path,
    Path | None,
    Path,
    Settings,
    Callable[[], None] | None,
    RenderStageUpdate | None,
    Callable[[RenderedVideoRecord], None] | None,
]


def use_grounded_single_export(project: ProjectRecord) -> bool:
    return project.guide is not None and bool(project.guide.steps)


def current_quality_report(project: ProjectRecord) -> QualityReportRecord:
    if project.quality_report is not None:
        return project.quality_report
    return build_quality_report(project, require_edit_plan(project))


def grounded_render_result(
    preview_video: RenderedVideoRecord,
    project: ProjectRecord,
) -> tuple[RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    return preview_video, require_edit_plan(project), current_quality_report(project)


def grounded_final_pipeline_args(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> GroundedFinalPipelineArgs:
    return (
        user_id,
        project,
        source_video,
        voiceover_audio,
        temp_dir,
        settings,
        heartbeat,
        stage_update,
        final_ready,
    )
