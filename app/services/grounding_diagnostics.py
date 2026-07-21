from __future__ import annotations

from typing import Sequence

from app.models.projects import SessionEventRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.walkthrough_guardrails import (
    auth_state_ratio,
    grounding_evidence_events,
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
    average_grounding = average_grounding_score(events)
    evidence_events = grounding_evidence_events(events)
    grounded_ratio = grounded_event_ratio(events)
    result_ratio = average_metric(events, "grounding_result_evidence")
    branch_ratio = average_metric(events, "grounding_branch_evidence")
    under_grounded = session_is_under_grounded(
        type("SessionSnapshot", (), {"events": list(events), "started_at": "0.0", "ended_at": f"{duration_seconds:.2f}"})(),
        transcript,
        analyses,
    )
    return {
        "source_duration_seconds": f"{duration_seconds:.2f}",
        "inferred_event_count": str(len(events)),
        "meaningful_event_count": str(meaningful_event_count(evidence_events)),
        "average_event_score": f"{average_score:.2f}",
        "average_grounding_score": f"{average_grounding:.2f}",
        "grounded_event_ratio": f"{grounded_ratio:.2f}",
        "result_grounding_ratio": f"{result_ratio:.2f}",
        "branch_grounding_ratio": f"{branch_ratio:.2f}",
        "timeline_coverage_ratio": f"{timeline_coverage_ratio(evidence_events, duration_seconds, analyses):.2f}",
        "weak_label_ratio": f"{weak_label_ratio(evidence_events):.2f}",
        "low_confidence_ratio": f"{low_confidence_ratio(evidence_events):.2f}",
        "auth_state_ratio": f"{auth_state_ratio(evidence_events):.2f}",
        "repeated_transcript_ratio": f"{repeated_transcript_ratio(evidence_events):.2f}",
        "visual_scene_count": str(len(analyses)),
        "fallback_scene_count": str(fallback_scene_count),
        "transcript_fallback_event_count": str(max(len(events) - len(evidence_events), 0)),
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


def average_grounding_score(events: Sequence[SessionEventRecord]) -> float:
    return average_metric(events, "grounding_score")


def grounded_event_ratio(events: Sequence[SessionEventRecord]) -> float:
    if not events:
        return 0.0
    grounded = sum(1 for event in events if safe_float(event.metadata.get("grounding_score", "0")) >= 0.56)
    return grounded / len(events)


def average_metric(events: Sequence[SessionEventRecord], key: str) -> float:
    if not events:
        return 0.0
    values = [safe_float(event.metadata.get(key, "0")) for event in events]
    return sum(values) / len(values) if values else 0.0


def safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
