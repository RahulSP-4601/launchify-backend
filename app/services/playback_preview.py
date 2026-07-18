from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Literal

from app.models.projects import ProjectRecord, RenderedVideoRecord
from app.services.render_proxy_preview import prepare_proxy_preview
from app.services.render_runtime_helpers import download_voiceover_audio, output_duration_seconds, upload_variant

Heartbeat = Callable[[], None]


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
        voiceover_audio = download_voiceover_audio(project)
        try:
            prepare_proxy_preview(project, source_video, output_path, voiceover_audio, heartbeat, quality="final")
        finally:
            if voiceover_audio is not None:
                voiceover_audio.unlink(missing_ok=True)
        video = upload_variant(user_id, project, output_path, variant, heartbeat=heartbeat)
        return video.model_copy(update={"duration_seconds": preview_duration_seconds(project, output_path)})


def preview_duration_seconds(project: ProjectRecord, source_video: Path) -> float:
    fallback = transcript_duration_seconds(project)
    return output_duration_seconds(source_video, fallback=fallback)


def transcript_duration_seconds(project: ProjectRecord) -> float:
    if project.transcript:
        return round(max(segment.end for segment in project.transcript), 2)
    return 0.0
