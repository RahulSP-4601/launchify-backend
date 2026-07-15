from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import get_settings

FRAME_WIDTH = 64
FRAME_HEIGHT = 36
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT


def frame_diff_scores(video_path: Path, timestamps: list[float]) -> list[float]:
    frames = [grayscale_frame(video_path, timestamp) for timestamp in timestamps]
    if not frames:
        return []
    scores = [0.0]
    scores.extend(diff_score(previous, current) for previous, current in zip(frames, frames[1:], strict=True))
    return [round(score, 3) for score in normalize_scores(scores)]


def grayscale_frame(video_path: Path, timestamp: float) -> bytes:
    command = ffmpeg_raw_frame_command(video_path, timestamp)
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for frame-diff analysis.") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"FFmpeg failed while computing frame diff: {detail}") from exc
    if len(result.stdout) != FRAME_BYTES:
        raise RuntimeError("FFmpeg did not return the expected raw grayscale frame.")
    return result.stdout


def diff_score(previous_frame: bytes, current_frame: bytes) -> float:
    total = sum(abs(previous - current) for previous, current in zip(previous_frame, current_frame, strict=True))
    return total / (FRAME_BYTES * 255)


def normalize_scores(scores: list[float]) -> list[float]:
    peak = max(scores, default=0.0)
    if peak <= 0:
        return scores
    return [min(1.0, score / peak) for score in scores]


def ffmpeg_raw_frame_command(video_path: Path, timestamp: float) -> list[str]:
    ffmpeg_binary = get_settings().ffmpeg_binary
    return [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(timestamp),
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
