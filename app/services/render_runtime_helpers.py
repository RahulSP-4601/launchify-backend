from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Callable, Literal

from app.core.config import get_settings
from app.models.projects import EditPlanRecord, ProjectRecord, RenderedVideoRecord
from app.services.render_hardening import verify_render_artifact, verify_uploaded_variant
from app.services.render_payloads import total_render_duration
from app.services.render_proxy_clips import proxy_highlight_duration
from app.services.storage import download_asset_to_file, upload_rendered_video_file
from app.services.usage_service import projected_rendered_seconds, total_rendered_seconds

logger = logging.getLogger(__name__)


def prepare_preview_render_source(
    source_video: Path,
    temp_dir: Path,
    heartbeat: Callable[[], None] | None = None,
) -> Path:
    settings = get_settings()
    return prepare_render_source(
        source_video,
        temp_dir / "render-source.mp4",
        fps=settings.preview_proxy_fps,
        width=settings.preview_proxy_width,
        crf=23,
        heartbeat=heartbeat,
        label="Render source",
    )


def prepare_final_render_source(
    source_video: Path,
    temp_dir: Path,
    heartbeat: Callable[[], None] | None = None,
) -> Path:
    settings = get_settings()
    return prepare_render_source(
        source_video,
        temp_dir / "final-render-source.mp4",
        fps=settings.final_render_fps,
        width=settings.final_render_width,
        crf=20,
        heartbeat=heartbeat,
        label="Final render source",
    )


def prepare_render_source(
    source_video: Path,
    output_path: Path,
    *,
    fps: int,
    width: int,
    crf: int,
    heartbeat: Callable[[], None] | None,
    label: str,
) -> Path:
    settings = get_settings()
    command = [
        settings.ffmpeg_binary,
        "-y",
        "-i",
        str(source_video),
        "-vf",
        f"fps={fps},scale='min(iw,{width})':-2",
        "-threads",
        "1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        str(output_path),
    ]
    run_ffmpeg_render_source(command, heartbeat, label)
    return output_path


def run_ffmpeg_render_source(command: list[str], heartbeat: Callable[[], None] | None, label: str) -> None:
    settings = get_settings()
    timeout_message = f"{label} preparation timed out after {settings.render_timeout_seconds} seconds."
    failure_message = f"{label} preparation failed before rendering started."
    try:
        run_process_with_heartbeat(command, timeout_seconds=settings.render_timeout_seconds, heartbeat=heartbeat)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for video rendering. Configure FFMPEG_BINARY in the backend env.") from exc
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        raise RuntimeError(timeout_message) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(failure_message) from exc


def upload_variant(
    user_id: str,
    project: ProjectRecord,
    output_path: Path,
    quality: Literal["preview", "final"],
    heartbeat: Callable[[], None] | None = None,
) -> RenderedVideoRecord:
    verify_render_artifact(output_path, quality)
    duration = output_duration_seconds(output_path, fallback=require_duration(project))
    uploaded_video = upload_rendered_video_file(
        user_id=user_id,
        project_id=project.id,
        variant=quality,
        filename=f"{project.project_name.lower().replace(' ', '-')}-{quality}.mp4",
        source_path=output_path,
        duration_seconds=duration,
        heartbeat=heartbeat,
    )
    verify_uploaded_variant(uploaded_video, quality)
    return uploaded_video


def require_duration(project: ProjectRecord) -> float:
    if project.edit_plan is None:
        raise RuntimeError("Edit plan duration is required before uploading rendered outputs.")
    return total_render_duration(project.edit_plan.total_duration_seconds)


def output_duration_seconds(output_path: Path, fallback: float) -> float:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
        return fallback
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return fallback
    return round(duration, 2) if duration > 0 else fallback


def enforce_final_render_limit(user_id: str, project: ProjectRecord) -> None:
    settings = get_settings()
    duration_seconds = effective_final_duration(project, settings)
    projected_seconds = projected_rendered_seconds(user_id, project.id, duration_seconds)
    limit_seconds = float(settings.trial_minutes_limit * 60)
    if projected_seconds > limit_seconds:
        remaining_seconds = max(limit_seconds - total_rendered_seconds(user_id), 0.0)
        remaining_minutes = remaining_seconds / 60
        raise RuntimeError(
            "This render would exceed your trial limit. "
            f"Only {remaining_minutes:.1f} minutes remain before the {settings.trial_minutes_limit} minute cap."
        )


def effective_final_duration(project: ProjectRecord, settings: object) -> float:
    if getattr(settings, "low_memory_final_mode", "render") != "proxy":
        return require_duration(project)
    return proxy_highlight_duration(project) or require_duration(project)


def download_voiceover_audio(project: ProjectRecord) -> Path | None:
    if project.voiceover is None or not project.voiceover.audio_storage_path:
        return None
    return download_asset_to_file(project.voiceover.audio_storage_path)


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
