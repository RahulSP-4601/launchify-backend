from __future__ import annotations

from typing import Sequence

from app.models.projects import GuideRecord, RecordingSessionRecord, TranscriptSegment

LONG_WALKTHROUGH_SECONDS = 18.0
MAX_SINGLE_STEP_RATIO = 0.82
MIN_LONG_WALKTHROUGH_STEPS = 2


def recording_duration_seconds(
    recording_session: RecordingSessionRecord | None,
    transcript: Sequence[TranscriptSegment],
) -> float:
    session_end = parse_time(recording_session.ended_at) if recording_session is not None else 0.0
    transcript_end = max((segment.end for segment in transcript), default=0.0)
    return round(max(session_end, transcript_end), 2)


def parse_time(value: str) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def sparse_action_count(action_count: int, duration_seconds: float) -> bool:
    return duration_seconds >= LONG_WALKTHROUGH_SECONDS and action_count < MIN_LONG_WALKTHROUGH_STEPS


def guide_is_under_grounded(
    guide: GuideRecord | None,
    duration_seconds: float,
) -> bool:
    if guide is None or not guide.steps:
        return False
    if duration_seconds < LONG_WALKTHROUGH_SECONDS:
        return False
    if len(guide.steps) < MIN_LONG_WALKTHROUGH_STEPS:
        return True
    longest_step = max((step.end - step.start for step in guide.steps), default=0.0)
    return longest_step >= duration_seconds * MAX_SINGLE_STEP_RATIO
