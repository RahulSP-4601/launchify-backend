from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar, cast

from app.services.inferred_recording_support import normalize_label

BEAM_WIDTH = 4
T = TypeVar("T")


@dataclass(frozen=True)
class SequenceCandidate:
    items: tuple[object, ...]
    score: float
    branch: str
    previous_after: str


def select_best_sequence(
    candidates_by_step: list[list[T]],
    *,
    candidate_score: Callable[[T], float],
    candidate_branch: Callable[[T], str],
    candidate_after: Callable[[T], str],
    candidate_label: Callable[[T], str],
) -> list[T]:
    beams = [SequenceCandidate(items=tuple(), score=0.0, branch="generic", previous_after="unknown")]
    for step_candidates in candidates_by_step:
        beams = advance_beams(
            beams,
            step_candidates,
            candidate_score,
            candidate_branch,
            candidate_after,
            candidate_label,
        )
    best = max(beams, key=lambda item: item.score, default=None)
    return [cast(T, item) for item in best.items] if best is not None else []


def advance_beams(
    beams: list[SequenceCandidate],
    step_candidates: list[T],
    candidate_score: Callable[[T], float],
    candidate_branch: Callable[[T], str],
    candidate_after: Callable[[T], str],
    candidate_label: Callable[[T], str],
) -> list[SequenceCandidate]:
    expanded: list[SequenceCandidate] = []
    for beam in beams:
        for candidate in step_candidates:
            expanded.append(
                SequenceCandidate(
                    items=(*beam.items, candidate),
                    score=beam.score + path_score(
                        beam,
                        candidate,
                        candidate_score,
                        candidate_branch,
                        candidate_after,
                        candidate_label,
                    ),
                    branch=merged_branch(beam.branch, candidate_branch(candidate)),
                    previous_after=candidate_after(candidate),
                )
            )
    ranked = sorted(expanded, key=lambda item: item.score, reverse=True)
    return ranked[:BEAM_WIDTH] or beams


def path_score(
    beam: SequenceCandidate,
    candidate: T,
    candidate_score: Callable[[T], float],
    candidate_branch: Callable[[T], str],
    candidate_after: Callable[[T], str],
    candidate_label: Callable[[T], str],
) -> float:
    score = candidate_score(candidate)
    score -= branch_switch_penalty(beam.branch, candidate_branch(candidate))
    score -= repeated_after_penalty(beam.previous_after, candidate_after(candidate))
    score -= repeated_label_penalty(beam.items, candidate, candidate_label)
    score += known_state_bonus(candidate_after(candidate))
    return round(score, 3)


def merged_branch(current: str, candidate: str) -> str:
    if candidate == "generic":
        return current
    return candidate


def branch_switch_penalty(current: str, candidate: str) -> float:
    if current == "generic" or candidate == "generic" or current == candidate:
        return 0.0
    return 0.42


def repeated_after_penalty(previous_after: str, current_after: str) -> float:
    if previous_after in {"unknown", "generic"} or current_after in {"unknown", "generic"}:
        return 0.0
    return 0.35 if previous_after == current_after else 0.0


def repeated_label_penalty(
    prior_items: tuple[object, ...],
    candidate: T,
    candidate_label: Callable[[T], str],
) -> float:
    label = normalize_label(candidate_label(candidate))
    if not label:
        return 0.0
    prior_labels = {
        normalize_label(candidate_label(item)) for item in prior_items[-2:] if isinstance(item, type(candidate))
    }
    return 0.24 if label in prior_labels else 0.0


def known_state_bonus(state_after: str) -> float:
    return 0.12 if state_after not in {"unknown", "generic"} else 0.0
