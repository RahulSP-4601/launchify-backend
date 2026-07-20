from __future__ import annotations

from app.models.projects import TranscriptSegment, VisualSceneAnalysisRecord
from app.services.action_sequence_metrics import focused_excerpt
from app.services.inferred_recording_support import intent_tokens, transcript_window

LOCAL_TRANSCRIPT_PADDING_SECONDS = 2.2


def local_transcript_excerpt(
    transcript: list[TranscriptSegment],
    analysis: VisualSceneAnalysisRecord,
    index: int,
    source_excerpt: str,
) -> str:
    timestamp = analysis.frames[index].timestamp
    start = max(analysis.start, timestamp - LOCAL_TRANSCRIPT_PADDING_SECONDS)
    end = min(analysis.end, timestamp + LOCAL_TRANSCRIPT_PADDING_SECONDS)
    excerpt = transcript_window(transcript, start, end) or transcript_window(transcript, analysis.start, analysis.end)
    focused = focused_excerpt(excerpt, analysis, index)
    return grounded_scene_excerpt(focused, source_excerpt, analysis.summary)


def grounded_scene_excerpt(
    transcript_excerpt: str,
    source_excerpt: str,
    summary: str,
) -> str:
    clean_transcript = transcript_excerpt.strip()
    clean_source = source_excerpt.strip()
    if not clean_transcript:
        return clean_source or summary
    if not clean_source:
        return clean_transcript
    source_tokens = intent_tokens(clean_source, summary)
    if not source_tokens:
        return clean_transcript
    transcript_overlap = token_overlap_ratio(intent_tokens(clean_transcript), source_tokens)
    if transcript_overlap >= 0.18:
        return clean_transcript
    return clean_source


def token_overlap_ratio(
    observed_tokens: set[str],
    expected_tokens: set[str],
) -> float:
    if not observed_tokens or not expected_tokens:
        return 0.0
    return round(len(observed_tokens & expected_tokens) / max(len(expected_tokens), 1), 3)
