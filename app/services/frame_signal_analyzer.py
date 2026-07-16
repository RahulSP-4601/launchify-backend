from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

FRAME_WIDTH = 64
FRAME_HEIGHT = 36
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT
FRAME_SEEK_OFFSETS = (0.0, -0.08, -0.2)
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameDiffResult:
    scores: list[float]
    available: bool


def frame_diff_scores(video_path: Path, timestamps: list[float]) -> FrameDiffResult:
    frames: list[bytes] = []
    for timestamp in timestamps:
        frame = grayscale_frame(video_path, timestamp)
        if frame is None:
            return FrameDiffResult(scores=[], available=False)
        frames.append(frame)
    if not frames:
        return FrameDiffResult(scores=[], available=False)
    scores = [0.0]
    scores.extend(diff_score(previous, current) for previous, current in zip(frames, frames[1:]))
    return FrameDiffResult(scores=[round(score, 3) for score in normalize_scores(scores)], available=True)


def grayscale_frame(video_path: Path, timestamp: float) -> bytes | None:
    failure_details: list[str] = []
    for seek_offset in FRAME_SEEK_OFFSETS:
        candidate_timestamp = max(0.0, round(timestamp + seek_offset, 3))
        result = run_raw_frame_command(video_path, candidate_timestamp)
        if result is None:
            failure_details.append(f"{candidate_timestamp:.3f}s: no frame")
            continue
        if len(result) == FRAME_BYTES:
            return result
        failure_details.append(f"{candidate_timestamp:.3f}s: {len(result)} bytes")
    logger.warning(
        "Skipping frame-diff motion evidence for %s at %.3fs (%s)",
        video_path.name,
        timestamp,
        ", ".join(failure_details) or "unknown frame extraction failure",
    )
    return None


def run_raw_frame_command(video_path: Path, timestamp: float) -> bytes | None:
    command = ffmpeg_raw_frame_command(video_path, timestamp)
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=get_settings().ffmpeg_timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is required for frame-diff analysis.") from exc
    except subprocess.TimeoutExpired:
        logger.warning(
            "FFmpeg frame-diff extraction timed out for %s at %.3fs after %ss",
            video_path.name,
            timestamp,
            get_settings().ffmpeg_timeout_seconds,
        )
        return None
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="ignore").strip()
        logger.warning("FFmpeg frame-diff extraction failed for %s at %.3fs: %s", video_path.name, timestamp, detail)
        return None
    return result.stdout or None


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
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
