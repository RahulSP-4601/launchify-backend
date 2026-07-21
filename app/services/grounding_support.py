from __future__ import annotations

from typing import Callable

from app.models.projects import SessionEventRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label

SceneFamilyResolver = Callable[[SessionEventRecord, VisualSceneAnalysisRecord | None], str]
SceneNumberResolver = Callable[[SessionEventRecord], int]
TransitionDetector = Callable[[SessionEventRecord], bool]
GroundingScoreResolver = Callable[[SessionEventRecord], float]


def roll_up_supporting_evidence(
    validated: list[SessionEventRecord],
    all_events: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    *,
    event_scene_family: SceneFamilyResolver,
    scene_number: SceneNumberResolver,
    event_has_distinct_transition: TransitionDetector,
    candidate_grounding_score: GroundingScoreResolver,
) -> list[SessionEventRecord]:
    enriched: list[SessionEventRecord] = []
    for event in validated:
        cluster = support_cluster(
            event,
            all_events,
            analyses_by_scene,
            event_scene_family=event_scene_family,
            scene_number=scene_number,
            event_has_distinct_transition=event_has_distinct_transition,
            candidate_grounding_score=candidate_grounding_score,
        )
        if len(cluster) <= 1:
            enriched.append(event)
            continue
        enriched.append(event.model_copy(update={"metadata": {**event.metadata, **support_cluster_metadata(event, cluster, candidate_grounding_score)}}))
    return enriched


def support_cluster(
    anchor: SessionEventRecord,
    all_events: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    *,
    event_scene_family: SceneFamilyResolver,
    scene_number: SceneNumberResolver,
    event_has_distinct_transition: TransitionDetector,
    candidate_grounding_score: GroundingScoreResolver,
) -> list[SessionEventRecord]:
    anchor_family = event_scene_family(anchor, analyses_by_scene.get(scene_number(anchor)))
    cluster = [anchor]
    for candidate in all_events:
        if candidate is anchor:
            continue
        if abs(candidate.timestamp - anchor.timestamp) > support_gap_seconds(anchor_family):
            continue
        if not support_candidate(
            anchor,
            candidate,
            anchor_family,
            analyses_by_scene,
            event_scene_family=event_scene_family,
            scene_number=scene_number,
            event_has_distinct_transition=event_has_distinct_transition,
            candidate_grounding_score=candidate_grounding_score,
        ):
            continue
        cluster.append(candidate)
    return sorted(cluster, key=lambda item: item.timestamp)


def support_candidate(
    anchor: SessionEventRecord,
    candidate: SessionEventRecord,
    anchor_family: str,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    *,
    event_scene_family: SceneFamilyResolver,
    scene_number: SceneNumberResolver,
    event_has_distinct_transition: TransitionDetector,
    candidate_grounding_score: GroundingScoreResolver,
) -> bool:
    if not same_underlying_step(anchor, candidate):
        return False
    candidate_family = event_scene_family(candidate, analyses_by_scene.get(scene_number(candidate)))
    if candidate_family != anchor_family:
        return False
    if not related_scene_numbers(scene_number(anchor), scene_number(candidate)):
        return False
    if event_has_distinct_transition(candidate):
        candidate_after = candidate.metadata.get("screen_after", "").strip()
        anchor_after = anchor.metadata.get("screen_after", "").strip()
        if candidate_after and anchor_after and candidate_after != anchor_after:
            return False
    if candidate.metadata.get("canonical_label", "").strip():
        return True
    if candidate.type == "focus" and anchor_family in {"account_picker", "difficulty_picker", "course_catalog"}:
        return True
    return candidate_grounding_score(candidate) >= 0.48


def same_underlying_step(anchor: SessionEventRecord, candidate: SessionEventRecord) -> bool:
    anchor_label = normalize_label(anchor.metadata.get("canonical_label", "") or anchor.target.label or anchor.target.text)
    candidate_label = normalize_label(candidate.metadata.get("canonical_label", "") or candidate.target.label or candidate.target.text)
    anchor_after = anchor.metadata.get("screen_after", "").strip()
    candidate_after = candidate.metadata.get("screen_after", "").strip()
    if anchor.metadata.get("canonical_label", "").strip() and candidate.metadata.get("canonical_label", "").strip():
        return anchor_label == candidate_label
    if anchor_label and candidate_label and anchor_label == candidate_label:
        return True
    if anchor_after and candidate_after and anchor_after != candidate_after:
        return False
    if anchor.metadata.get("canonical_label", "").strip():
        return candidate_is_child_of_anchor(anchor, candidate, anchor_label)
    if candidate.metadata.get("canonical_label", "").strip():
        return candidate_is_child_of_anchor(candidate, anchor, candidate_label)
    return same_transcript_clause(anchor, candidate)


