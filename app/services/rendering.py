from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from app.core.config import get_settings
from app.models.projects import EditPlanRecord, ProjectRecord, QualityReportRecord, RenderedVideoRecord
from app.services.render_payloads import build_render_payload, total_render_duration
from app.services.render_review import refine_from_preview
from app.services.storage import download_asset_to_file, upload_rendered_video_file
from app.services.timing import timed_stage
from app.services.usage_service import projected_rendered_seconds, total_rendered_seconds


def render_project_videos(
    user_id: str,
    project: ProjectRecord,
    heartbeat: Callable[[], None] | None = None,
) -> tuple[RenderedVideoRecord, RenderedVideoRecord, EditPlanRecord, QualityReportRecord]:
    asset_path = require_asset_path(project)
    settings = get_settings()
    ensure_render_worker_ready()
    with TemporaryDirectory(prefix="launchify-render-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_video = download_asset_to_file(asset_path)
        voiceover_audio = download_voiceover_audio(project)
        try:
            preview_output = temp_dir / "preview.mp4"
            beat(heartbeat)
            with timed_stage("preview_render_initial", settings.preview_render_warn_seconds):
                render_preview_output(project, source_video, voiceover_audio, temp_dir, preview_output)
            beat(heartbeat)
            with timed_stage("preview_review", settings.planning_warn_seconds):
                reviewed_project, quality_report, rerender_preview = reviewed_project(project, preview_output)
            beat(heartbeat)
            enforce_final_render_limit(user_id, reviewed_project)
            if rerender_preview:
                with timed_stage("preview_render_refined", settings.preview_render_warn_seconds):
                    render_preview_output(reviewed_project, source_video, voiceover_audio, temp_dir, preview_output)
                beat(heartbeat)
            preview_video = upload_variant(user_id, reviewed_project, preview_output, "preview")
            beat(heartbeat)
            with timed_stage("final_render", settings.final_render_warn_seconds):
                final_video = render_and_upload_variant(user_id, reviewed_project, source_video, voiceover_audio, temp_dir, "final")
            beat(heartbeat)
            return preview_video, final_video, require_edit_plan(reviewed_project), quality_report
        finally:
            source_video.unlink(missing_ok=True)
            if voiceover_audio is not None:
                voiceover_audio.unlink(missing_ok=True)


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
) -> RenderedVideoRecord:
    output_path = temp_dir / f"{quality}.mp4"
    render_payload_path = write_render_payload(project, temp_dir, quality, voiceover_audio)
    invoke_render_worker(render_payload_path, source_video, output_path, quality)
    return upload_variant(user_id, project, output_path, quality)


def render_preview_output(
    project: ProjectRecord,
    source_video: Path,
    voiceover_audio: Path | None,
    temp_dir: Path,
    output_path: Path,
) -> None:
    render_payload_path = write_render_payload(project, temp_dir, "preview", voiceover_audio)
    invoke_render_worker(render_payload_path, source_video, output_path, "preview")


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
) -> None:
    settings = get_settings()
    worker_dir = Path(settings.render_worker_dir).resolve()
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
        subprocess.run(
            command,
            cwd=worker_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.render_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("Render worker dependencies are missing. Install the backend render worker.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Render worker timed out after {settings.render_timeout_seconds} seconds.") from exc
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
