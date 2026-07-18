from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Literal

from app.core.config import get_settings
from app.models.projects import EditPlanScene, FocusBox, ProjectRecord, RenderedVideoRecord, VoiceoverMode
from app.services.render_proxy_clips import highlight_clips
from app.services.render_runtime_helpers import output_duration_seconds, run_process_with_heartbeat

logger = logging.getLogger(__name__)

PreviewReady = Callable[[RenderedVideoRecord], None]
UploadPreview = Callable[[str, ProjectRecord, Path, Callable[[], None] | None], RenderedVideoRecord]
Heartbeat = Callable[[], None]
ExportQuality = Literal["preview", "final"]


def prepare_proxy_preview(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    voiceover_audio: Path | None = None,
    heartbeat: Heartbeat | None = None,
    quality: ExportQuality = "preview",
) -> None:
    clips = highlight_clips(project)
    if not clips:
        render_passthrough_video(project, source_video, output_path, voiceover_audio, heartbeat, quality)
        return
    render_highlight_reel(project, source_video, output_path, clips, voiceover_audio, heartbeat, quality)


def render_highlight_reel(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    clips: list[tuple[float, float]],
    voiceover_audio: Path | None,
    heartbeat: Heartbeat | None,
    quality: ExportQuality,
) -> None:
    settings = get_settings()
    has_audio = source_has_audio(source_video)
    command = build_highlight_command(project, source_video, output_path, clips, has_audio, voiceover_audio, quality)
    try:
        run_process_with_heartbeat(command, timeout_seconds=settings.render_timeout_seconds, heartbeat=heartbeat)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for proxy highlight rendering. Configure FFMPEG_BINARY in the backend env.") from exc
    except (subprocess.CalledProcessError, TimeoutError) as exc:
        raise RuntimeError("Proxy highlight rendering failed before the final export step.") from exc


