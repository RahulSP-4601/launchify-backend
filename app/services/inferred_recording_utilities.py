from __future__ import annotations

import subprocess
from pathlib import Path

from app.core.config import get_settings
from app.models.projects import SessionEventRecord
from app.services.guide_event_dedupe import synthetic_event_score
from app.services.inferred_recording_support import normalize_label

DEFAULT_VIEWPORT = (1280, 720)


def collapse_semantic_tail_events(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    collapsed: list[SessionEventRecord] = []
    for event in sorted(events, key=lambda item: item.timestamp):
        if collapsed and should_merge_semantic_tail(collapsed[-1], event):
            collapsed[-1] = preferred_tail_event(collapsed[-1], event)
            continue
        collapsed.append(event)
    return collapsed


def video_dimensions(video_path: Path) -> tuple[int, int]:
    settings = get_settings()
    command = [
        settings.ffprobe_binary, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video_path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=settings.ffmpeg_timeout_seconds)
        width_text, height_text = result.stdout.strip().split("x", maxsplit=1)
        return max(int(width_text), 1), max(int(height_text), 1)
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return DEFAULT_VIEWPORT


def should_merge_semantic_tail(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    if right.timestamp - left.timestamp > 1.0:
        return False
    left_action = left.metadata.get("action_class", "")
    right_action = right.metadata.get("action_class", "")
    if left_action not in {"button_click", "focus", "result_state"} or right_action not in {"button_click", "focus", "result_state"}:
        return False
    if left.metadata.get("scene_number") != right.metadata.get("scene_number"):
        return False
    left_label = normalize_label(left.metadata.get("canonical_label", "") or left.target.label or left.target.text)
    right_label = normalize_label(right.metadata.get("canonical_label", "") or right.target.label or right.target.text)
    return bool(left_label and right_label and (left_label == right_label or left_label in right_label or right_label in left_label))


def preferred_tail_event(left: SessionEventRecord, right: SessionEventRecord) -> SessionEventRecord:
    if "before you start learning" in normalize_label(right.target.label or right.target.text):
        return right
    if "before you start learning" in normalize_label(left.target.label or left.target.text):
        return left
    return right if synthetic_event_score(right) >= synthetic_event_score(left) else left
