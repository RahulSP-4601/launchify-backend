from __future__ import annotations

from app.models.projects import SessionEventRecord
from app.services.canonical_consistency import branch_family
from app.services.inferred_recording_support import normalize_label


def reconcile_canonical_graph_events(
    events: list[SessionEventRecord],
    graph_events: list[SessionEventRecord],
) -> list[SessionEventRecord]:
    if not graph_events:
        return events
    reconciled = events[:]
    for graph_event in graph_events:
        replacement_index = next(
            (
                index for index, event in enumerate(reconciled)
                if same_scene_event_cluster(event, graph_event)
            ),
            None,
        )
        if replacement_index is None:
            reconciled.append(graph_event)
            continue
        reconciled[replacement_index] = merged_canonical_event(reconciled[replacement_index], graph_event)
    return sorted(reconciled, key=lambda item: item.timestamp)


def same_scene_event_cluster(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    if left.metadata.get("scene_number") != right.metadata.get("scene_number"):
        return False
    if abs(left.timestamp - right.timestamp) > 1.6:
        return False
    if left.type != right.type and not compatible_transition_types(left.type, right.type):
        return False
    return labels_compatible(left, right)


def compatible_transition_types(left_type: str, right_type: str) -> bool:
    return {left_type, right_type} <= {"click", "focus", "navigation"}


def labels_compatible(left: SessionEventRecord, right: SessionEventRecord) -> bool:
    left_labels = candidate_labels(left)
    right_labels = candidate_labels(right)
    if not left_labels or not right_labels:
        return False
    if left_labels & right_labels:
        return True
    if auth_labels_compatible(left_labels, right_labels):
        return True
    return any(token_overlap(left_label, right_label) >= 0.75 for left_label in left_labels for right_label in right_labels)


def candidate_labels(event: SessionEventRecord) -> set[str]:
    labels = {
        normalize_label(event.target.label),
        normalize_label(event.target.text),
        normalize_label(event.metadata.get("canonical_label", "")),
        normalize_label(event.metadata.get("raw_target_label", "")),
        normalize_label(event.metadata.get("result_label", "")),
    }
    return {label for label in labels if label}


def token_overlap(left_label: str, right_label: str) -> float:
    left_tokens = set(left_label.split())
    right_tokens = set(right_label.split())
    if not left_tokens or not right_tokens:
        return 0.0
    shared = len(left_tokens & right_tokens)
    base = min(len(left_tokens), len(right_tokens))
    return shared / base if base else 0.0


def auth_labels_compatible(left_labels: set[str], right_labels: set[str]) -> bool:
    for left in left_labels:
        for right in right_labels:
            if branch_family(left) == "existing" and branch_family(right) == "existing":
                if "google" in left and "google" in right:
                    return True
    return False


def merged_canonical_event(
    existing: SessionEventRecord,
    graph_event: SessionEventRecord,
) -> SessionEventRecord:
    metadata = {**existing.metadata, **graph_event.metadata}
    target = existing.target
    canonical_label = graph_event.metadata.get("canonical_label", "").strip()
    if canonical_label:
        target = graph_event.target
    elif not existing.target.label.strip() and graph_event.target.label.strip():
        target = graph_event.target
    event_type = graph_event.type if canonical_label else existing.type
    timestamp = min(existing.timestamp, graph_event.timestamp) if canonical_label else existing.timestamp
    return existing.model_copy(update={"type": event_type, "timestamp": timestamp, "target": target, "metadata": metadata})
