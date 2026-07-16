from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings


@dataclass(frozen=True)
class ExtractedFrame:
    timestamp: float
    image_path: Path


def extract_scene_frames(
    video_path: Path,
    scene_numbers: list[int],
    scene_ranges: list[tuple[float, float]],
    output_dir: Path,
) -> dict[int, list[ExtractedFrame]]:
    planned_frames = [
        (scene_number, frame_index, frame_time)
        for scene_number, scene_range in zip(scene_numbers, scene_ranges, strict=True)
        for frame_index, frame_time in enumerate(sample_times(*scene_range), start=1)
    ]
    max_workers = min(max(get_settings().visual_analysis_concurrency, 1) * 2, max(len(planned_frames), 1))
    frames_by_scene: dict[int, list[ExtractedFrame]] = {scene_number: [] for scene_number in scene_numbers}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        extracted = executor.map(
            lambda frame: build_extracted_frame(video_path, output_dir, *frame),
            planned_frames,
        )
        for scene_number, extracted_frame in extracted:
            frames_by_scene[scene_number].append(extracted_frame)
    for extracted_frames in frames_by_scene.values():
        extracted_frames.sort(key=lambda frame: frame.timestamp)
    return frames_by_scene


def build_extracted_frame(
    video_path: Path,
    output_dir: Path,
    scene_number: int,
    frame_index: int,
    timestamp: float,
) -> tuple[int, ExtractedFrame]:
    return (
        scene_number,
        ExtractedFrame(
            timestamp=timestamp,
            image_path=extract_frame(video_path, output_dir, scene_number, frame_index, timestamp),
        ),
    )


def sample_times(start: float, end: float) -> list[float]:
    duration = max(end - start, 0.3)
    return unique_times(
        [
            start + min(0.2, duration * 0.08),
            start + duration * 0.2,
            start + duration * 0.4,
            start + duration * 0.6,
            start + duration * 0.8,
            end - min(0.2, duration * 0.08),
        ]
    )


def extract_frame(
    video_path: Path,
    output_dir: Path,
    scene_number: int,
    frame_index: int,
    timestamp: float,
) -> Path:
    output_path = output_dir / f"scene-{scene_number}-frame-{frame_index}.jpg"
    command = build_ffmpeg_command(video_path, output_path, timestamp)
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
    return output_path


def build_ffmpeg_command(video_path: Path, output_path: Path, timestamp: float) -> list[str]:
    ffmpeg_binary = get_settings().ffmpeg_binary
    return [
        ffmpeg_binary,
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(output_path),
    ]


def unique_times(timestamps: list[float]) -> list[float]:
    unique: list[float] = []
    for timestamp in timestamps:
        rounded = round(timestamp, 2)
        if rounded not in unique:
            unique.append(rounded)
    return unique
