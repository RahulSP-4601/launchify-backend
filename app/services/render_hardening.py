from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Literal

from app.core.config import get_settings
from app.models.projects import RenderedVideoRecord

logger = logging.getLogger(__name__)
RenderStageUpdate = Callable[[str], None]


def log_render_stage(stage_name: str, project_id: str) -> None:
    logger.info("Render stage %s started for project %s.", stage_name, project_id)


def notify_render_stage(stage_update: RenderStageUpdate | None, stage_name: str, project_id: str) -> None:
    log_render_stage(stage_name, project_id)
    if stage_update is not None:
        stage_update(stage_name)


def run_with_retry[T](label: str, operation: Callable[[], T]) -> T:
    max_attempts = max(1, get_settings().render_retry_attempts + 1)
    last_error: RuntimeError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except RuntimeError as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            logger.warning("Retrying %s after attempt %s/%s failed: %s", label, attempt, max_attempts, exc)
    if last_error is not None:
        raise RuntimeError(f"{label.title()} failed after {max_attempts} attempts: {last_error}") from last_error
    raise RuntimeError(f"{label.title()} failed before it could start.")


def verify_render_artifact(output_path: Path, quality: Literal["preview", "final"]) -> None:
    if not output_path.exists():
        raise RuntimeError(f"{quality.title()} render did not produce a video file.")
    if not output_path.is_file():
        raise RuntimeError(f"{quality.title()} render output path is not a file.")
    file_size = output_path.stat().st_size
    if file_size <= 0:
        raise RuntimeError(f"{quality.title()} render produced an empty video file.")
    metadata = probe_rendered_video(output_path, quality)
    format_info = metadata.get("format", {})
    duration = safe_float(format_info.get("duration"))
    if duration is None or duration <= 0:
        raise RuntimeError(f"{quality.title()} render produced an invalid video with no readable duration.")
    probed_size = safe_int(format_info.get("size"))
    if probed_size is not None and probed_size <= 0:
        raise RuntimeError(f"{quality.title()} render produced an invalid video with zero probed size.")


def verify_uploaded_variant(video: RenderedVideoRecord, quality: Literal["preview", "final"]) -> None:
    if not video.storage_path:
        raise RuntimeError(f"{quality.title()} upload completed without returning a storage path.")
    if video.size_bytes <= 0:
        raise RuntimeError(f"{quality.title()} upload completed with an invalid file size.")


def probe_rendered_video(output_path: Path, quality: Literal["preview", "final"]) -> dict[str, Any]:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size",
        "-of",
        "json",
        str(output_path),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"{quality.title()} render verification requires FFprobe. Configure FFPROBE_BINARY in the backend env."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{quality.title()} render verification timed out.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f" ({detail})" if detail else ""
        raise RuntimeError(f"{quality.title()} render produced an unreadable MP4{suffix}.") from exc
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{quality.title()} render verification returned invalid FFprobe output.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{quality.title()} render verification returned an unexpected FFprobe payload.")
    return payload


def safe_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def safe_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