def render_passthrough_video(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    voiceover_audio: Path | None,
    heartbeat: Heartbeat | None,
    quality: ExportQuality,
) -> None:
    settings = get_settings()
    has_audio = source_has_audio(source_video)
    command = build_passthrough_command(project, source_video, output_path, has_audio, voiceover_audio, quality)
    try:
        run_process_with_heartbeat(command, timeout_seconds=settings.render_timeout_seconds, heartbeat=heartbeat)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for final video export. Configure FFMPEG_BINARY in the backend env.") from exc
    except (subprocess.CalledProcessError, TimeoutError) as exc:
        raise RuntimeError("Final video export failed while processing the raw recording.") from exc


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
    quality: ExportQuality,
) -> list[str]:
    settings = get_settings()
    voiceover_mode = resolved_voiceover_mode(project, voiceover_audio)
    filter_complex = build_highlight_filter(project, clips, has_audio, voiceover_mode, quality)
    command = [settings.ffmpeg_binary, "-y", "-i", str(source_video)]
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
        "20" if quality == "final" else "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ])
    if voiceover_mode != "original" or has_audio:
        command.extend(["-map", "[aout]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
    else:
        command.append("-an")
    command.append(str(output_path))
    return command


def build_passthrough_command(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    has_audio: bool,
    voiceover_audio: Path | None,
    quality: ExportQuality,
) -> list[str]:
    settings = get_settings()
    voiceover_mode = resolved_voiceover_mode(project, voiceover_audio)
    duration_seconds = source_duration_seconds(project, source_video)
    command = [settings.ffmpeg_binary, "-y", "-i", str(source_video)]
    if voiceover_mode != "original" and voiceover_audio is not None:
        command.extend(["-i", str(voiceover_audio)])
    command.extend([
        "-vf",
        passthrough_scale_filter(quality),
        "-r",
        str(target_fps(quality)),
        "-threads",
        "1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20" if quality == "final" else "24",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ])
    if voiceover_mode != "original" or has_audio:
        command.extend([
            "-filter_complex",
            passthrough_audio_filter(has_audio, voiceover_mode, duration_seconds),
            "-map",
            "0:v:0",
            "-map",
            "[aout]",
        ])
        command.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
    else:
        command.append("-an")
    command.append(str(output_path))
    return command


def build_highlight_filter(
    project: ProjectRecord,
    clips: list[tuple[float, float]],
    has_audio: bool,
    voiceover_mode: VoiceoverMode,
    quality: ExportQuality,
) -> str:
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, clip in enumerate(clips):
        scene = project.edit_plan.scenes[index] if project.edit_plan is not None and index < len(project.edit_plan.scenes) else None
        filters.extend(segment_filters(index, clip, scene, has_audio, quality))
        concat_inputs.append(f"[v{index}]")
        if has_audio:
            concat_inputs.append(f"[a{index}]")
    concat_labels = "[joinedv][aorig]" if has_audio else "[joinedv]"
    filters.append(f"{''.join(concat_inputs)}concat=n={len(clips)}:v=1:a={1 if has_audio else 0}{concat_labels}")
    filters.append(output_scale_filter(quality))
    audio_output = audio_mix_filter(clips, has_audio, voiceover_mode)
    if audio_output:
        filters.append(audio_output)
    return ";".join(filters)


def segment_filters(
    index: int,
    clip: tuple[float, float],
    scene: EditPlanScene | None,
    has_audio: bool,
    quality: ExportQuality,
) -> list[str]:
    start, end = clip
    chain = [f"[0:v]trim=start={start}:end={end}", "setpts=PTS-STARTPTS"]
    crop_filter, crop_box, crop_bounds = scene_crop_filter(scene, quality)
    if crop_filter:
        chain.append(crop_filter)
    chain.extend(scene_overlay_filters(scene, start, end, crop_box, crop_bounds, quality))
    chain.append(f"fps={target_fps(quality)}")
    chain.append(f"[v{index}]")
    filters = [".".join([])]  # placeholder removed below
    video_filter = ",".join(chain[:-1]) + chain[-1]
    filters = [video_filter]
    if has_audio:
        filters.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{index}]")
    return filters


def scene_crop_filter(
    scene: EditPlanScene | None,
    quality: ExportQuality,
) -> tuple[str | None, FocusBox | None, tuple[float, float, float, float] | None]:
    if scene is None:
        return None, None, None
    focus_box = representative_focus_box(scene)
    zoom_scale = representative_zoom_scale(scene)
    if focus_box is None or zoom_scale <= 1.02:
        return None, focus_box, None
    crop_width = max(min(round(1 / min(zoom_scale, 1.28), 4), 0.98), 0.7)
    crop_height = crop_width
    origin_x = clamp((focus_box.x + focus_box.width / 2) - crop_width / 2, 0.0, 1.0 - crop_width)
    origin_y = clamp((focus_box.y + focus_box.height / 2) - crop_height / 2, 0.0, 1.0 - crop_height)
    filter_text = (
        f"crop=w=iw*{crop_width}:h=ih*{crop_height}:x=iw*{origin_x}:y=ih*{origin_y}"
    )
    return (
        filter_text,
        rebased_box(focus_box, origin_x, origin_y, crop_width, crop_height),
        (origin_x, origin_y, crop_width, crop_height),
    )


def representative_focus_box(scene: EditPlanScene) -> FocusBox | None:
    for highlight in scene.highlights:
        if highlight.focus_box is not None:
            return highlight.focus_box
    for zoom in scene.zooms:
        if zoom.focus_box is not None:
            return zoom.focus_box
    return None


def representative_zoom_scale(scene: EditPlanScene) -> float:
    if not scene.zooms:
        return 1.0
    return max(zoom.scale for zoom in scene.zooms)


def rebased_box(box: FocusBox, origin_x: float, origin_y: float, crop_width: float, crop_height: float) -> FocusBox:
    return FocusBox(
        x=clamp((box.x - origin_x) / crop_width, 0.0, 1.0),
        y=clamp((box.y - origin_y) / crop_height, 0.0, 1.0),
        width=clamp(box.width / crop_width, 0.04, 1.0),
        height=clamp(box.height / crop_height, 0.04, 1.0),
    )


def rebased_highlight_box(
    box: FocusBox | None,
    crop_bounds: tuple[float, float, float, float] | None,
) -> FocusBox | None:
    if box is None or crop_bounds is None:
        return box
    origin_x, origin_y, crop_width, crop_height = crop_bounds
    return rebased_box(box, origin_x, origin_y, crop_width, crop_height)


def scene_overlay_filters(
    scene: EditPlanScene | None,
    clip_start: float,
    clip_end: float,
    focus_box: FocusBox | None,
    crop_bounds: tuple[float, float, float, float] | None,
    quality: ExportQuality,
) -> list[str]:
    if scene is None:
        return []
    filters: list[str] = []
    filters.extend(highlight_draw_filters(scene, clip_start, clip_end, focus_box, crop_bounds))
    filters.extend(caption_draw_filters(scene, clip_start, clip_end, quality))
    return filters


def highlight_draw_filters(
    scene: EditPlanScene,
    clip_start: float,
    clip_end: float,
    focus_box: FocusBox | None,
    crop_bounds: tuple[float, float, float, float] | None,
) -> list[str]:
    filters: list[str] = []
    for highlight in scene.highlights:
        start = max(highlight.start, clip_start) - clip_start
        end = min(highlight.end, clip_end) - clip_start
        if end - start <= 0.05:
            continue
        box = rebased_highlight_box(highlight.focus_box, crop_bounds) or focus_box
        if box is None:
            continue
        filters.append(
            "drawbox="
            f"x=w*{box.x}:y=h*{box.y}:w=w*{box.width}:h=h*{box.height}:"
            "color=0xF59E0B@0.95:t=4:"
            f"enable='between(t,{round(start, 2)},{round(end, 2)})'"
        )
    return filters


def caption_draw_filters(
    scene: EditPlanScene,
    clip_start: float,
    clip_end: float,
    quality: ExportQuality,
) -> list[str]:
    filters: list[str] = []
    for caption in scene.captions:
        start = max(caption.start, clip_start) - clip_start
        end = min(caption.end, clip_end) - clip_start
        if end - start <= 0.05:
            continue
        font_size = 34 if quality == "final" else 24
        escaped = escape_drawtext(caption.text.replace("\n", " "))
        filters.append(
            "drawtext="
            f"text='{escaped}':"
            f"fontsize={font_size}:fontcolor=white:"
            "box=1:boxcolor=black@0.42:boxborderw=18:"
            "x=(w-text_w)/2:y=h-(h*0.15):"
            f"enable='between(t,{round(start, 2)},{round(end, 2)})'"
        )
    return filters


def output_scale_filter(quality: ExportQuality) -> str:
    settings = get_settings()
    width = settings.final_render_width if quality == "final" else settings.preview_proxy_width
    height = settings.final_render_height if quality == "final" else settings.preview_proxy_height
    return (
        f"[joinedv]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black[vout]"
    )


def passthrough_scale_filter(quality: ExportQuality) -> str:
    settings = get_settings()
    width = settings.final_render_width if quality == "final" else settings.preview_proxy_width
    height = settings.final_render_height if quality == "final" else settings.preview_proxy_height
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"


def target_fps(quality: ExportQuality) -> int:
    settings = get_settings()
    return settings.final_render_fps if quality == "final" else settings.preview_proxy_fps


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


def passthrough_audio_filter(has_audio: bool, voiceover_mode: VoiceoverMode, duration_seconds: float) -> str:
    safe_duration = max(round(duration_seconds, 2), 0.1)
    if voiceover_mode == "voiceover":
        return f"[1:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aout]"
    if voiceover_mode == "mixed" and has_audio:
        return (
            f"[0:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aorig];"
            f"[1:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[avo];"
            "[aorig][avo]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    if voiceover_mode == "mixed":
        return f"[1:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aout]"
    if has_audio:
        return f"[0:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aout]"
    return "anullsrc=channel_layout=stereo:sample_rate=48000[aout]"


def source_duration_seconds(project: ProjectRecord, source_video: Path) -> float:
    fallback = 0.0
    if project.edit_plan is not None:
        fallback = max(project.edit_plan.total_duration_seconds, 0.0)
    if fallback <= 0 and project.transcript:
        fallback = round(max(segment.end for segment in project.transcript), 2)
    return output_duration_seconds(source_video, fallback=fallback)


def escape_drawtext(text: str) -> str:
    escaped = text.replace("\\", "\\\\\\\\")
    for value in (":", "'", "%", "[", "]", ","):
        escaped = escaped.replace(value, f"\\\\{value}")
    return escaped


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


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
