from __future__ import annotations

from typing import Sequence

from app.models.projects import SessionEventRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.walkthrough_guardrails import (
    auth_state_ratio,
    meaningful_event_count,
    low_confidence_ratio,
    recording_duration_seconds,
    repeated_transcript_ratio,
    session_is_under_grounded,
    timeline_coverage_ratio,
    weak_label_ratio,
)


def recording_diagnostics(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
    analyses: Sequence[VisualSceneAnalysisRecord],
) -> dict[str, str]:
    duration_seconds = recording_duration_seconds(None, transcript)
    fallback_scene_count = sum(1 for analysis in analyses if analysis.confidence <= 0.2 or not analysis.frame_diff_available)
    average_score = average_event_score(events)
    under_grounded = session_is_under_grounded(
        type("SessionSnapshot", (), {"events": list(events), "started_at": "0.0", "ended_at": f"{duration_seconds:.2f}"})(),
        transcript,
    )
    return {
        "source_duration_seconds": f"{duration_seconds:.2f}",
        "inferred_event_count": str(len(events)),
        "meaningful_event_count": str(meaningful_event_count(events)),
        "average_event_score": f"{average_score:.2f}",
        "timeline_coverage_ratio": f"{timeline_coverage_ratio(events, duration_seconds):.2f}",
        "weak_label_ratio": f"{weak_label_ratio(events):.2f}",
        "low_confidence_ratio": f"{low_confidence_ratio(events):.2f}",
        "auth_state_ratio": f"{auth_state_ratio(events):.2f}",
        "repeated_transcript_ratio": f"{repeated_transcript_ratio(events):.2f}",
        "visual_scene_count": str(len(analyses)),
        "fallback_scene_count": str(fallback_scene_count),
        "under_grounded": "true" if under_grounded else "false",
    }


def average_event_score(events: Sequence[SessionEventRecord]) -> float:
    if not events:
        return 0.0
    scores: list[float] = []
    for event in events:
        try:
            scores.append(max(float(event.metadata.get("score", "0")), 0.0))
        except (TypeError, ValueError):
            continue
    if not scores:
        return 0.0
    return sum(scores) / len(scores)
