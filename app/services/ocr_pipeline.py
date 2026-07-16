from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings
from app.services.video_frames import ExtractedFrame


@dataclass(frozen=True)
class OcrFrameResult:
    labels: list[str]
    confidence: float


def extract_ocr_labels(frames: list[ExtractedFrame]) -> dict[float, OcrFrameResult]:
    return {frame.timestamp: labels_for_frame(frame.image_path) for frame in frames}


def labels_for_frame(image_path: Path) -> OcrFrameResult:
    for page_mode in (6, 11):
        result = tesseract_result(image_path, page_mode)
        if result.labels and result.confidence >= 0.38:
            return result
    return tesseract_result(image_path, 6)


def tesseract_result(image_path: Path, page_mode: int) -> OcrFrameResult:
    tsv_output = tesseract_tsv(image_path, page_mode)
    entries = parse_tsv_entries(tsv_output)
    labels = filtered_lines([entry["text"] for entry in entries])
    confidence = normalized_confidence(entries)
    return OcrFrameResult(labels=labels, confidence=confidence)


def tesseract_tsv(image_path: Path, page_mode: int) -> str:
    command = [
        get_settings().tesseract_binary,
        str(image_path),
        "stdout",
        "--psm",
        str(page_mode),
        "tsv",
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=get_settings().tesseract_timeout_seconds,
        )
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return ""
    except subprocess.CalledProcessError:
        return ""
    return result.stdout


def parse_tsv_entries(tsv_output: str) -> list[dict[str, str]]:
    if not tsv_output.strip():
        return []
    reader = csv.DictReader(io.StringIO(tsv_output), delimiter="\t")
    return [row for row in reader if row.get("text", "").strip()]


def normalized_confidence(entries: list[dict[str, str]]) -> float:
    confidences = [entry_confidence(entry) for entry in entries if entry_confidence(entry) > 0]
    if not confidences:
        return 0.0
    return round(sum(confidences) / len(confidences) / 100, 3)


def entry_confidence(entry: dict[str, str]) -> float:
    raw_value = entry.get("conf", "-1").strip()
    try:
        return max(0.0, float(raw_value))
    except ValueError:
        return 0.0


def filtered_lines(lines: list[str]) -> list[str]:
    cleaned = [normalized_line(line) for line in lines]
    return [line for line in cleaned if valid_line(line)]


def normalized_line(line: str) -> str:
    return " ".join(line.strip().split())


def valid_line(line: str) -> bool:
    return len(line) >= 2 and any(character.isalpha() for character in line)
