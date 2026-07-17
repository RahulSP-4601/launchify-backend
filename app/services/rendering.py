from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from app.core.config import Settings, get_settings
from app.models.projects import EditPlanRecord, ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.render_hardening import (
    RenderStageUpdate,
    notify_render_stage,
    run_with_retry,
)
from app.services.render_payloads import build_render_payload, total_render_duration
from app.services.render_proxy_preview import (
    persist_proxy_preview_after_final,
    persist_proxy_preview_on_failure,
    prepare_proxy_preview,
)
from app.services.render_runtime_helpers import (
    beat,
    download_voiceover_audio,
    enforce_final_render_limit,
    ensure_render_worker_ready,
    prepare_preview_render_source,
    require_duration,
    require_edit_plan,
    run_process_with_heartbeat,
    upload_variant,
)
from app.services.render_review import refine_from_preview
from app.services.storage import download_asset_to_file
from app.services.timing import timed_stage

logger = logging.getLogger(__name__)


def render_project_videos(
    user_id: str,
    project: ProjectRecord,
    heartbeat: Callable[[], None] | None = None,
    stage_update: RenderStageUpdate | None = None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None = None,
    final_ready: Callable[[RenderedVideoRecord], None] | None = None,
) -> tuple[RenderedVideoRecord | None, RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    asset_path = require_asset_path(project)
    settings = get_settings()
    ensure_render_worker_ready()
    logger.info("Render pipeline using %s preview mode for project %s.", settings.preview_render_mode, project.id)
    with TemporaryDirectory(prefix="launchify-render-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_video = download_asset_to_file(asset_path)
        voiceover_audio = download_voiceover_audio(project)
        preview_render_source: Path | None = None
        try:
            preview_render_source = prepare_preview_render_source(source_video, temp_dir, heartbeat)
            return execute_render_pipeline(
                user_id,
                project,
                source_video,
                preview_render_source,
                voiceover_audio,
                temp_dir,
                settings,
                heartbeat,
                stage_update,
                preview_ready,
                final_ready,
            )
        finally:
            source_video.unlink(missing_ok=True)
            if preview_render_source is not None and preview_render_source != source_video:
                preview_render_source.unlink(missing_ok=True)
            if voiceover_audio is not None:
                voiceover_audio.unlink(missing_ok=True)


def execute_render_pipeline(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    preview_render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> tuple[RenderedVideoRecord | None, RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    preview_video, preview_output, reviewed_project, quality_report = execute_preview_pipeline(
        user_id,
        project,
        preview_render_source,
        voiceover_audio,
        temp_dir,
        settings,
        heartbeat,
        stage_update,
        preview_ready,
    )
    final_render_source = choose_final_render_source(settings, source_video, preview_render_source)
    final_video = execute_final_pipeline_with_preview_fallback(
        user_id,
        reviewed_project,
        final_render_source,
        voiceover_audio,
        temp_dir,
        settings,
        heartbeat,
        stage_update,
        preview_video,
        preview_output,
        preview_ready,
        final_ready,
    )
    preview_video = finalize_preview_video(
        user_id,
        reviewed_project,
        settings,
        preview_video,
        preview_output,
        heartbeat,
        preview_ready,
    )
    return preview_video, final_video, require_edit_plan(reviewed_project), quality_report


def choose_final_render_source(
    settings: Settings,
    source_video: Path,
    preview_render_source: Path,
) -> Path:
    if settings.preview_render_mode == "proxy":
        logger.info("Using prepared proxy source for final render to stay within worker memory limits.")
        return preview_render_source
    return source_video


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
) -> tuple[RenderedVideoRecord | None, Path, ProjectRecord, QualityReportRecord]:
    preview_output = temp_dir / "preview.mp4"
    preview_video: RenderedVideoRecord | None = None
    beat(heartbeat)
    notify_render_stage(stage_update, "preview_render_initial", project.id)
    with timed_stage("preview_render_initial", settings.preview_render_warn_seconds):
        run_with_retry("preview render", lambda: prepare_preview_output(project, render_source, voiceover_audio, temp_dir, preview_output, heartbeat))
    beat(heartbeat)
    notify_render_stage(stage_update, "preview_review", project.id)
    with timed_stage("preview_review", settings.planning_warn_seconds):
        reviewed_project_record, quality_report, rerender_preview = reviewed_project(project, preview_output)
    beat(heartbeat)
    if rerender_preview and settings.preview_render_mode == "styled":
        rerender_refined_preview(
            reviewed_project_record,
            render_source,
            voiceover_audio,
            temp_dir,
            settings,
            heartbeat,
            stage_update,
            preview_output,
        )
    if settings.preview_render_mode == "styled":
        notify_render_stage(stage_update, "preview_upload", reviewed_project_record.id)
        preview_video = run_with_retry(
            "preview upload",
            lambda: upload_variant(user_id, reviewed_project_record, preview_output, "preview", heartbeat=heartbeat),
        )
        if preview_ready is not None:
            preview_ready(preview_video)
    beat(heartbeat)
    return preview_video, preview_output, reviewed_project_record, quality_report


def execute_final_pipeline(
    user_id: str,
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> RenderedVideoRecord:
    final_video = render_final_variant(
        user_id,
        project,
        render_source,
        voiceover_audio,
        temp_dir,
        settings,
        heartbeat,
        stage_update,
    )
    if final_ready is not None:
        final_ready(final_video)
    beat(heartbeat)
    return final_video


def execute_final_pipeline_with_preview_fallback(
    user_id: str,
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_video: RenderedVideoRecord | None,
    preview_output: Path,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> RenderedVideoRecord:
    enforce_final_render_limit(user_id, project)
    if use_proxy_final_output(settings):
        return upload_proxy_final_variant(
            user_id,
            project,
            preview_output,
            heartbeat,
            stage_update,
            final_ready,
        )
    try:
        return execute_final_pipeline(
            user_id,
            project,
            render_source,
            voiceover_audio,
            temp_dir,
            settings,
            heartbeat,
            stage_update,
            final_ready,
        )
    except Exception:
        if preview_video is None and settings.preview_render_mode == "proxy":
            persist_proxy_preview_on_failure(
                user_id,
                project,
                preview_output,
                heartbeat,
                preview_ready,
                upload_proxy_preview_variant,
            )
        raise


def use_proxy_final_output(settings: Settings) -> bool:
    return settings.low_memory_final_mode == "proxy"


def finalize_preview_video(
    user_id: str,
    project: ProjectRecord,
    settings: Settings,
    preview_video: RenderedVideoRecord | None,
    preview_output: Path,
    heartbeat: Callable[[], None] | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
) -> RenderedVideoRecord | None:
    if preview_video is not None or settings.preview_render_mode != "proxy":
        return preview_video
    return persist_proxy_preview_after_final(
        user_id,
        project,
        preview_output,
        heartbeat,
        preview_ready,
        upload_proxy_preview_variant,
    )


def rerender_refined_preview(
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    preview_output: Path,
) -> None:
    notify_render_stage(stage_update, "preview_render_refined", project.id)
    with timed_stage("preview_render_refined", settings.preview_render_warn_seconds):
        run_with_retry(
            "refined preview render",
            lambda: render_preview_output(project, render_source, voiceover_audio, temp_dir, preview_output, heartbeat),
        )
    beat(heartbeat)


def render_final_variant(
    user_id: str,
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
) -> RenderedVideoRecord:
    notify_render_stage(stage_update, "final_render", project.id)
    with timed_stage("final_render", settings.final_render_warn_seconds):
        return run_with_retry(
            "final render and upload",
            lambda: render_and_upload_variant(
                user_id,
                project,
                render_source,
                voiceover_audio,
                temp_dir,
                "final",
                heartbeat,
                stage_update,
            ),
        )


def require_asset_path(project: ProjectRecord) -> str:
    if project.asset is None:
        raise RuntimeError("Source asset is required before rendering video outputs.")
    return project.asset.storage_path


def render_and_upload_variant(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    quality: Literal["preview", "final"],
    heartbeat: Callable[[], None] | None = None,
    stage_update: RenderStageUpdate | None = None,
) -> RenderedVideoRecord:
    output_path = temp_dir / f"{quality}.mp4"
    render_payload_path = write_render_payload(project, temp_dir, quality, voiceover_audio)
    invoke_render_worker(render_payload_path, source_video, output_path, quality, heartbeat=heartbeat)
    notify_render_stage(stage_update, f"{quality}_upload", project.id)
    return upload_variant(user_id, project, output_path, quality, heartbeat=heartbeat)


def render_preview_output(
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    output_path: Path,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    render_payload_path = write_render_payload(project, temp_dir, "preview", voiceover_audio)
    invoke_render_worker(render_payload_path, source_video, output_path, "preview", heartbeat=heartbeat)


def prepare_preview_output(
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    output_path: Path,
    heartbeat: Callable[[], None] | None = None,
) -> None:
    if get_settings().preview_render_mode == "proxy":
        prepare_proxy_preview(source_video, output_path)
        return
    render_preview_output(project, source_video, voiceover_audio, temp_dir, output_path, heartbeat)


def upload_proxy_preview_variant(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    heartbeat: Callable[[], None] | None,
) -> RenderedVideoRecord:
    return run_with_retry(
        "proxy preview upload",
        lambda: upload_variant(user_id, project, preview_output, "preview", heartbeat=heartbeat),
    )


def upload_proxy_final_variant(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    heartbeat: Callable[[], None] | None,
    stage_update: RenderStageUpdate | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> RenderedVideoRecord:
    logger.info("Publishing reviewed preview output as final video for project %s to stay within starter memory limits.", project.id)
    notify_render_stage(stage_update, "final_render", project.id)
    notify_render_stage(stage_update, "final_upload", project.id)
    final_video = run_with_retry(
        "proxy final upload",
        lambda: upload_variant(user_id, project, preview_output, "final", heartbeat=heartbeat),
    )
    if final_ready is not None:
        final_ready(final_video)
    beat(heartbeat)
    return final_video


def write_render_payload(
    project: ProjectRecord,
    temp_dir: Path,
    quality: Literal["preview", "final"],
    voiceover_audio: Path | None,
) -> Path:
    payload_path = temp_dir / f"{quality}-payload.json"
    payload = build_render_payload(project, quality, str(voiceover_audio) if voiceover_audio is not None else "")
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload_path


def invoke_render_worker(
    payload_path: Path,
    source_video: Path,
    output_path: Path,
    quality: Literal["preview", "final"],
    heartbeat: Callable[[], None] | None = None,
) -> None:
    settings = get_settings()
    worker_dir = Path(settings.render_worker_dir).resolve()
    env = os.environ.copy()
    env["RENDER_CONCURRENCY"] = str(settings.render_concurrency)
    env["RENDER_OFFTHREAD_VIDEO_THREADS"] = str(settings.render_offthread_video_threads)
    env["RENDER_MEDIA_CACHE_SIZE_MB"] = str(settings.render_media_cache_size_mb)
    env["RENDER_OFFTHREAD_VIDEO_CACHE_SIZE_MB"] = str(settings.render_offthread_video_cache_size_mb)
    if quality == "final" and settings.preview_render_mode == "proxy":
        env["RENDER_SCALE"] = str(settings.low_memory_final_render_scale)
    command = [
        "npm",
        "run",
        f"render:{quality}",
        "--",
        "--input",
        str(payload_path),
        "--source",
        str(source_video),
        "--output",
        str(output_path),
    ]
    try:
        run_process_with_heartbeat(
            command,
            timeout_seconds=settings.render_timeout_seconds,
            heartbeat=heartbeat,
            cwd=worker_dir,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Render worker dependencies are missing. Install the backend render worker.") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Render worker timed out after {settings.render_timeout_seconds} seconds.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Render worker failed while producing the {quality} video.") from exc


def reviewed_project(
    project: ProjectRecord,
    preview_output: Path,
) -> tuple[ProjectRecord, QualityReportRecord, bool]:
    refined_edit_plan, quality_report, rerender_preview = refine_from_preview(project, preview_output)
    return (
        project.model_copy(update={"edit_plan": refined_edit_plan, "quality_report": quality_report}),
        quality_report,
        rerender_preview,
    )