def candidate_is_child_of_anchor(
    canonical_event: SessionEventRecord,
    child_event: SessionEventRecord,
    canonical_label: str,
) -> bool:
    child_label = normalize_label(child_event.target.label or child_event.target.text)
    if not child_label:
        return False
    if canonical_label.startswith("pick your"):
        return any(token in child_label for token in ("jlpt", "level", "n5", "n4", "n3", "n2", "n1"))
    if canonical_label == "select a course":
        return any(token in child_label for token in ("course", "japanese", "english", "german", "spanish", "french", "open"))
    if canonical_label == "continue with google":
        return any(token in child_label for token in ("account", "google", "profile", "gmail", "continue"))
    if canonical_label == "google login":
        return any(token in child_label for token in ("google", "login", "log in", "sign in"))
    return False


def same_transcript_clause(anchor: SessionEventRecord, candidate: SessionEventRecord) -> bool:
    anchor_excerpt = normalize_label(anchor.metadata.get("transcript_excerpt", ""))
    candidate_excerpt = normalize_label(candidate.metadata.get("transcript_excerpt", ""))
    if not anchor_excerpt or not candidate_excerpt:
        return False
    return anchor_excerpt == candidate_excerpt


def related_scene_numbers(left: int, right: int) -> bool:
    if left <= 0 or right <= 0:
        return True
    return abs(left - right) <= 1


def support_gap_seconds(family: str) -> float:
    if family in {"account_picker", "course_catalog"}:
        return 12.0
    if family == "difficulty_picker":
        return 8.0
    return 6.0


def support_cluster_metadata(
    anchor: SessionEventRecord,
    cluster: list[SessionEventRecord],
    candidate_grounding_score: GroundingScoreResolver,
) -> dict[str, str]:
    support_windows = [event_window(item) for item in cluster]
    support_scenes = [scene_window(item) for item in cluster]
    support_scores = [candidate_grounding_score(item) for item in cluster]
    support_count = max(len(cluster) - 1, 0)
    support_window_start = min(start for start, _end in support_windows)
    support_window_end = max(end for _start, end in support_windows)
    support_scene_start = min(start for start, _end in support_scenes)
    support_scene_end = max(end for _start, end in support_scenes)
    average_support = sum(support_scores) / len(support_scores) if support_scores else candidate_grounding_score(anchor)
    boosted_score = max(candidate_grounding_score(anchor), min(average_support + min(support_count * 0.03, 0.09), 0.92))
    boosted_status = "strong" if boosted_score >= 0.72 else "supported" if boosted_score >= 0.56 else "weak"
    return {
        "grounding_support_count": str(support_count),
        "grounding_support_window_start": f"{support_window_start:.2f}",
        "grounding_support_window_end": f"{support_window_end:.2f}",
        "grounding_support_scene_start": f"{support_scene_start:.2f}",
        "grounding_support_scene_end": f"{support_scene_end:.2f}",
        "grounding_score": f"{boosted_score:.2f}",
        "grounding_status": boosted_status,
    }


def event_window(event: SessionEventRecord) -> tuple[float, float]:
    start = safe_float(event.metadata.get("grounding_window_start", "0"))
    end = safe_float(event.metadata.get("grounding_window_end", "0"))
    if end <= start:
        timestamp = max(event.timestamp, 0.0)
        return round(max(timestamp - 0.8, 0.0), 2), round(timestamp + 0.8, 2)
    return start, end


def scene_window(event: SessionEventRecord) -> tuple[float, float]:
    start = safe_float(event.metadata.get("grounding_scene_start", "0"))
    end = safe_float(event.metadata.get("grounding_scene_end", "0"))
    if end <= start:
        return event_window(event)
    return start, end


def safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
