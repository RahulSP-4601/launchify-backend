from __future__ import annotations

from app.services.action_classifier import event_action_class
from app.models.projects import SessionEventRecord, TranscriptSegment
from app.services.inferred_recording_support import actionable_label, duplicate_event

MIN_SUPPLEMENT_SCORE = 0.42
ACTIONLIKE_CLASSES = frozenset(
    {
        "auth_action",
        "menu_open",
        "tab_switch",
        "card_selection",
        "button_click",
        "input_entry",
        "navigation",
    }
)


def preserve_sparse_timeline(
    selected: list[SessionEventRecord],
    candidates: list[SessionEventRecord],
    transcript: list[TranscriptSegment],
) -> list[SessionEventRecord]:
    target_count = minimum_expected_events(transcript)
    if len(selected) >= target_count:
        return sorted(selected, key=lambda item: item.timestamp)
    supplemented = selected[:]
    scene_pool = distinct_scene_candidates(candidates)
    for candidate in scene_pool:
        if len(supplemented) >= target_count:
            break
        if not can_add_candidate(candidate, supplemented):
            continue
        supplemented.append(candidate)
    return sorted(supplemented, key=lambda item: item.timestamp)


def minimum_expected_events(transcript: list[TranscriptSegment]) -> int:
    duration = max((segment.end for segment in transcript), default=0.0)
    if duration >= 35.0:
        return 4
    if duration >= 18.0:
        return 3
    return 2


def distinct_scene_candidates(candidates: list[SessionEventRecord]) -> list[SessionEventRecord]:
    ranked = sorted(candidates, key=timeline_candidate_rank, reverse=True)
    best_by_scene: dict[int, SessionEventRecord] = {}
    for event in ranked:
        scene_id = scene_number(event)
        if scene_id <= 0 or scene_id in best_by_scene:
            continue
        best_by_scene[scene_id] = event
    return sorted(best_by_scene.values(), key=lambda item: timeline_candidate_rank(item), reverse=True)


def timeline_candidate_rank(event: SessionEventRecord) -> tuple[float, float, float]:
    return (
        float(event.metadata.get("score", "0") or 0.0),
        scene_priority(event),
        -event.timestamp,
    )


def scene_priority(event: SessionEventRecord) -> float:
    scene_id = scene_number(event)
    if scene_id <= 0:
        return 0.0
    return min(scene_id / 12.0, 1.0)


def can_add_candidate(candidate: SessionEventRecord, selected: list[SessionEventRecord]) -> bool:
    if not supplement_candidate_is_meaningful(candidate):
        return False
    if any(duplicate_event(existing, candidate) for existing in selected):
        return False
    if scene_number(candidate) in {scene_number(existing) for existing in selected}:
        return False
    return True


def supplement_candidate_is_meaningful(candidate: SessionEventRecord) -> bool:
    score = float(candidate.metadata.get("score", "0") or 0.0)
    if score < MIN_SUPPLEMENT_SCORE:
        return False
    label = candidate.target.label or candidate.target.text
    action_class = event_action_class(candidate)
    if action_class in ACTIONLIKE_CLASSES:
        return actionable_label(label) or candidate.type in {"input", "navigation"}
    return candidate.type in {"input", "navigation"} and actionable_label(label)


def scene_number(event: SessionEventRecord) -> int:
    return int(event.metadata.get("scene_number", "0") or 0)
