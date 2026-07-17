from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable

from app.models.projects import ProjectRecord, RenderedVideoRecord

logger = logging.getLogger(__name__)

PreviewReady = Callable[[RenderedVideoRecord], None]
UploadPreview = Callable[[str, ProjectRecord, Path, Callable[[], None] | None], RenderedVideoRecord]
Heartbeat = Callable[[], None]


def prepare_proxy_preview(source_video: Path, output_path: Path) -> None:
    if source_video != output_path:
        shutil.copyfile(source_video, output_path)


def persist_proxy_preview(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    heartbeat: Heartbeat | None,
    preview_ready: PreviewReady | None,
    upload_preview: UploadPreview,
) -> RenderedVideoRecord:
    preview_video = upload_preview(user_id, project, preview_output, heartbeat)
    if preview_ready is not None:
        preview_ready(preview_video)
    if heartbeat is not None:
        heartbeat()
    return preview_video


def persist_proxy_preview_after_final(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    heartbeat: Heartbeat | None,
    preview_ready: PreviewReady | None,
    upload_preview: UploadPreview,
) -> RenderedVideoRecord | None:
    try:
        return persist_proxy_preview(user_id, project, preview_output, heartbeat, preview_ready, upload_preview)
    except Exception:
        logger.exception("Proxy preview upload failed after final render succeeded for project %s.", project.id)
        return None


def persist_proxy_preview_on_failure(
    user_id: str,
    project: ProjectRecord,
    preview_output: Path,
    heartbeat: Heartbeat | None,
    preview_ready: PreviewReady | None,
    upload_preview: UploadPreview,
) -> None:
    try:
        persist_proxy_preview(user_id, project, preview_output, heartbeat, preview_ready, upload_preview)
    except Exception:
        logger.exception("Proxy preview upload failed while preserving a render failure for project %s.", project.id)
