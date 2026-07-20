from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal

from app.core.config import Settings
from app.models.projects import ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.render_hardening import RenderStageUpdate, notify_render_stage, run_with_retry
from app.services.timing import timed_stage


def preview_render_quality(settings: Settings) -> Literal["preview", "final"]:
    if settings.preview_render_mode == "styled" and settings.fast_pipeline_enabled:
        return "final"
    return "preview"


def execute_preview_pipeline(
    user_id: str,
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    beat: Callable[[Callable[[], None] | None], None],
    prepare_preview_output: Callable[..., None],
    rerender_refined_preview: Callable[..., None],
    reviewed_project: Callable[[ProjectRecord, Path], tuple[ProjectRecord, QualityReportRecord, bool]],
    upload_variant: Callable[..., RenderedVideoRecord],
) -> tuple[RenderedVideoRecord | None, Path, ProjectRecord, QualityReportRecord]:
    preview_output = temp_dir / "preview.mp4"
    reviewed_project_record, quality_report, rerender_preview, preview_quality = reviewed_preview_stage(
        project,
        render_source,
        voiceover_audio,
        temp_dir,
        preview_output,
        settings,
        heartbeat,
        stage_update,
        beat,
        prepare_preview_output,
        reviewed_project,
    )
    preview_video = finalize_preview_stage(
        user_id,
        reviewed_project_record,
        render_source,
        voiceover_audio,
        temp_dir,
        preview_output,
        preview_quality,
        settings,
        heartbeat,
        stage_update,
        preview_ready,
        beat,
        rerender_preview,
        rerender_refined_preview,
        prepare_preview_output,
        upload_variant,
    )
    return preview_video, preview_output, reviewed_project_record, quality_report


def reviewed_preview_stage(
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    preview_output: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    beat: Callable[[Callable[[], None] | None], None],
    prepare_preview_output: Callable[..., None],
    reviewed_project: Callable[[ProjectRecord, Path], tuple[ProjectRecord, QualityReportRecord, bool]],
) -> tuple[ProjectRecord, QualityReportRecord, bool, Literal["preview", "final"]]:
    preview_quality = preview_render_quality(settings)
    beat(heartbeat)
    render_initial_preview_stage(
        project,
        render_source,
        voiceover_audio,
        temp_dir,
        preview_output,
        preview_quality,
        settings,
        heartbeat,
        stage_update,
        prepare_preview_output,
    )
    beat(heartbeat)
    notify_render_stage(stage_update, "preview_review", project.id)
    with timed_stage("preview_review", settings.planning_warn_seconds):
        reviewed_project_record, quality_report, rerender_preview = reviewed_project(project, preview_output)
    beat(heartbeat)
    return reviewed_project_record, quality_report, rerender_preview, preview_quality


def finalize_preview_stage(
    user_id: str,
    reviewed_project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    preview_output: Path,
    preview_quality: Literal["preview", "final"],
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    beat: Callable[[Callable[[], None] | None], None],
    rerender_preview: bool,
    rerender_refined_preview: Callable[..., None],
    prepare_preview_output: Callable[..., None],
    upload_variant: Callable[..., RenderedVideoRecord],
) -> RenderedVideoRecord | None:
    rerender_reviewed_preview_stage(
        reviewed_project,
        render_source,
        voiceover_audio,
        temp_dir,
        preview_output,
        preview_quality,
        settings,
        heartbeat,
        stage_update,
        rerender_preview,
        rerender_refined_preview,
        prepare_preview_output,
    )
    beat(heartbeat)
    preview_video = upload_styled_preview_stage(
        user_id,
        reviewed_project,
        preview_output,
        settings,
        heartbeat,
        stage_update,
        preview_ready,
        upload_variant,
    )
    beat(heartbeat)
    return preview_video


def render_initial_preview_stage(
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    preview_output: Path,
    preview_quality: Literal["preview", "final"],
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    prepare_preview_output: Callable[..., None],
) -> None:
    notify_render_stage(stage_update, "preview_render_initial", project.id)
    with timed_stage("preview_render_initial", settings.preview_render_warn_seconds):
        run_with_retry(
            "preview render",
            lambda: prepare_preview_output(project, render_source, voiceover_audio, temp_dir, preview_output, preview_quality, heartbeat),
        )


def rerender_reviewed_preview_stage(
    reviewed_project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    preview_output: Path,
    preview_quality: Literal["preview", "final"],
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    rerender_preview: bool,
    rerender_refined_preview: Callable[..., None],
    prepare_preview_output: Callable[..., None],
) -> None:
    if not rerender_preview:
        return
    if settings.preview_render_mode == "styled":
        rerender_refined_preview(
            reviewed_project,
            render_source,
            voiceover_audio,
            temp_dir,
            settings,
            heartbeat,
            stage_update,
            preview_output,
            preview_quality,
        )
        return
    notify_render_stage(stage_update, "preview_render_refined", reviewed_project.id)
    with timed_stage("preview_render_refined", settings.preview_render_warn_seconds):
        run_with_retry(
            "refined proxy preview render",
            lambda: prepare_preview_output(
                reviewed_project,
                render_source,
                voiceover_audio,
                temp_dir,
                preview_output,
                preview_quality,
                heartbeat,
            ),
        )


def upload_styled_preview_stage(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    upload_variant: Callable[..., RenderedVideoRecord],
) -> RenderedVideoRecord | None:
    if settings.preview_render_mode != "styled":
        return None
    notify_render_stage(stage_update, "preview_upload", project.id)
    preview_video = run_with_retry(
        "preview upload",
        lambda: upload_variant(user_id, project, preview_output, "preview", heartbeat=heartbeat),
    )
    if preview_ready is not None:
        preview_ready(preview_video)
    return preview_video
