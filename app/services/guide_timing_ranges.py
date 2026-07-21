from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, cast

from app.models.projects import RecordingSessionRecord, SessionEventRecord
from app.services.action_classifier import event_action_class
from app.services.event_grounding import normalize_event_timestamp

AUTH_STEP_WINDOW_SECONDS = 3.8
AUTH_LABEL_TOKENS = {"google", "login", "log", "sign", "signup", "account", "existing", "create"}
MIN_STEP_DURATION_SECONDS = 0.8
MAX_CONTEXT_WINDOW_SECONDS = 11.6
MAX_CARD_SELECTION_WINDOW_SECONDS = 9.2
MAX_GENERIC_WINDOW_SECONDS = 7.4
FLOW_HANDOFF_GAP_SECONDS = 0.42


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
    ranges: list[tuple[float, float]] = []
    previous_end = source_start
    for index, cluster in enumerate(typed_clusters):
        next_cluster = typed_clusters[index + 1] if index + 1 < len(typed_clusters) else None
        next_anchor = anchors[index + 1] if index + 1 < len(anchors) else source_end
        step_start = max(previous_end, cluster.start)
        step_end = bounded_step_end(cluster, next_cluster, step_start, next_anchor, source_end)
        ranges.append((round(step_start, 2), round(max(step_end, step_start + MIN_STEP_DURATION_SECONDS), 2)))
        previous_end = ranges[-1][1]
    return ranges


def bounded_step_end(
    cluster: EventClusterLike,
    next_cluster: EventClusterLike | None,
    step_start: float,
    next_anchor: float,
    source_end: float,
) -> float:
    boundary_cap = min(source_end, max(next_anchor - 0.18, step_start + MIN_STEP_DURATION_SECONDS))
    local_end = max(cluster.end, cluster_anchor(cluster) + settled_hold_seconds(cluster), step_start + MIN_STEP_DURATION_SECONDS)
    contextual_end = step_start + contextual_window_seconds(cluster, next_cluster, boundary_cap - step_start)
    bridge_end = flow_bridge_end(cluster, next_cluster, next_anchor, source_end)
    target_end = max(local_end, contextual_end)
    if bridge_end is not None:
        target_end = max(target_end, bridge_end)
    if is_distinct_auth_cluster(cluster):
        return min(target_end, step_start + max_auth_window_seconds(cluster, next_cluster), boundary_cap)
    if event_action_class(cluster.event) == "card_selection":
        return min(target_end, step_start + MAX_CARD_SELECTION_WINDOW_SECONDS, boundary_cap)
    return min(target_end, step_start + MAX_GENERIC_WINDOW_SECONDS, boundary_cap)


def is_distinct_auth_cluster(cluster: EventClusterLike) -> bool:
    if event_action_class(cluster.event) != "auth_action" and cluster.event.type != "click":
        return False
    label = f"{cluster.event.target.label} {cluster.transcript_excerpt}".lower()
    return any(token in label for token in AUTH_LABEL_TOKENS)


def session_bounds(
    session: RecordingSessionRecord | None,
    clusters: Sequence[EventClusterLike],
) -> tuple[float, float]:
    source_start = min(cluster.start for cluster in clusters)
    source_end = parse_session_time(session.ended_at) if session is not None else 0.0
    fallback_end = max(cluster.end for cluster in clusters)
    if source_end <= source_start:
        source_end = fallback_end
    return round(max(source_start, 0.0), 2), round(max(source_end, fallback_end), 2)


def cluster_anchor(cluster: EventClusterLike) -> float:
    timestamp = normalize_event_timestamp(cluster.event.timestamp)
    return round(min(max(timestamp, cluster.start), cluster.end), 2)


def settled_hold_seconds(cluster: EventClusterLike) -> float:
    action_class = event_action_class(cluster.event)
    if action_class == "card_selection":
        return 0.75
    if action_class == "auth_action":
        return 0.85
    if cluster.event.type == "focus":
        return 0.9
    return 0.55


def contextual_window_seconds(
    cluster: EventClusterLike,
    next_cluster: EventClusterLike | None,
    available_seconds: float,
) -> float:
    action_class = event_action_class(cluster.event)
    words = transcript_word_count(cluster.transcript_excerpt)
    base = 2.35
    if action_class == "auth_action":
        base = 3.35
    elif action_class == "card_selection":
        base = 2.95
    elif cluster.event.type == "focus":
        base = 2.7
    narration_bonus = min(max(words - 10, 0) / 10.0, 3.0) * 0.55
    gap_bonus = 0.0
    if available_seconds >= 6.0:
        gap_bonus += 0.9
    if available_seconds >= 10.0:
        gap_bonus += 0.7
    if is_flow_continuation(cluster, next_cluster):
        gap_bonus += 1.3
    if preserves_result_reveal(cluster, next_cluster):
        gap_bonus += 0.75
    desired = base + narration_bonus + gap_bonus
    if action_class == "auth_action":
        return round(min(max(desired, AUTH_STEP_WINDOW_SECONDS), available_seconds, MAX_CONTEXT_WINDOW_SECONDS), 2)
    if action_class == "card_selection":
        return round(min(max(desired, 3.4), available_seconds, MAX_CARD_SELECTION_WINDOW_SECONDS), 2)
    return round(min(max(desired, 2.4), available_seconds, MAX_GENERIC_WINDOW_SECONDS), 2)


def max_auth_window_seconds(cluster: EventClusterLike, next_cluster: EventClusterLike | None) -> float:
    words = transcript_word_count(cluster.transcript_excerpt)
    density_bonus = min(max(words - 14, 0) / 12.0, 2.0) * 0.45
    continuity_bonus = 2.2 if is_flow_continuation(cluster, next_cluster) else 0.0
    return round(min(MAX_CONTEXT_WINDOW_SECONDS, AUTH_STEP_WINDOW_SECONDS + density_bonus + continuity_bonus), 2)


def flow_bridge_end(
    cluster: EventClusterLike,
    next_cluster: EventClusterLike | None,
    next_anchor: float,
    source_end: float,
) -> float | None:
    if next_cluster is None:
        return None
    if not (is_flow_continuation(cluster, next_cluster) or preserves_result_reveal(cluster, next_cluster)):
        return None
    handoff = max(cluster.end, next_cluster.start - FLOW_HANDOFF_GAP_SECONDS)
    return round(min(source_end, max(handoff, cluster.start + MIN_STEP_DURATION_SECONDS)), 2)


def is_flow_continuation(
    cluster: EventClusterLike,
    next_cluster: EventClusterLike | None,
) -> bool:
    if next_cluster is None:
        return False
    current_action = event_action_class(cluster.event)
    next_action = event_action_class(next_cluster.event)
    if current_action == "auth_action" and next_action == "auth_action":
        return True
    if current_action == "card_selection" and next_action in {"button_click", "focus", "generic_action"}:
        return True
    return cluster.event.type == "click" and next_cluster.event.type == "focus" and next_cluster.start - cluster.end <= 8.0


def preserves_result_reveal(
    cluster: EventClusterLike,
    next_cluster: EventClusterLike | None,
) -> bool:
    if next_cluster is None:
        return False
    current_action = event_action_class(cluster.event)
    next_action = event_action_class(next_cluster.event)
    return current_action in {"auth_action", "card_selection"} and next_action != "auth_action"


def transcript_word_count(text: str) -> int:
    return len([word for word in text.split() if word.strip(".,")])


def parse_session_time(value: str) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0
