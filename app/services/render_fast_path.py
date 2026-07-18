from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from app.core.config import Settings
from app.models.projects import EditPlanRecord, ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.render_hardening import RenderStageUpdate

logger = logging.getLogger(__name__)


def execute_single_preview_pipeline(
    user_id: str,
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    execute_preview_pipeline: Callable[..., tuple[RenderedVideoRecord | None, Path, ProjectRecord, QualityReportRecord]],
    finalize_preview_video: Callable[..., RenderedVideoRecord | None],
    require_edit_plan: Callable[[ProjectRecord], EditPlanRecord],
    beat: Callable[[Callable[[], None] | None], None],
    prepare_preview_output: Callable[..., None],
    rerender_refined_preview: Callable[..., None],
    reviewed_project_fn: Callable[[ProjectRecord, Path], tuple[ProjectRecord, QualityReportRecord, bool]],
    upload_variant: Callable[..., RenderedVideoRecord],
) -> tuple[RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    logger.info("Fast pipeline enabled for project %s; publishing the reviewed preview as the only MVP video output.", project.id)
    preview_video, preview_output, reviewed_project, quality_report = execute_preview_pipeline(
        user_id,
        project,
        render_source,
        voiceover_audio,
        temp_dir,
        settings,
        heartbeat,
        stage_update,
        preview_ready,
        beat,
        prepare_preview_output,
        rerender_refined_preview,
        reviewed_project_fn,
        upload_variant,
    )
    persisted_preview = finalize_preview_video(
        user_id,
        reviewed_project,
        settings,
        preview_video,
        preview_output,
        heartbeat,
        preview_ready,
    )
    if persisted_preview is None:
        raise RuntimeError("Preview render completed but no preview video was persisted.")
    return persisted_preview, require_edit_plan(reviewed_project), quality_report
