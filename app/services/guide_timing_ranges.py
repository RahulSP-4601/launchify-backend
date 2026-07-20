from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from app.models.projects import RecordingSessionRecord, SessionEventRecord
from app.services.action_classifier import event_action_class
from app.services.event_grounding import normalize_event_timestamp

AUTH_STEP_WINDOW_SECONDS = 3.8
AUTH_LABEL_TOKENS = {"google", "login", "log", "sign", "signup", "account", "existing", "create"}
MIN_STEP_DURATION_SECONDS = 0.8


class EventClusterLike(Protocol):
    start: float
    end: float
    transcript_excerpt: str
    event: SessionEventRecord


def contextual_step_ranges(
    clusters: Sequence[object],
    session: RecordingSessionRecord | None,
) -> list[tuple[float, float]]:
    if not clusters:
        return []
    typed_clusters = [cast(EventClusterLike, cluster) for cluster in clusters]
    source_start, source_end = session_bounds(session, typed_clusters)
    anchors = [cluster_anchor(cluster) for cluster in typed_clusters]
    boundaries = [source_start]
    for index in range(len(anchors) - 1):
        boundaries.append(round((anchors[index] + anchors[index + 1]) / 2, 2))
    boundaries.append(source_end)
    ranges: list[tuple[float, float]] = []
    previous_end = source_start
    for index, cluster in enumerate(typed_clusters):
        step_start = max(previous_end, min(boundaries[index], cluster.start))
        step_end = bounded_step_end(cluster, step_start, boundaries[index + 1], source_end)
        ranges.append((round(step_start, 2), round(max(step_end, step_start + MIN_STEP_DURATION_SECONDS), 2)))
        previous_end = ranges[-1][1]
    return ranges


def bounded_step_end(cluster: EventClusterLike, step_start: float, boundary_end: float, source_end: float) -> float:
    if is_distinct_auth_cluster(cluster):
        local_end = min(source_end, max(cluster.end + 0.85, step_start + MIN_STEP_DURATION_SECONDS))
        return min(local_end, step_start + AUTH_STEP_WINDOW_SECONDS, max(boundary_end - 0.18, step_start + MIN_STEP_DURATION_SECONDS))
    return min(max(boundary_end, step_start + MIN_STEP_DURATION_SECONDS), source_end)


def is_distinct_auth_cluster(cluster: EventClusterLike) -> bool:
    if event_action_class(cluster.event) != "auth_action" and cluster.event.type != "click":
        return False
    label = f"{cluster.event.target.label} {cluster.transcript_excerpt}".lower()
    return any(token in label for token in AUTH_LABEL_TOKENS)


def session_bounds(
    session: RecordingSessionRecord | None,
    clusters: Sequence[EventClusterLike],
) -> tuple[float, float]:
    source_start = parse_session_time(session.started_at) if session is not None else 0.0
    source_end = parse_session_time(session.ended_at) if session is not None else 0.0
    fallback_end = max(cluster.end for cluster in clusters)
    if source_end <= source_start:
        source_end = fallback_end
    source_start = min(source_start, min(cluster.start for cluster in clusters))
    return round(max(source_start, 0.0), 2), round(max(source_end, fallback_end), 2)


def cluster_anchor(cluster: EventClusterLike) -> float:
    timestamp = normalize_event_timestamp(cluster.event.timestamp)
    return round(min(max(timestamp, cluster.start), cluster.end), 2)


def parse_session_time(value: str) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0
