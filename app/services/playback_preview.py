from __future__ import annotations

from pathlib import Path

from app.models.projects import AssetRecord, ProjectRecord, RenderedVideoRecord
from app.services.render_runtime_helpers import output_duration_seconds


def publish_grounded_preview(project: ProjectRecord, source_video: Path) -> RenderedVideoRecord:
    asset = require_source_asset(project)
    return RenderedVideoRecord(
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        storage_path=asset.storage_path,
        duration_seconds=preview_duration_seconds(project, source_video),
        variant="preview",
    )


def require_source_asset(project: ProjectRecord) -> AssetRecord:
    if project.asset is None:
        raise RuntimeError("Source asset is required before publishing the grounded preview.")
    return project.asset


def preview_duration_seconds(project: ProjectRecord, source_video: Path) -> float:
    fallback = transcript_duration_seconds(project)
    return output_duration_seconds(source_video, fallback=fallback)


def transcript_duration_seconds(project: ProjectRecord) -> float:
    if project.transcript:
        return round(max(segment.end for segment in project.transcript), 2)
    return 0.0
