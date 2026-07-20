from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Literal

from app.core.config import get_settings
from app.models.projects import EditPlanScene, FocusBox, ProjectRecord, RenderedVideoRecord, VoiceoverMode
from app.services.preview_manifest import PreviewManifest, PreviewManifestClip, build_preview_manifest
from app.services.preview_render_guard import render_profiles, validate_rendered_preview
from app.services.render_focus_effects import rebased_highlight_box, scene_crop_plan, spotlight_filters
from app.services.render_runtime_helpers import output_duration_seconds, require_duration, run_process_with_heartbeat

logger = logging.getLogger(__name__)
PreviewReady = Callable[[RenderedVideoRecord], None]
UploadPreview = Callable[[str, ProjectRecord, Path, Callable[[], None] | None], RenderedVideoRecord]
Heartbeat = Callable[[], None]
ExportQuality = Literal["preview", "final"]
SEEK_SLACK_SECONDS = 1.0
def prepare_proxy_preview(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    voiceover_audio: Path | None = None,
    heartbeat: Heartbeat | None = None,
    quality: ExportQuality = "preview",
) -> None:
    voiceover_mode = resolved_voiceover_mode(project, voiceover_audio)
    manifest = build_preview_manifest(project, voiceover_mode, quality)
    log_preview_manifest(project, manifest, voiceover_mode, voiceover_audio)
    if not manifest.clips:
        render_passthrough_video(project, source_video, output_path, voiceover_audio, heartbeat, quality)
        return
    last_error: RuntimeError | None = None
    for profile_name, candidate in render_profiles(manifest, quality):
        try:
            render_highlight_reel(project, source_video, output_path, candidate.clips, voiceover_audio, heartbeat, quality)
            validation_error = validate_rendered_preview(candidate, output_path, voiceover_mode, voiceover_audio)
            if validation_error is None:
                return
            logger.warning("Preview validation failed for project %s: profile=%s, reason=%s.", project.id, profile_name, validation_error)
        except RuntimeError as exc:
            last_error = exc
            logger.warning("Preview profile failed for project %s: profile=%s, error=%s.", project.id, profile_name, exc)
    raise last_error or RuntimeError("Preview rendering failed validation for every fallback profile.")

def render_highlight_reel(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    clips: list[PreviewManifestClip],
    voiceover_audio: Path | None,
    heartbeat: Heartbeat | None,
    quality: ExportQuality,
) -> None:
    has_audio = source_has_audio(source_video)
    try:
        render_sequential_reel(project, source_video, output_path, clips, has_audio, voiceover_audio, heartbeat, quality)
        logger.info("Preview render succeeded for project %s: rendered_clips=%s, voiceover_mode=%s.", project.id, len(clips), resolved_voiceover_mode(project, voiceover_audio))
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for proxy highlight rendering. Configure FFMPEG_BINARY in the backend env.") from exc
    except (subprocess.CalledProcessError, TimeoutError) as exc:
        logger.warning("Preview render failed for project %s: rendered_clips=%s, voiceover_mode=%s, error=%s.", project.id, len(clips), resolved_voiceover_mode(project, voiceover_audio), exc)
        raise RuntimeError("Proxy highlight rendering failed before the final export step.") from exc

def log_preview_manifest(
    project: ProjectRecord,
    manifest: PreviewManifest,
    voiceover_mode: VoiceoverMode,
    voiceover_audio: Path | None,
) -> None:
    logger.info(
        "Preview render plan for project %s: clip_count=%s, clip_duration_seconds=%.2f, stage_counts=%s, voiceover_mode=%s, voiceover_audio=%s.",
        project.id,
        len(manifest.clips),
        manifest.total_duration_seconds,
        manifest.stage_counts,
        voiceover_mode,
        voiceover_audio is not None,
    )
    for payload in manifest.diagnostic_payloads(project.voiceover is not None and project.voiceover.status == "ready"):
        logger.info("Preview render scene for project %s: %s", project.id, payload)
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
def render_sequential_reel(
    project: ProjectRecord,
    source_video: Path,
    output_path: Path,
    clips: list[PreviewManifestClip],
    has_audio: bool,
    voiceover_audio: Path | None,
    heartbeat: Heartbeat | None,
    quality: ExportQuality,
) -> None:
    deadline = time.monotonic() + get_settings().render_timeout_seconds
    voiceover_mode = resolved_voiceover_mode(project, voiceover_audio)
    render_audio = has_audio and voiceover_mode != "voiceover"
    clip_paths = render_clip_segments(source_video, clips, render_audio, quality, output_path.parent, heartbeat, deadline)
    joined_output = output_path.parent / "preview-joined.mp4"
    concat_clip_segments(clip_paths, joined_output, heartbeat, deadline)
    finalize_preview_audio(project, joined_output, output_path, has_audio, voiceover_audio, voiceover_mode, heartbeat, deadline)
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
    duration_seconds = preview_audio_duration(project, source_duration_seconds(project, source_video), voiceover_mode)
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
def render_clip_segments(
    source_video: Path,
    clips: list[PreviewManifestClip],
    render_audio: bool,
    quality: ExportQuality,
    working_dir: Path,
    heartbeat: Heartbeat | None,
    deadline: float,
) -> list[Path]:
    clip_paths: list[Path] = []
    for index, clip in enumerate(clips):
        clip_path = working_dir / f"clip-{index:02d}.mp4"
        command = build_clip_command(source_video, clip, clip_path, render_audio, quality, working_dir)
        run_process_with_heartbeat(command, timeout_seconds=remaining_timeout_seconds(deadline), heartbeat=heartbeat)
        clip_paths.append(clip_path)
    return clip_paths
