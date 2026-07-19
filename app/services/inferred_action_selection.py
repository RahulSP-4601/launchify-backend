from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import SessionEventRecord
from app.services.inferred_recording_support import duplicate_event, low_signal_label, normalize_label
from app.services.guide_event_dedupe import synthetic_event_score

MAX_GLOBAL_EVENTS = 12


@dataclass(frozen=True)
class SceneEventCandidate:
    scene_number: int
    event: SessionEventRecord


def select_global_events(candidates: list[SceneEventCandidate]) -> list[SessionEventRecord]:
    ranked = sorted(candidates, key=rank_candidate, reverse=True)
    selected: list[SceneEventCandidate] = []
    for candidate in ranked:
        if should_skip_candidate(candidate, selected):
            continue
        duplicate_index = duplicate_selected_index(candidate, selected)
        if duplicate_index is not None:
            if synthetic_event_score(candidate.event) > synthetic_event_score(selected[duplicate_index].event):
                selected[duplicate_index] = candidate
            continue
        selected.append(candidate)
        if len(selected) >= MAX_GLOBAL_EVENTS:
            break
    return sorted((candidate.event for candidate in selected), key=lambda item: item.timestamp)


def rank_candidate(candidate: SceneEventCandidate) -> tuple[float, float, float, float, float]:
    label = candidate.event.target.label or candidate.event.target.text or ""
    return (
        synthetic_event_score(candidate.event),
        timeline_position_score(candidate),
        action_class_priority(candidate),
        0.0 if low_signal_label(label) else 1.0,
        -candidate.event.timestamp,
    )


def should_skip_candidate(candidate: SceneEventCandidate, selected: list[SceneEventCandidate]) -> bool:
    if not selected:
        return False
    transcript_excerpt = normalize_label(candidate.event.metadata.get("transcript_excerpt", ""))
    same_excerpt = sum(
        1 for item in selected if transcript_excerpt and normalize_label(item.event.metadata.get("transcript_excerpt", "")) == transcript_excerpt
    )
    same_class = sum(1 for item in selected if action_class(candidate) == action_class(item))
    if same_excerpt >= 2:
        return True
    if action_class(candidate) == "auth_action" and same_class >= 2:
        return True
    return False


def duplicate_selected_index(
    candidate: SceneEventCandidate,
    selected: list[SceneEventCandidate],
) -> int | None:
    return next((index for index, item in enumerate(selected) if duplicate_event(item.event, candidate.event)), None)


def timeline_position_score(candidate: SceneEventCandidate) -> float:
    scene_score = min(candidate.scene_number / 12.0, 1.0)
    time_score = min(candidate.event.timestamp / 45.0, 1.0)
    return round(max(scene_score, time_score), 3)


def action_class_priority(candidate: SceneEventCandidate) -> float:
    priorities = {
        "button_click": 1.0,
        "card_selection": 0.96,
        "menu_open": 0.93,
        "tab_switch": 0.91,
        "navigation": 0.89,
        "input_entry": 0.87,
        "auth_action": 0.78,
        "result_state": 0.72,
        "explanatory_hold": 0.68,
        "generic_action": 0.64,
    }
    return priorities.get(action_class(candidate), 0.64)


def action_class(candidate: SceneEventCandidate) -> str:
    return candidate.event.metadata.get("action_class", "generic_action").strip() or "generic_action"
