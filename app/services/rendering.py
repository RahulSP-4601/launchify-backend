from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from app.core.config import Settings, get_settings
from app.models.projects import EditPlanRecord, ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.render_payloads import build_render_payload, total_render_duration
from app.services.render_review import refine_from_preview
from app.services.storage import download_asset_to_file, upload_rendered_video_file
from app.services.timing import timed_stage
from app.services.usage_service import projected_rendered_seconds, total_rendered_seconds

logger = logging.getLogger(__name__)


def render_project_videos(
    user_id: str,
    project: ProjectRecord,
    heartbeat: Callable[[], None] | None = None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None = None,
    final_ready: Callable[[RenderedVideoRecord], None] | None = None,
) -> tuple[RenderedVideoRecord, RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    asset_path = require_asset_path(project)
    settings = get_settings()
    ensure_render_worker_ready()
    with TemporaryDirectory(prefix="launchify-render-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_video = download_asset_to_file(asset_path)
        voiceover_audio = download_voiceover_audio(project)
        render_source: Path | None = None
        try:
            render_source = prepare_render_source(source_video, temp_dir, heartbeat)
            return execute_render_pipeline(
                user_id,
                project,
                source_video,
                render_source,
                voiceover_audio,
                temp_dir,
                settings,
                heartbeat,
                preview_ready,
                final_ready,
            )
        finally:
            source_video.unlink(missing_ok=True)
            if render_source is not None and render_source != source_video:
                render_source.unlink(missing_ok=True)
            if voiceover_audio is not None:
                voiceover_audio.unlink(missing_ok=True)


def execute_render_pipeline(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    preview_ready: Callable[[RenderedVideoRecord], None] | None,
    final_ready: Callable[[RenderedVideoRecord], None] | None,
) -> tuple[RenderedVideoRecord, RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    preview_output = temp_dir / "preview.mp4"
    beat(heartbeat)
    log_render_stage("preview_render_initial", project.id)
    with timed_stage("preview_render_initial", settings.preview_render_warn_seconds):
        render_preview_output(project, render_source, voiceover_audio, temp_dir, preview_output, heartbeat)
    beat(heartbeat)
    log_render_stage("preview_review", project.id)
    with timed_stage("preview_review", settings.planning_warn_seconds):
        reviewed_project, quality_report, rerender_preview = reviewed_project(project, preview_output)
    beat(heartbeat)
    enforce_final_render_limit(user_id, reviewed_project)
    if rerender_preview:
        rerender_refined_preview(reviewed_project, render_source, voiceover_audio, temp_dir, settings, heartbeat, preview_output)
    preview_video = upload_variant(user_id, reviewed_project, preview_output, "preview")
    if preview_ready is not None:
        preview_ready(preview_video)
    beat(heartbeat)
    final_video = render_final_variant(user_id, reviewed_project, source_video, voiceover_audio, temp_dir, settings, heartbeat)
    if final_ready is not None:
        final_ready(final_video)
    beat(heartbeat)
    return preview_video, final_video, require_edit_plan(reviewed_project), quality_report


def rerender_refined_preview(
    project: ProjectRecord,
    render_source: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
    preview_output: Path,
) -> None:
    log_render_stage("preview_render_refined", project.id)
    with timed_stage("preview_render_refined", settings.preview_render_warn_seconds):
        render_preview_output(project, render_source, voiceover_audio, temp_dir, preview_output, heartbeat)
    beat(heartbeat)


def render_final_variant(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    settings: Settings,
    heartbeat: Callable[[], None] | None,
) -> RenderedVideoRecord:
    log_render_stage("final_render", project.id)
    with timed_stage("final_render", settings.final_render_warn_seconds):
        return render_and_upload_variant(
            user_id,
            project,
            source_video,
            voiceover_audio,
            temp_dir,
            "final",
            heartbeat,
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
) -> RenderedVideoRecord:
    output_path = temp_dir / f"{quality}.mp4"
    render_payload_path = write_render_payload(project, temp_dir, quality, voiceover_audio)
    invoke_render_worker(render_payload_path, source_video, output_path, quality, heartbeat=heartbeat)
    return upload_variant(user_id, project, output_path, quality)


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


def run_process_with_heartbeat(
    command: list[str],
    *,
    timeout_seconds: int,
    heartbeat: Callable[[], None] | None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    process = subprocess.Popen(command, cwd=cwd, env=env)
    wait_for_process(process, timeout_seconds, heartbeat)
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def wait_for_process(
    process: subprocess.Popen[bytes],
    timeout_seconds: int,
    heartbeat: Callable[[], None] | None,
) -> None:
    settings = get_settings()
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait(timeout=5)
            raise TimeoutError
        try:
            process.wait(timeout=min(settings.job_heartbeat_interval_seconds, max(remaining, 0.1)))
            return
        except subprocess.TimeoutExpired:
            beat(heartbeat)


def prepare_render_source(
    source_video: Path,
    temp_dir: Path,
    heartbeat: Callable[[], None] | None = None,
) -> Path:
    proxy_path = temp_dir / "render-source.mp4"
    command = [
        get_settings().ffmpeg_binary,
        "-y",
        "-i",
        str(source_video),
        "-vf",
        "scale='min(iw,1280)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(proxy_path),
    ]
    try:
        run_process_with_heartbeat(
            command,
            timeout_seconds=get_settings().render_timeout_seconds,
            heartbeat=heartbeat,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for video rendering. Configure FFMPEG_BINARY in the backend env.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Render source preparation timed out after {get_settings().render_timeout_seconds} seconds.") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Render source preparation timed out after {get_settings().render_timeout_seconds} seconds.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Render source preparation failed before preview rendering started.") from exc
    return proxy_path


def upload_variant(
    user_id: str,
    project: ProjectRecord,
    output_path: Path,
    quality: Literal["preview", "final"],
) -> RenderedVideoRecord:
    if output_path.stat().st_size == 0:
        raise RuntimeError(f"{quality.title()} render produced an empty video file.")
    duration = require_duration(project)
    return upload_rendered_video_file(
        user_id=user_id,
        project_id=project.id,
        variant=quality,
        filename=f"{project.project_name.lower().replace(' ', '-')}-{quality}.mp4",
        source_path=output_path,
        duration_seconds=duration,
    )


def require_duration(project: ProjectRecord) -> float:
    if project.edit_plan is None:
        raise RuntimeError("Edit plan duration is required before uploading rendered outputs.")
    return total_render_duration(project.edit_plan.total_duration_seconds)


def enforce_final_render_limit(user_id: str, project: ProjectRecord) -> None:
    settings = get_settings()
    duration_seconds = require_duration(project)
    projected_seconds = projected_rendered_seconds(user_id, project.id, duration_seconds)
    limit_seconds = float(settings.trial_minutes_limit * 60)
    if projected_seconds > limit_seconds:
        remaining_seconds = max(limit_seconds - total_rendered_seconds(user_id), 0.0)
        remaining_minutes = remaining_seconds / 60
        raise RuntimeError(
            "This render would exceed your trial limit. "
            f"Only {remaining_minutes:.1f} minutes remain before the {settings.trial_minutes_limit} minute cap."
        )


def download_voiceover_audio(project: ProjectRecord) -> Path | None:
    if project.voiceover is None or not project.voiceover.audio_storage_path:
        return None
    return download_asset_to_file(project.voiceover.audio_storage_path)


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


def require_edit_plan(project: ProjectRecord) -> EditPlanRecord:
    if project.edit_plan is None:
        raise RuntimeError("Edit plan is required before saving reviewed render outputs.")
    return project.edit_plan


def ensure_render_worker_ready() -> None:
    worker_dir = Path(get_settings().render_worker_dir).resolve()
    if not worker_dir.exists():
        raise RuntimeError("Render worker directory is missing. Install the backend render worker.")
    if not (worker_dir / "package.json").exists():
        raise RuntimeError("Render worker package.json is missing. Install the backend render worker.")


def beat(heartbeat: Callable[[], None] | None) -> None:
    if heartbeat is not None:
        heartbeat()


def log_render_stage(stage_name: str, project_id: str) -> None:
    logger.info("Render stage %s started for project %s.", stage_name, project_id)
