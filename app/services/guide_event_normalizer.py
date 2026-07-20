from __future__ import annotations

from typing import Sequence

from app.models.projects import SessionEventRecord, TranscriptSegment
from app.services.guide_event_dedupe import synthetic_duplicate_index, synthetic_event_score

MEANINGFUL_EVENT_TYPES = frozenset({"click", "input", "navigation", "keypress", "keydown", "focus", "custom"})


def normalize_events(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
) -> list[SessionEventRecord]:
    max_transcript_end = max((segment.end for segment in transcript), default=0.0)
    normalized: list[SessionEventRecord] = []
    seen_input_keys: set[tuple[str, str]] = set()
    for event in sorted(events, key=lambda item: item.timestamp):
        if event.metadata.get("grounding_source") == "transcript_fallback":
            continue
        if event.type not in MEANINGFUL_EVENT_TYPES and event.type != "input":
            continue
        timestamp = normalized_timestamp(event.timestamp, max_transcript_end)
        selector = (event.target.selector or "").strip()
        value = (event.value or "").strip()
        if event.type == "input":
            dedupe_key = (selector, value)
            if dedupe_key in seen_input_keys:
                continue
            seen_input_keys.add(dedupe_key)
        normalized_event = event.model_copy(update={"timestamp": timestamp})
        duplicate_index = synthetic_duplicate_index(normalized, normalized_event)
        if duplicate_index is None:
            normalized.append(normalized_event)
            continue
        existing = normalized[duplicate_index]
        if synthetic_event_score(normalized_event) > synthetic_event_score(existing):
            normalized[duplicate_index] = normalized_event
    return normalized


def normalized_timestamp(value: float, transcript_end: float) -> float:
    if transcript_end > 0 and value > transcript_end * 10:
        return round(value / 1000.0, 2)
    if value > 10_000:
        return round(value / 1000.0, 2)
    return round(max(value, 0.0), 2)
