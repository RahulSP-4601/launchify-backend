from __future__ import annotations

from typing import Sequence

from app.models.projects import SessionEventRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.walkthrough_guardrails import recording_duration_seconds, sparse_action_count


def recording_diagnostics(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
    analyses: Sequence[VisualSceneAnalysisRecord],
) -> dict[str, str]:
    duration_seconds = recording_duration_seconds(None, transcript)
    fallback_scene_count = sum(1 for analysis in analyses if analysis.confidence <= 0.2 or not analysis.frame_diff_available)
    under_grounded = sparse_action_count(len(events), duration_seconds)
    return {
        "source_duration_seconds": f"{duration_seconds:.2f}",
        "inferred_event_count": str(len(events)),
        "visual_scene_count": str(len(analyses)),
        "fallback_scene_count": str(fallback_scene_count),
        "under_grounded": "true" if under_grounded else "false",
    }
