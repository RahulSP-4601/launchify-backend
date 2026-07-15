from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from app.core.config import get_settings
from app.models.projects import ProjectRecord, RenderedVideoRecord
from app.services.render_payloads import build_render_payload, total_render_duration
from app.services.storage import download_asset_to_file, upload_rendered_video_file


def render_project_videos(user_id: str, project: ProjectRecord) -> tuple[RenderedVideoRecord, RenderedVideoRecord]:
    asset_path = require_asset_path(project)
    with TemporaryDirectory(prefix="launchify-render-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_video = download_asset_to_file(asset_path)
        try:
            preview_video = render_and_upload_variant(user_id, project, source_video, temp_dir, "preview")
            final_video = render_and_upload_variant(user_id, project, source_video, temp_dir, "final")
            return preview_video, final_video
        finally:
            source_video.unlink(missing_ok=True)


def require_asset_path(project: ProjectRecord) -> str:
    if project.asset is None:
        raise RuntimeError("Source asset is required before rendering video outputs.")
    return project.asset.storage_path


def render_and_upload_variant(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    temp_dir: Path,
    quality: Literal["preview", "final"],
) -> RenderedVideoRecord:
    output_path = temp_dir / f"{quality}.mp4"
    render_payload_path = write_render_payload(project, temp_dir, quality)
    invoke_render_worker(render_payload_path, source_video, output_path, quality)
    return upload_variant(user_id, project, output_path, quality)


def write_render_payload(project: ProjectRecord, temp_dir: Path, quality: Literal["preview", "final"]) -> Path:
    payload_path = temp_dir / f"{quality}-payload.json"
    payload_path.write_text(json.dumps(build_render_payload(project, quality)), encoding="utf-8")
    return payload_path


def invoke_render_worker(
    payload_path: Path,
    source_video: Path,
    output_path: Path,
    quality: Literal["preview", "final"],
) -> None:
    worker_dir = Path(get_settings().render_worker_dir).resolve()
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
        subprocess.run(command, cwd=worker_dir, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Render worker dependencies are missing. Install the backend render worker.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.strip() or exc.stdout.strip() or "Render worker failed.") from exc


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
