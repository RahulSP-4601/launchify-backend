from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.models.projects import LaunchScriptScene, TranscriptSegment

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


def align_script_scenes(
    scenes: list[LaunchScriptScene],
    transcript: list[TranscriptSegment],
) -> list[tuple[float, float]]:
    if not transcript:
        raise RuntimeError("Transcript is required before generating the edit plan.")
    ranges: list[tuple[float, float]] = []
    search_start = 0
    for scene in scenes:
        start_index, end_index = best_window_for_scene(scene, transcript, search_start)
        ranges.append((transcript[start_index].start, transcript[end_index].end))
        search_start = min(end_index + 1, len(transcript) - 1)
    return normalize_ranges(ranges, transcript[-1].end)


def best_window_for_scene(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    search_start: int,
) -> tuple[int, int]:
    desired_segments = max(1, round(scene.estimated_duration_seconds / 4))
    max_window = min(max(2, desired_segments + 1), 5)
    scene_text = scene_text_for_matching(scene)
    best_score = -1.0
    best_range = (search_start, search_start)
    for start in range(search_start, len(transcript)):
        for window_size in range(1, max_window + 1):
            end = start + window_size - 1
            if end >= len(transcript):
                break
            score = score_window(scene_text, transcript[start : end + 1])
            if score > best_score:
                best_score = score
                best_range = (start, end)
    return best_range


def scene_text_for_matching(scene: LaunchScriptScene) -> str:
    parts = (scene.spoken_line, scene.source_excerpt, scene.on_screen_text)
    return " ".join(part for part in parts if part.strip())


def score_window(scene_text: str, window: list[TranscriptSegment]) -> float:
    window_text = " ".join(segment.text for segment in window)
    token_score = overlap_score(scene_text, window_text)
    sequence_score = SequenceMatcher(a=scene_text.lower(), b=window_text.lower()).ratio()
    return token_score * 0.7 + sequence_score * 0.3


def overlap_score(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    common = len(left_tokens & right_tokens)
    return common / max(len(left_tokens), 1)


def tokenize(value: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(value.lower()) if len(token) > 1}


def normalize_ranges(ranges: list[tuple[float, float]], max_end: float) -> list[tuple[float, float]]:
    normalized: list[tuple[float, float]] = []
    previous_end = 0.0
    for start, end in ranges:
        bounded_start = max(previous_end, round(start, 2))
        bounded_end = max(bounded_start + 0.25, round(min(end, max_end), 2))
        normalized.append((bounded_start, bounded_end))
        previous_end = bounded_end
    return normalized
