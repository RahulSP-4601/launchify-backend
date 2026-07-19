from __future__ import annotations

from app.models.projects import FocusBox, RecordingSessionRecord, SessionEventRecord, TranscriptSegment
from app.services.inferred_action_selection import SceneEventCandidate, select_global_events
from app.services.inferred_recording_support import box_area
from app.services.walkthrough_guardrails import session_is_under_grounded

MIN_CANDIDATE_SIGNAL_COUNT = 3


def select_global_event_candidates(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    candidates = [SceneEventCandidate(int(event.metadata.get("scene_number", "0") or 0), event) for event in events]
    return select_global_events(candidates)


def should_retry_strict_recovery(
    events: list[SessionEventRecord],
    transcript: list[TranscriptSegment],
) -> bool:
    snapshot = RecordingSessionRecord(events=events, started_at="0.0", ended_at=f"{max((segment.end for segment in transcript), default=0.0):.2f}")
    return session_is_under_grounded(snapshot, transcript)


def candidate_signal_count(
    stop_score: float,
    action_phrase: float,
    visual_strength: float,
    focus_box: FocusBox,
    diff_score: float,
) -> int:
    signals = 0
    if stop_score >= 0.14:
        signals += 1
    if box_area(focus_box) <= 0.16:
        signals += 1
    if diff_score >= 0.16 or visual_strength >= 0.52:
        signals += 1
    if action_phrase >= 0.24:
        signals += 1
    return signals


def plausible_focus_box(focus_box: FocusBox) -> bool:
    return 0.0 < box_area(focus_box) <= 0.2
