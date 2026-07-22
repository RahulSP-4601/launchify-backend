from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from app.models.projects import ProjectRecord, RenderedVideoRecord
from app.services.preview_execution import build_preview_execution_project, downloadable_execution_voiceover_audio
from app.services.preview_render_quality import build_render_diagnostics, should_reject_preview_publish
from app.services.render_proxy_preview import prepare_proxy_preview
from app.services.render_runtime_helpers import (
    output_duration_seconds,
    prepare_preview_render_source,
    prepare_final_render_source,
    upload_variant,
)

Heartbeat = Callable[[], None]
logger = logging.getLogger(__name__)


def publish_grounded_preview(
    user_id: str,
    project: ProjectRecord,
    source_video: Path,
    heartbeat: Heartbeat | None = None,
    variant: Literal["preview", "final"] = "preview",
) -> RenderedVideoRecord:
    with TemporaryDirectory(prefix="launchify-playback-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        output_path = temp_dir / f"{variant}.mp4"
        prepared_source, quality = preview_render_inputs(source_video, temp_dir, heartbeat, variant)
        execution_project = build_preview_execution_project(project, quality)
        voiceover_audio = downloadable_execution_voiceover_audio(execution_project)
        logger.info(
            "Preview publish started for project %s: variant=%s, quality=%s, source_video=%s, prepared_source=%s, voiceover_audio=%s.",
            execution_project.id,
            variant,
            quality,
            source_video.name,
            prepared_source.name,
            voiceover_audio is not None,
        )
        try:
            render_report = prepare_proxy_preview(execution_project, prepared_source, output_path, voiceover_audio, heartbeat, quality=quality)
        finally:
            if voiceover_audio is not None:
                voiceover_audio.unlink(missing_ok=True)
        diagnostics = build_render_diagnostics(render_report, output_path)
        if should_reject_preview_publish(diagnostics):
            issues = ", ".join(diagnostics.issues) or "preview_validation_failed"
            raise RuntimeError(f"Rendered preview failed publish validation: {issues}")
        if diagnostics.issues:
            logger.warning(
                "Preview publish downgraded for project %s: issues=%s, selected_profile=%s, rendered_preview_score=%.2f.",
                execution_project.id,
                diagnostics.issues,
                diagnostics.selected_profile,
                diagnostics.rendered_preview_score,
            )
        video = upload_variant(user_id, execution_project, output_path, variant, heartbeat=heartbeat).model_copy(update={"diagnostics": diagnostics})
        logger.info(
            "Preview publish finished for project %s: variant=%s, output_duration_seconds=%.2f, stored_path=%s.",
            execution_project.id,
            variant,
            preview_duration_seconds(execution_project, output_path),
            video.storage_path,
        )
        return video.model_copy(update={"duration_seconds": preview_duration_seconds(execution_project, output_path)})


def preview_duration_seconds(project: ProjectRecord, source_video: Path) -> float:
    fallback = transcript_duration_seconds(project)
    return output_duration_seconds(source_video, fallback=fallback)


def preview_render_inputs(
    source_video: Path,
    temp_dir: Path,
    heartbeat: Heartbeat | None,
    variant: Literal["preview", "final"],
) -> tuple[Path, Literal["preview", "final"]]:
    if variant == "final":
        return prepare_final_render_source(source_video, temp_dir, heartbeat), "final"
    return prepare_preview_render_source(source_video, temp_dir, heartbeat), "preview"


def transcript_duration_seconds(project: ProjectRecord) -> float:
    if project.transcript:
        return round(max(segment.end for segment in project.transcript), 2)
    return 0.0
