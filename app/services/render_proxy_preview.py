from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from app.core.config import get_settings
from app.models.projects import ProjectRecord, RenderedVideoRecord, VoiceoverMode
from app.services.render_proxy_clips import highlight_clips
from app.services.render_runtime_helpers import run_process_with_heartbeat

logger = logging.getLogger(__name__)

PreviewReady = Callable[[RenderedVideoRecord], None]
UploadPreview = Callable[[str, ProjectRecord, Path, Callable[[], None] | None], RenderedVideoRecord]
Heartbeat = Callable[[], None]


def prepare_proxy_preview(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    voiceover_audio: Path | None = None,
    heartbeat: Heartbeat | None = None,
) -> None:
    clips = highlight_clips(project)
    if not clips:
        if source_video != output_path:
            shutil.copyfile(source_video, output_path)
        return
    render_highlight_reel(project, source_video, output_path, clips, voiceover_audio, heartbeat)


def render_highlight_reel(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    clips: list[tuple[float, float]],
    voiceover_audio: Path | None,
    heartbeat: Heartbeat | None,
) -> None:
    settings = get_settings()
    has_audio = source_has_audio(source_video)
    command = build_highlight_command(project, source_video, output_path, clips, has_audio, voiceover_audio)
    try:
        run_process_with_heartbeat(command, timeout_seconds=settings.render_timeout_seconds, heartbeat=heartbeat)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for proxy highlight rendering. Configure FFMPEG_BINARY in the backend env.") from exc
    except (subprocess.CalledProcessError, TimeoutError) as exc:
        raise RuntimeError("Proxy highlight rendering failed before the final export step.") from exc


def source_has_audio(source_video: Path) -> bool:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        str(source_video),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return bool(result.stdout.strip())


def build_highlight_command(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    clips: list[tuple[float, float]],
    has_audio: bool,
    voiceover_audio: Path | None,
) -> list[str]:
    settings = get_settings()
    voiceover_mode = resolved_voiceover_mode(project, voiceover_audio)
    filter_complex = build_highlight_filter(clips, has_audio, voiceover_mode)
    command = [
        settings.ffmpeg_binary,
        "-y",
        "-i",
        str(source_video),
    ]
    if voiceover_mode != "original" and voiceover_audio is not None:
        command.extend(["-i", str(voiceover_audio)])
    command.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-threads",
        "1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ])
    if voiceover_mode == "voiceover":
        command.extend(["-map", "[aout]"])
    elif voiceover_mode == "mixed":
        command.extend(["-map", "[aout]"])
    elif has_audio:
        command.extend(["-map", "[aout]"])
    else:
        command.append("-an")
    if voiceover_mode != "original" or has_audio:
        command.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
    command.append(str(output_path))
    return command


def build_highlight_filter(clips: list[tuple[float, float]], has_audio: bool, voiceover_mode: VoiceoverMode) -> str:
    settings = get_settings()
    segment_filters, concat_inputs = segment_filters_for_clips(clips, has_audio)
    concat_labels = "[joinedv][aorig]" if has_audio else "[joinedv]"
    concat_filter = f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a={1 if has_audio else 0}{concat_labels}"
    scaled_output = (
        f"[joinedv]fps={settings.preview_proxy_fps},"
        f"scale={settings.preview_proxy_width}:{settings.preview_proxy_height}:force_original_aspect_ratio=decrease,"
        f"pad={settings.preview_proxy_width}:{settings.preview_proxy_height}:(ow-iw)/2:(oh-ih)/2:black[vout]"
    )
    audio_output = audio_mix_filter(clips, has_audio, voiceover_mode)
    return ";".join(segment_filters + [concat_filter, scaled_output, audio_output] if audio_output else segment_filters + [concat_filter, scaled_output])


def segment_filters_for_clips(
    clips: list[tuple[float, float]],
    has_audio: bool,
) -> tuple[list[str], list[str]]:
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, (start, end) in enumerate(clips):
        filters.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{index}]")
        concat_inputs.append(f"[v{index}]")
        if has_audio:
            filters.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]")
            concat_inputs.append(f"[a{index}]")
    return filters, concat_inputs


def resolved_voiceover_mode(project: ProjectRecord, voiceover_audio: Path | None) -> VoiceoverMode:
    if voiceover_audio is None or project.voiceover is None or project.voiceover.status != "ready":
        return "original"
    return project.voiceover.mode


def audio_mix_filter(clips: list[tuple[float, float]], has_audio: bool, voiceover_mode: VoiceoverMode) -> str:
    total_duration = round(sum(end - start for start, end in clips), 2)
    if voiceover_mode == "voiceover":
        return f"[1:a]atrim=start=0:end={total_duration},asetpts=PTS-STARTPTS[aout]"
    if voiceover_mode == "mixed" and has_audio:
        return (
            f"[1:a]atrim=start=0:end={total_duration},asetpts=PTS-STARTPTS[avo];"
            "[aorig][avo]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    if voiceover_mode == "mixed":
        return f"[1:a]atrim=start=0:end={total_duration},asetpts=PTS-STARTPTS[aout]"
    if has_audio:
        return "[aorig]anull[aout]"
    return ""


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