def build_clip_command(
    source_video: Path,
    clip: PreviewManifestClip,
    clip_path: Path,
    render_audio: bool,
    quality: ExportQuality,
    working_dir: Path,
) -> list[str]:
    settings = get_settings()
    source_duration = source_clip_duration(clip)
    clip_duration = max(round(clip.trim_end - clip.trim_start, 2), source_duration)
    coarse_start = max(round(clip.source_start - SEEK_SLACK_SECONDS, 2), 0.0)
    precise_start = round(max(clip.source_start - coarse_start, 0.0), 2)
    filters = segment_filters(0, clip, render_audio, quality, working_dir, precise_start, source_duration, clip_duration)
    command = [
        settings.ffmpeg_binary,
        "-y",
        "-ss",
        str(coarse_start),
        "-t",
        str(round(precise_start + source_duration, 2)),
        "-i",
        str(source_video),
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v0]",
    ]
    command.extend(["-threads", "1", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20" if quality == "final" else "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    if render_audio:
        command.extend(["-map", "[a0]", "-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
    else:
        command.append("-an")
    command.append(str(clip_path))
    return command
def concat_clip_segments(
    clip_paths: list[Path],
    output_path: Path,
    heartbeat: Heartbeat | None,
    deadline: float,
) -> None:
    settings = get_settings()
    concat_file = output_path.parent / "clips.txt"
    concat_file.write_text("".join(f"file '{path.name}'\n" for path in clip_paths), encoding="utf-8")
    command = [
        settings.ffmpeg_binary,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    run_process_with_heartbeat(command, timeout_seconds=remaining_timeout_seconds(deadline), heartbeat=heartbeat, cwd=output_path.parent)
def finalize_preview_audio(
    project: ProjectRecord,
    joined_output: Path,
    output_path: Path,
    has_audio: bool,
    voiceover_audio: Path | None,
    voiceover_mode: VoiceoverMode,
    heartbeat: Heartbeat | None,
    deadline: float,
) -> None:
    settings = get_settings()
    if voiceover_mode == "original" or voiceover_audio is None:
        joined_output.replace(output_path)
        return
    duration_seconds = preview_audio_duration(project, output_duration_seconds(joined_output, fallback=require_duration(project)), voiceover_mode)
    command = [settings.ffmpeg_binary, "-y", "-i", str(joined_output), "-i", str(voiceover_audio), "-filter_complex", passthrough_audio_filter(has_audio, voiceover_mode, duration_seconds), "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart", str(output_path)]
    run_process_with_heartbeat(command, timeout_seconds=remaining_timeout_seconds(deadline), heartbeat=heartbeat)
def segment_filters(
    index: int,
    clip: PreviewManifestClip,
    concat_audio: bool,
    quality: ExportQuality,
    working_dir: Path,
    precise_start: float,
    source_duration: float,
    clip_duration: float,
) -> list[str]:
    scene = clip.scene
    chain = [f"[0:v]trim=start={precise_start}:end={round(precise_start + source_duration, 2)}", "setpts=PTS-STARTPTS"]
    if clip.freeze_frame:
        chain.extend(["select='eq(n,0)'", freeze_frame_filter(source_duration, clip_duration)])
    crop_filter, crop_box, crop_bounds = scene_crop_filter(scene, clip.source_start, clip.source_end, quality, clip.stage)
    animated_crop = animated_crop_filter_text(crop_filter)
    if animated_crop:
        chain.extend(highlight_draw_filters(scene, clip.source_start, clip.source_end, crop_box, None))
    if crop_filter:
        chain.append(crop_filter)
    if not animated_crop:
        chain.extend(highlight_draw_filters(scene, clip.source_start, clip.source_end, crop_box, crop_bounds))
    chain.extend(caption_draw_filters(scene, clip.source_start, clip.source_end, quality, working_dir))
    chain.extend(video_finish_filters(clip))
    chain.append(f"fps={target_fps(quality)}")
    chain.append(passthrough_scale_filter(quality))
    chain.append(f"[v{index}]")
    filters = [",".join(chain[:-1]) + chain[-1]]
    if concat_audio:
        filters.append(f"[0:a]atrim=start={precise_start}:end={round(precise_start + source_duration, 2)},asetpts=PTS-STARTPTS[a{index}]")
    return filters

def remaining_timeout_seconds(deadline: float) -> int:
    return max(int(deadline - time.monotonic()), 1)

def scene_crop_filter(
    scene: EditPlanScene | None,
    clip_start: float,
    clip_end: float,
    quality: ExportQuality,
    stage: str,
) -> tuple[str | None, FocusBox | None, tuple[float, float, float, float] | None]:
    return scene_crop_plan(
        scene,
        clip_start,
        clip_end,
        stage,
        target_width(quality),
        target_height(quality),
        target_fps(quality),
    )


def animated_crop_filter_text(filter_text: str | None) -> bool:
    return bool(filter_text and filter_text.startswith("zoompan="))


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
        filters.extend(spotlight_filters(box, start, end, highlight.style))
    return filters


def caption_draw_filters(
    scene: EditPlanScene,
    clip_start: float,
    clip_end: float,
    quality: ExportQuality,
    working_dir: Path,
) -> list[str]:
    filters: list[str] = []
    for index, caption in enumerate(scene.captions, start=1):
        start = max(caption.start, clip_start) - clip_start
        end = min(caption.end, clip_end) - clip_start
        if end - start <= 0.05:
            continue
        font_size = 34 if quality == "final" else 24
        caption_file = write_caption_text_file(working_dir, scene.scene_number, index, caption.text)
        filters.append(
            "drawtext="
            f"textfile='{escape_drawtext_path(caption_file)}':"
            "expansion=none:"
            f"fontsize={font_size}:fontcolor=white:"
            "line_spacing=8:box=1:boxcolor=black@0.34:boxborderw=20:borderw=1:bordercolor=white@0.08:"
            "x=(w-text_w)/2:y=h-(h*0.15):"
            f"enable='between(t,{round(start, 2)},{round(end, 2)})'"
        )
    return filters


def video_finish_filters(clip: PreviewManifestClip) -> list[str]:
    duration = round(max(clip.trim_end - clip.trim_start, 0.1), 2)
    fade_in = min(0.12, max(duration * 0.08, 0.04))
    fade_out = min(0.16, max(duration * 0.1, 0.05))
    filters = [f"fade=t=in:st=0:d={fade_in}"]
    if clip.stage != "focus" and duration > fade_out + 0.08:
        filters.append(f"fade=t=out:st={round(duration - fade_out, 2)}:d={fade_out}")
    return filters


def source_clip_duration(clip: PreviewManifestClip) -> float:
    return max(round(clip.source_end - clip.source_start, 2), 0.1)


def freeze_frame_filter(source_duration: float, clip_duration: float) -> str:
    hold_duration = round(max(clip_duration - source_duration, 0.0), 2)
    return f"tpad=stop_mode=clone:stop_duration={hold_duration}" if hold_duration > 0 else "null"


def passthrough_scale_filter(quality: ExportQuality) -> str:
    width = target_width(quality)
    height = target_height(quality)
    return f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
def target_width(quality: ExportQuality) -> int:
    settings = get_settings()
    return settings.final_render_width if quality == "final" else settings.preview_proxy_width
def target_height(quality: ExportQuality) -> int:
    settings = get_settings()
    return settings.final_render_height if quality == "final" else settings.preview_proxy_height
def target_fps(quality: ExportQuality) -> int:
    settings = get_settings()
    return settings.final_render_fps if quality == "final" else settings.preview_proxy_fps
def resolved_voiceover_mode(project: ProjectRecord, voiceover_audio: Path | None) -> VoiceoverMode:
    if voiceover_audio is None or project.voiceover is None or project.voiceover.status != "ready":
        return "original"
    return project.voiceover.mode
def passthrough_audio_filter(has_audio: bool, voiceover_mode: VoiceoverMode, duration_seconds: float) -> str:
    safe_duration = max(round(duration_seconds, 2), 0.1)
    if voiceover_mode == "voiceover":
        return f"[1:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aout]"
    if voiceover_mode == "mixed" and has_audio:
        return (
            f"[0:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[aorig];"
            f"[1:a]atrim=start=0:end={safe_duration},asetpts=PTS-STARTPTS[avo];"
            "[aorig][avo]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
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
def preview_audio_duration(project: ProjectRecord, visual_duration: float, voiceover_mode: VoiceoverMode) -> float:
    return round(max(visual_duration, scheduled_voiceover_duration(project, voiceover_mode), 0.1), 2)
def scheduled_voiceover_duration(project: ProjectRecord, voiceover_mode: VoiceoverMode) -> float:
    voiceover = project.voiceover
    if voiceover_mode == "original" or voiceover is None or voiceover.status != "ready":
        return 0.0
    clip_end = max((clip.end for clip in voiceover.clips if clip.audio_storage_path), default=0.0)
    cue_end = max((cue.end for cue in voiceover.cues), default=0.0)
    return round(max(voiceover.duration_seconds, clip_end, cue_end), 2)
def write_caption_text_file(working_dir: Path, scene_number: int, caption_index: int, text: str) -> Path:
    caption_file = working_dir / f"scene-{scene_number}-caption-{caption_index}.txt"
    caption_file.write_text(normalized_caption_text(text), encoding="utf-8")
    return caption_file
def normalized_caption_text(text: str) -> str:
    preserved = "\n".join(line for line in (re.sub(r"\s+", " ", part).strip() for part in text.replace("\r", "").split("\n")) if line)
    return preserved or " "
def escape_drawtext_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace("'", r"\'")
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
