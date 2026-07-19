from __future__ import annotations

from typing import Sequence

from app.models.projects import GuideRecord, RecordingSessionRecord, SessionEventRecord, TranscriptSegment
from app.services.action_classifier import event_action_class
from app.services.inferred_recording_support import actionable_label

LONG_WALKTHROUGH_SECONDS = 18.0
MAX_SINGLE_STEP_RATIO = 0.82
MIN_LONG_WALKTHROUGH_STEPS = 3
MIN_MEDIUM_WALKTHROUGH_STEPS = 4
MIN_MEANINGFUL_EVENT_SCORE = 0.48
MIN_TIMELINE_COVERAGE_RATIO = 0.42
MAX_WEAK_LABEL_RATIO = 0.45
MAX_LOW_CONFIDENCE_RATIO = 0.5
MAX_REPEATED_TRANSCRIPT_RATIO = 0.5
MAX_AUTH_STATE_RATIO = 0.45


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
    return duration_seconds >= LONG_WALKTHROUGH_SECONDS and action_count < minimum_action_count(duration_seconds)


def minimum_action_count(duration_seconds: float) -> int:
    if duration_seconds >= 45.0:
        return MIN_MEDIUM_WALKTHROUGH_STEPS
    return MIN_LONG_WALKTHROUGH_STEPS


def guide_is_under_grounded(
    guide: GuideRecord | None,
    duration_seconds: float,
) -> bool:
    if guide is None or not guide.steps:
        return False
    if duration_seconds < LONG_WALKTHROUGH_SECONDS:
        return False
    if len(guide.steps) < minimum_action_count(duration_seconds):
        return True
    longest_step = max((step.end - step.start for step in guide.steps), default=0.0)
    return longest_step >= duration_seconds * MAX_SINGLE_STEP_RATIO


def session_is_under_grounded(
    recording_session: RecordingSessionRecord | None,
    transcript: Sequence[TranscriptSegment],
) -> bool:
    if recording_session is None or not recording_session.events:
        return False
    duration_seconds = recording_duration_seconds(recording_session, transcript)
    if duration_seconds < LONG_WALKTHROUGH_SECONDS:
        return False
    meaningful_count = meaningful_event_count(recording_session.events)
    if meaningful_count < minimum_action_count(duration_seconds):
        return True
    coverage_ratio = timeline_coverage_ratio(recording_session.events, duration_seconds)
    if coverage_ratio < MIN_TIMELINE_COVERAGE_RATIO:
        return True
    if weak_label_ratio(recording_session.events) > MAX_WEAK_LABEL_RATIO:
        return True
    if low_confidence_ratio(recording_session.events) > MAX_LOW_CONFIDENCE_RATIO:
        return True
    if auth_state_ratio(recording_session.events) > MAX_AUTH_STATE_RATIO:
        return True
    return repeated_transcript_ratio(recording_session.events) >= MAX_REPEATED_TRANSCRIPT_RATIO


def meaningful_event_count(events: Sequence[SessionEventRecord]) -> int:
    return sum(1 for event in events if event_score(event) >= MIN_MEANINGFUL_EVENT_SCORE and event_is_meaningful(event))


def weak_label_ratio(events: Sequence[SessionEventRecord]) -> float:
    if not events:
        return 1.0
    weak = sum(1 for event in events if not event_has_strong_label_signal(event))
    return round(weak / len(events), 3)


def low_confidence_ratio(events: Sequence[SessionEventRecord]) -> float:
    if not events:
        return 1.0
    weak = sum(1 for event in events if event_score(event) < MIN_MEANINGFUL_EVENT_SCORE)
    return round(weak / len(events), 3)


def repeated_transcript_ratio(events: Sequence[SessionEventRecord]) -> float:
    excerpts = [normalize_excerpt(event.metadata.get("transcript_excerpt", "")) for event in events]
    excerpts = [excerpt for excerpt in excerpts if excerpt]
    if not excerpts:
        return 0.0
    counts: dict[str, int] = {}
    for excerpt in excerpts:
        counts[excerpt] = counts.get(excerpt, 0) + 1
    return round(max(counts.values()) / len(excerpts), 3)


def timeline_coverage_ratio(events: Sequence[SessionEventRecord], duration_seconds: float) -> float:
    if not events or duration_seconds <= 0:
        return 0.0
    timestamps = sorted(normalized_event_timestamp(event) for event in events)
    if len(timestamps) == 1:
        return 0.0
    covered = max(timestamps[-1] - timestamps[0], 0.0)
    return round(min(covered / duration_seconds, 1.0), 3)


def auth_state_ratio(events: Sequence[SessionEventRecord]) -> float:
    if not events:
        return 1.0
    auth_or_state = sum(1 for event in events if auth_related_event(event))
    return round(auth_or_state / len(events), 3)


def event_score(event: SessionEventRecord) -> float:
    try:
        return max(float(event.metadata.get("score", "0")), 0.0)
    except (TypeError, ValueError):
        return 0.0


def event_label(event: SessionEventRecord) -> str:
    return event.target.label or event.target.text or event.target.selector


def normalized_event_timestamp(event: SessionEventRecord) -> float:
    return round(max(float(event.timestamp), 0.0), 2)


def normalize_excerpt(value: str) -> str:
    words = " ".join(value.lower().split())
    return words[:120]


def event_is_meaningful(event: SessionEventRecord) -> bool:
    if actionable_label(event_label(event)):
        return True
    return event_action_class(event) in {
        "auth_action",
        "card_selection",
        "button_click",
        "input_entry",
        "navigation",
        "result_state",
        "tab_switch",
        "menu_open",
    }


def event_has_strong_label_signal(event: SessionEventRecord) -> bool:
    if actionable_label(event_label(event)):
        return True
    return event_action_class(event) in {
        "auth_action",
        "card_selection",
        "button_click",
        "input_entry",
        "navigation",
        "result_state",
        "tab_switch",
        "menu_open",
    }


def auth_related_event(event: SessionEventRecord) -> bool:
    if event_action_class(event) == "auth_action":
        return True
    if event_action_class(event) != "result_state":
        return False
    label = normalize_label(event_label(event))
    return any(token in label for token in ("account", "login", "google", "sign"))


def normalize_label(value: str) -> str:
    return " ".join(value.lower().split())
