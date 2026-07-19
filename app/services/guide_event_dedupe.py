from __future__ import annotations

from app.models.projects import SessionEventRecord


def synthetic_duplicate_index(
    events: list[SessionEventRecord],
    candidate: SessionEventRecord,
) -> int | None:
    if candidate.metadata.get("inferred") != "true":
        return None
    for index, existing in enumerate(events):
        if existing.metadata.get("inferred") != "true":
            continue
        if synthetic_events_match(existing, candidate):
            return index
    return None


def synthetic_events_match(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    same_scene = left.metadata.get("scene_number") == right.metadata.get("scene_number")
    same_type = left.type == right.type
    same_label = normalized_event_label(left) == normalized_event_label(right)
    same_excerpt = left.metadata.get("transcript_excerpt", "").strip()[:120] == right.metadata.get("transcript_excerpt", "").strip()[:120]
    close_in_time = abs(left.timestamp - right.timestamp) <= 2.2
    return same_type and close_in_time and (same_scene or same_label or same_excerpt)


def normalized_event_label(event: SessionEventRecord) -> str:
    label = (event.target.label or event.target.text or "").strip().lower()
    return " ".join(label.split())


def synthetic_event_score(event: SessionEventRecord) -> float:
    try:
        return float(event.metadata.get("score", "0"))
    except ValueError:
        return 0.0
