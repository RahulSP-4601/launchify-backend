from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import SessionEventRecord
from app.services.inferred_recording_support import duplicate_event, low_signal_label
from app.services.guide_event_dedupe import synthetic_event_score

MAX_GLOBAL_EVENTS = 12


@dataclass(frozen=True)
class SceneEventCandidate:
    scene_number: int
    event: SessionEventRecord


def select_global_events(candidates: list[SceneEventCandidate]) -> list[SessionEventRecord]:
    ranked = sorted(candidates, key=rank_candidate, reverse=True)
    selected: list[SessionEventRecord] = []
    for candidate in ranked:
        duplicate_index = next((index for index, existing in enumerate(selected) if duplicate_event(existing, candidate.event)), None)
        if duplicate_index is not None:
            if synthetic_event_score(candidate.event) > synthetic_event_score(selected[duplicate_index]):
                selected[duplicate_index] = candidate.event
            continue
        selected.append(candidate.event)
        if len(selected) >= MAX_GLOBAL_EVENTS:
            break
    return sorted(selected, key=lambda item: item.timestamp)


def rank_candidate(candidate: SceneEventCandidate) -> tuple[float, float, float, float]:
    label = candidate.event.target.label or candidate.event.target.text or ""
    return (
        synthetic_event_score(candidate.event),
        0.0 if low_signal_label(label) else 1.0,
        1.0 if candidate.event.type == "click" else 0.86 if candidate.event.type == "input" else 0.72,
        -candidate.event.timestamp,
    )
