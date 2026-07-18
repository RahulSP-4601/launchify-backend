from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedFrame:
    timestamp: float
    image_path: Path


def extract_scene_frames(
    video_path: Path,
    scene_numbers: list[int],
    scene_ranges: list[tuple[float, float]],
    output_dir: Path,
    *,
    frame_budget: int | None = None,
    frame_width: int | None = None,
    jpeg_quality: int = 2,
) -> dict[int, list[ExtractedFrame]]:
    return {
        scene_number: extract_frames_for_scene(
            video_path,
            scene_number,
            scene_range,
            output_dir,
            frame_budget=frame_budget,
            frame_width=frame_width,
            jpeg_quality=jpeg_quality,
        )
        for scene_number, scene_range in zip(scene_numbers, scene_ranges, strict=True)
    }


def extract_frames_for_scene(
    video_path: Path,
    scene_number: int,
    scene_range: tuple[float, float],
    output_dir: Path,
    *,
    frame_budget: int | None = None,
    frame_width: int | None = None,
    jpeg_quality: int = 2,
) -> list[ExtractedFrame]:
    extracted_frames: list[ExtractedFrame] = []
    safe_scene_range = normalize_scene_range(video_path, scene_range)
    for frame_index, frame_time in enumerate(sample_times(*safe_scene_range, frame_budget=frame_budget), start=1):
        try:
            image_path = extract_frame(
                video_path,
                output_dir,
                scene_number,
                frame_index,
                frame_time,
                frame_width=frame_width,
                jpeg_quality=jpeg_quality,
            )
        except RuntimeError as exc:
            logger.warning(
                "Skipping scene %s frame %s at %.2fs for %s: %s",
                scene_number,
                frame_index,
                frame_time,
                video_path.name,
                exc,
            )
            continue
        extracted_frames.append(ExtractedFrame(timestamp=frame_time, image_path=image_path))
    if not extracted_frames:
        raise RuntimeError(f"No visual-analysis frames could be extracted for scene {scene_number}.")
    extracted_frames.sort(key=lambda frame: frame.timestamp)
    return extracted_frames


def sample_times(start: float, end: float, *, frame_budget: int | None = None) -> list[float]:
    effective_frame_budget = max(frame_budget or 6, 2)
    duration = max(end - start, 0.3)
    earliest = start + min(0.2, duration * 0.08)
    latest = end - min(0.2, duration * 0.08)
    if effective_frame_budget == 2:
        return unique_times([earliest, latest])
    sampling_span = max(latest - earliest, 0.01)
    step = sampling_span / max(effective_frame_budget - 1, 1)
    return unique_times(
        [min(earliest + (step * index), latest) for index in range(effective_frame_budget - 1)] + [latest]
    )


def normalize_scene_range(video_path: Path, scene_range: tuple[float, float]) -> tuple[float, float]:
    start, end = scene_range
    video_duration = video_duration_seconds(video_path)
    if video_duration <= 0.4:
        return start, end
    safe_start = max(start, 0.0)
    safe_end = min(end, video_duration - 0.25)
    if safe_end <= safe_start:
        return safe_start, min(end, video_duration)
    return safe_start, safe_end


def extract_frame(
    video_path: Path,
    output_dir: Path,
    scene_number: int,
    frame_index: int,
    timestamp: float,
    *,
    frame_width: int | None = None,
    jpeg_quality: int = 2,
) -> Path:
    output_path = output_dir / f"scene-{scene_number}-frame-{frame_index}.jpg"
    extraction_error: RuntimeError | None = None
    for candidate_timestamp in candidate_timestamps(timestamp):
        output_path.unlink(missing_ok=True)
        try:
            run_ffmpeg_capture(
                video_path,
                output_path,
                candidate_timestamp,
                frame_width=frame_width,
                jpeg_quality=jpeg_quality,
            )
        except RuntimeError as exc:
            extraction_error = exc
            continue
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
    if extraction_error is not None:
        raise extraction_error
    raise RuntimeError("FFmpeg did not write a usable frame image.")


def run_ffmpeg_capture(
    video_path: Path,
    output_path: Path,
    timestamp: float,
    *,
    frame_width: int | None,
    jpeg_quality: int,
) -> None:
    command = build_ffmpeg_command(video_path, output_path, timestamp, frame_width=frame_width, jpeg_quality=jpeg_quality)
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=get_settings().ffmpeg_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for visual analysis. Configure FFMPEG_BINARY in the backend env.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"FFmpeg timed out while extracting video frames after {get_settings().ffmpeg_timeout_seconds} seconds."
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or "Unknown FFmpeg failure."
        raise RuntimeError(f"FFmpeg failed while extracting video frames: {detail}") from exc


def candidate_timestamps(timestamp: float) -> list[float]:
    offsets = (0.0, 0.08, 0.16, 0.28, 0.4, 0.6)
    candidates: list[float] = []
    for offset in offsets:
        candidate = round(max(timestamp - offset, 0.0), 2)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def build_ffmpeg_command(
    video_path: Path,
    output_path: Path,
    timestamp: float,
    *,
    frame_width: int | None = None,
    jpeg_quality: int = 2,
) -> list[str]:
    settings = get_settings()
    ffmpeg_binary = settings.ffmpeg_binary
    command = [
        ffmpeg_binary,
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        str(jpeg_quality),
    ]
    if frame_width is not None:
        command.extend(["-vf", f"scale='min(iw,{frame_width})':-2"])
    command.append(str(output_path))
    return command


def video_duration_seconds(video_path: Path) -> float:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
        return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError, ValueError):
        return 0.0


def unique_times(timestamps: list[float]) -> list[float]:
    unique: list[float] = []
    for timestamp in timestamps:
        rounded = round(timestamp, 2)
        if rounded not in unique:
            unique.append(rounded)
    return unique
