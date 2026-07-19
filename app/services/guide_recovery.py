from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.models.projects import SessionEventRecord, TranscriptSegment
from app.services.walkthrough_guardrails import sparse_action_count

RECOVERY_CONNECTORS = re.compile(r"\b(?:then|next|after that|once|from here|now)\b", re.IGNORECASE)
MIN_RECOVERY_SEGMENT_SECONDS = 2.0


@dataclass(frozen=True)
class RecoveryStepSeed:
    start: float
    end: float
    transcript_excerpt: str


def needs_cluster_recovery(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
) -> bool:
    duration = max((segment.end for segment in transcript), default=0.0)
    return sparse_action_count(len(events), duration)


def recovered_step_seeds(transcript: Sequence[TranscriptSegment]) -> list[RecoveryStepSeed]:
    if not transcript:
        return []
    seeds: list[RecoveryStepSeed] = []
    current_start = transcript[0].start
    current_end = transcript[0].end
    parts = [transcript[0].text.strip()]
    for index, segment in enumerate(transcript[1:], start=1):
        previous = transcript[index - 1]
        gap = max(segment.start - previous.end, 0.0)
        split = gap >= 1.2 or RECOVERY_CONNECTORS.search(segment.text or "") is not None
        if split and current_end - current_start >= MIN_RECOVERY_SEGMENT_SECONDS:
            seeds.append(RecoveryStepSeed(start=round(current_start, 2), end=round(current_end, 2), transcript_excerpt=join_parts(parts)))
            current_start = segment.start
            parts = [segment.text.strip()]
        else:
            parts.append(segment.text.strip())
        current_end = segment.end
    seeds.append(RecoveryStepSeed(start=round(current_start, 2), end=round(current_end, 2), transcript_excerpt=join_parts(parts)))
    return [seed for seed in seeds if seed.transcript_excerpt.strip()]


def join_parts(parts: Sequence[str]) -> str:
    return " ".join(part for part in parts if part)
