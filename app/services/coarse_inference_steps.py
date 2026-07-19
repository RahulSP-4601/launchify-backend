from __future__ import annotations

import re

from app.models.projects import TranscriptSegment
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.inference_step_builder import InferenceStep


def coarse_transcript_steps(transcript: list[TranscriptSegment]) -> list[InferenceStep]:
    from app.services.inference_step_builder import InferenceStep

    enriched = [step for segment in transcript for step in clause_steps_for_segment(segment)]
    if not enriched:
        return []
    return merge_coarse_steps(sorted(enriched, key=lambda item: item.start))


def clause_steps_for_segment(segment: TranscriptSegment) -> list[InferenceStep]:
    from app.services.inference_step_builder import InferenceStep, clean_text

    clauses = split_coarse_clauses(segment.text)
    if len(clauses) <= 1:
        return [InferenceStep(start=round(segment.start, 2), end=round(segment.end, 2), text=clean_text(segment.text))]
    durations = clause_durations(segment.start, segment.end, clauses)
    return [
        InferenceStep(start=round(start, 2), end=round(end, 2), text=clean_text(clause))
        for clause, start, end in durations
        if clean_text(clause)
    ]


def split_coarse_clauses(text: str) -> list[str]:
    from app.services.inference_step_builder import clean_text

    parts = re.split(r"\bor you can\b|\bafter\b|\bonce\b|\bthen\b|\bso\b|[.!?;]+", text, flags=re.IGNORECASE)
    clauses = [" ".join(part.strip(" ,.").split()) for part in parts if part.strip(" ,.")]
    return clauses or [clean_text(text)]


def clause_durations(start: float, end: float, clauses: list[str]) -> list[tuple[str, float, float]]:
    total_duration = max(end - start, 0.0)
    if total_duration <= 0:
        return [(clause, start, start) for clause in clauses[:1]]
    weights = [max(len(clause.split()), 3) for clause in clauses]
    total_weight = sum(weights)
    cursor = start
    allocated: list[tuple[str, float, float]] = []
    for index, clause in enumerate(clauses):
        span = total_duration * (weights[index] / max(total_weight, 1))
        clause_end = end if index == len(clauses) - 1 else min(cursor + span, end)
        if clause_end <= cursor:
            continue
        allocated.append((clause, cursor, clause_end))
        cursor = clause_end
    return allocated


def merge_coarse_steps(steps: list[InferenceStep]) -> list[InferenceStep]:
    from app.services.inference_step_builder import merge_group

    merged: list[InferenceStep] = []
    for step in steps:
        previous = merged[-1] if merged else None
        if previous is not None and should_merge_coarse_step(previous, step):
            merged[-1] = merge_group([previous, step])
            continue
        merged.append(step)
    return merged


def should_merge_coarse_step(previous: InferenceStep, current: InferenceStep) -> bool:
    from app.services.inference_step_builder import ABSOLUTE_MAX_STEP_SECONDS, action_score, merged_duration

    if max(current.start - previous.end, 0.0) >= 0.45:
        return False
    if action_score(previous.text) > 0 and action_score(current.text) > 0:
        return False
    return merged_duration(previous, current) <= ABSOLUTE_MAX_STEP_SECONDS


def backfill_coverage_steps(selected: list[InferenceStep], fallback_steps: list[InferenceStep], target_count: int) -> list[InferenceStep]:
    from app.services.inference_step_builder import signal_score

    if len(selected) >= target_count:
        return selected
    supplemented = selected[:]
    for step in sorted(fallback_steps, key=signal_score, reverse=True):
        if any(abs(existing.start - step.start) <= 2.4 for existing in supplemented):
            continue
        supplemented.append(step)
        if len(supplemented) >= target_count:
            break
    return sorted(supplemented, key=lambda item: item.start)
