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
    if use_weighted_alignment(scenes, transcript):
        return weighted_scene_ranges(scenes, transcript)
    total_weight = sum(scene_weight(scene) for scene in scenes) or 1.0
    transcript_start = transcript[0].start
    transcript_end = transcript[-1].end
    accumulated_weight = 0.0
    ranges: list[tuple[float, float]] = []
    search_start = 0
    for index, scene in enumerate(scenes):
        remaining_scenes = len(scenes) - index
        latest_start = max(search_start, len(transcript) - remaining_scenes)
        remaining_after = len(scenes) - index - 1
        latest_end = len(transcript) - remaining_after - 1
        start_progress = accumulated_weight / total_weight
        accumulated_weight += scene_weight(scene)
        end_progress = accumulated_weight / total_weight
        start_index, end_index = best_window_for_scene(
            scene,
            transcript,
            search_start,
            latest_start,
            latest_end,
            target_midpoint=weighted_midpoint(transcript_start, transcript_end, start_progress, end_progress),
        )
        ranges.append((transcript[start_index].start, transcript[end_index].end))
        search_start = min(end_index + 1, len(transcript) - 1)
    return normalize_ranges(ranges, transcript[-1].end)


def best_window_for_scene(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    search_start: int,
    latest_start: int,
    latest_end: int,
    *,
    target_midpoint: float,
) -> tuple[int, int]:
    desired_segments = max(1, round(scene.estimated_duration_seconds / 4))
    max_window = min(max(2, desired_segments + 1), 5)
    scene_text = scene_text_for_matching(scene)
    best_score = -1.0
    best_range = (search_start, search_start)
    desired_duration = max(scene.estimated_duration_seconds, 1.0)
    for start in range(search_start, latest_start + 1):
        for window_size in range(1, max_window + 1):
            end = start + window_size - 1
            if end > latest_end or end >= len(transcript):
                break
            score = score_window(scene_text, transcript[start : end + 1]) + timing_score(
                transcript[start].start,
                transcript[end].end,
                target_midpoint,
                desired_duration,
            )
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


def scene_weight(scene: LaunchScriptScene) -> float:
    return max(scene.estimated_duration_seconds, 1.0)


def weighted_midpoint(start: float, end: float, start_progress: float, end_progress: float) -> float:
    return start + ((start_progress + end_progress) / 2) * max(end - start, 0.0)


def timing_score(start: float, end: float, target_midpoint: float, desired_duration: float) -> float:
    midpoint = (start + end) / 2
    midpoint_penalty = abs(midpoint - target_midpoint) / max(desired_duration * 2, 1.0)
    duration_penalty = abs((end - start) - desired_duration) / max(desired_duration, 1.0)
    return max(0.0, 0.35 - (midpoint_penalty * 0.2) - (duration_penalty * 0.15))


def normalize_ranges(ranges: list[tuple[float, float]], max_end: float) -> list[tuple[float, float]]:
    normalized_reversed: list[tuple[float, float]] = []
    total_ranges = len(ranges)
    next_start = round(max_end, 2)
    for reversed_index, (start, end) in enumerate(reversed(ranges)):
        remaining_before = total_ranges - reversed_index - 1
        minimum_end = round((remaining_before + 1) * 0.25, 2)
        bounded_end = min(round(min(end, max_end), 2), next_start)
        bounded_end = max(bounded_end, minimum_end)
        latest_start = max(0.0, round(bounded_end - 0.25, 2))
        bounded_start = min(round(start, 2), latest_start)
        normalized_reversed.append((bounded_start, bounded_end))
        next_start = bounded_start
    return list(reversed(normalized_reversed))


def use_weighted_alignment(
    scenes: list[LaunchScriptScene],
    transcript: list[TranscriptSegment],
) -> bool:
    return len(transcript) <= 3 and len(scenes) > len(transcript) and low_overlap_transcript(scenes, transcript)


def weighted_scene_ranges(
    scenes: list[LaunchScriptScene],
    transcript: list[TranscriptSegment],
) -> list[tuple[float, float]]:
    total_weight = sum(scene_weight(scene) for scene in scenes) or 1.0
    start = transcript[0].start
    end = transcript[-1].end
    cursor = start
    ranges: list[tuple[float, float]] = []
    for index, scene in enumerate(scenes):
        span = max((end - start) * (scene_weight(scene) / total_weight), 0.8)
        next_end = end if index == len(scenes) - 1 else min(cursor + span, end)
        ranges.append((round(cursor, 2), round(max(next_end, cursor + 0.8), 2)))
        cursor = next_end
    return normalize_ranges(ranges, end)


def low_overlap_transcript(
    scenes: list[LaunchScriptScene],
    transcript: list[TranscriptSegment],
) -> bool:
    segment_texts = [[segment] for segment in transcript]
    scene_scores = [
        max((score_window(scene_text_for_matching(scene), window) for window in segment_texts), default=0.0)
        for scene in scenes
    ]
    average_best_score = sum(scene_scores) / max(len(scene_scores), 1)
    return average_best_score < 0.32
