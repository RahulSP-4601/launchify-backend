from __future__ import annotations

from typing import Sequence

from app.models.projects import GuideRecord, RecordingSessionRecord, SessionEventRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.action_classifier import event_action_class
from app.services.canonical_consistency import branch_family, event_branch_family
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
    analyses: Sequence[VisualSceneAnalysisRecord] | None = None,
) -> bool:
    if recording_session is None or not recording_session.events:
        return False
    duration_seconds = recording_duration_seconds(recording_session, transcript)
    if duration_seconds < LONG_WALKTHROUGH_SECONDS:
        return False
    evidence_events = grounding_evidence_events(recording_session.events)
    if not evidence_events:
        return True
    if coherent_extraction_flow(evidence_events, duration_seconds):
        return False
    meaningful_count = meaningful_event_count(evidence_events)
    if meaningful_count < minimum_action_count(duration_seconds):
        return True
    coverage_ratio = timeline_coverage_ratio(evidence_events, duration_seconds, analyses)
    if coverage_ratio < MIN_TIMELINE_COVERAGE_RATIO:
        return True
    if weak_label_ratio(evidence_events) > MAX_WEAK_LABEL_RATIO:
        return True
    if low_confidence_ratio(evidence_events) > MAX_LOW_CONFIDENCE_RATIO:
        return True
    if auth_state_ratio(evidence_events) > MAX_AUTH_STATE_RATIO and distinct_screen_after_count(evidence_events) < 3:
        return True
    return repeated_transcript_ratio(evidence_events) >= MAX_REPEATED_TRANSCRIPT_RATIO


def grounding_evidence_events(events: Sequence[SessionEventRecord]) -> list[SessionEventRecord]:
    return [event for event in events if event.metadata.get("grounding_source") != "transcript_fallback"]


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

def timeline_coverage_ratio(
    events: Sequence[SessionEventRecord],
    duration_seconds: float,
    analyses: Sequence[VisualSceneAnalysisRecord] | None = None,
) -> float:
    if not events or duration_seconds <= 0:
        return 0.0
    spans = grounded_scene_spans(events) or grounded_event_spans(events)
    if spans:
        covered = covered_seconds(spans)
        denominator = coverage_denominator(spans, duration_seconds, events, analyses)
        return round(min(covered / denominator, 1.0), 3)
    timestamps = sorted(normalized_event_timestamp(event) for event in events)
    if len(timestamps) == 1:
        return 0.0
    covered = max(timestamps[-1] - timestamps[0], 0.0)
    return round(min(covered / duration_seconds, 1.0), 3)


def coverage_denominator(
    spans: list[tuple[float, float]],
    duration_seconds: float,
    events: Sequence[SessionEventRecord],
    analyses: Sequence[VisualSceneAnalysisRecord] | None,
) -> float:
    analysis_spans = relevant_analysis_spans(events, analyses)
    if not analysis_spans:
        analysis_spans = analyzed_scene_spans(analyses)
    if analysis_spans:
        analyzed_duration = covered_seconds(analysis_spans)
        overlap = covered_seconds(intersect_spans(spans, analysis_spans))
        if overlap >= min(covered_seconds(spans) * 0.7, analyzed_duration):
            return max(min(analyzed_duration, duration_seconds), 0.01)
    meaningful_span = max(spans[-1][1] - spans[0][0], 0.0)
    return min(duration_seconds, max(meaningful_span + idle_gap_allowance(spans), covered_seconds(spans), 0.01))


def analyzed_scene_spans(
    analyses: Sequence[VisualSceneAnalysisRecord] | None,
) -> list[tuple[float, float]]:
    if not analyses:
        return []
    spans = [
        (round(max(analysis.start, 0.0), 2), round(max(analysis.end, 0.0), 2))
        for analysis in analyses
        if analysis.end > analysis.start and (analysis.confidence >= 0.3 or analysis.frame_diff_available)
    ]
    return merge_spans(sorted(spans))


def relevant_analysis_spans(
    events: Sequence[SessionEventRecord],
    analyses: Sequence[VisualSceneAnalysisRecord] | None,
) -> list[tuple[float, float]]:
    if not analyses or not events:
        return []
    analyses_by_scene = {analysis.scene_number: analysis for analysis in analyses}
    event_scenes = sorted({scene_number(event) for event in events if scene_number(event) > 0})
    if not event_scenes:
        return []
    relevant_scenes = set(event_scenes)
    selected_branch = dominant_event_branch(events)
    ordered_events = sorted(
        (event for event in events if scene_number(event) > 0),
        key=lambda item: (scene_number(item), item.timestamp),
    )
    for left_event, right_event in zip(ordered_events, ordered_events[1:], strict=False):
        left_scene = scene_number(left_event)
        right_scene = scene_number(right_event)
        if right_scene - left_scene <= 1:
            continue
        for candidate_scene in range(left_scene + 1, right_scene):
            analysis = analyses_by_scene.get(candidate_scene)
            if analysis is None:
                continue
            if bridge_scene_relevant(analysis, left_event, right_event, selected_branch):
                relevant_scenes.add(candidate_scene)
    spans = [
        (round(max(analysis.start, 0.0), 2), round(max(analysis.end, 0.0), 2))
        for scene_id, analysis in analyses_by_scene.items()
        if scene_id in relevant_scenes and analysis.end > analysis.start
    ]
    return merge_spans(sorted(spans))


def dominant_event_branch(events: Sequence[SessionEventRecord]) -> str:
    counts: dict[str, int] = {}
    for event in events:
        branch = event_branch_family(event)
        if branch == "generic":
            continue
        counts[branch] = counts.get(branch, 0) + 1
    if not counts:
        return "generic"
    return max(counts.items(), key=lambda item: item[1])[0]


def bridge_scene_relevant(
    analysis: VisualSceneAnalysisRecord,
    left_event: SessionEventRecord,
    right_event: SessionEventRecord,
    selected_branch: str,
) -> bool:
    scene_state = analysis_scene_state(analysis)
    if scene_state in {screen_state(left_event, "screen_after"), screen_state(right_event, "screen_before")}:
        return True
    if scene_state == screen_state(right_event, "screen_after") and scene_state == "account_picker":
        return True
    if branch_conflicts_with_selected_flow(analysis, selected_branch):
        return False
    return False


def branch_conflicts_with_selected_flow(
    analysis: VisualSceneAnalysisRecord,
    selected_branch: str,
) -> bool:
    if selected_branch == "generic":
        return False
    labels_text = " ".join(label.lower().strip() for label in analysis.visible_labels if label.strip())
    scene_branch = branch_family(labels_text)
    return scene_branch not in {"generic", selected_branch}


def analysis_scene_state(analysis: VisualSceneAnalysisRecord) -> str:
    labels = " ".join(label.lower().strip() for label in analysis.visible_labels if label.strip())
    if "choose an account" in labels or "account" in labels:
        return "account_picker"
    if "select a course" in labels or "open course" in labels or any(
        token in labels for token in ("japanese", "english", "german", "spanish", "french")
    ):
        return "course_catalog"
    if any(token in labels for token in ("pick your", "before you start", "level")):
        return "difficulty_picker"
    if any(token in labels for token in ("google login", "log in with google", "sign up with google", "continue with google")):
        return "auth_provider"
    return "generic"


def screen_state(event: SessionEventRecord, key: str) -> str:
    return (event.metadata.get(key, "") or "").strip()


def scene_number(event: SessionEventRecord) -> int:
    try:
        return int(event.metadata.get("scene_number", "0") or 0)
    except (TypeError, ValueError):
        return 0


def intersect_spans(
    left: list[tuple[float, float]],
    right: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    intersections: list[tuple[float, float]] = []
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        left_start, left_end = left[left_index]
        right_start, right_end = right[right_index]
        start = max(left_start, right_start)
        end = min(left_end, right_end)
        if end > start:
            intersections.append((round(start, 2), round(end, 2)))
        if left_end <= right_end:
            left_index += 1
        else:
            right_index += 1
    return intersections


def grounded_event_spans(events: Sequence[SessionEventRecord]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for event in events:
        try:
            start = max(float(event.metadata.get("grounding_window_start", "")), 0.0)
            end = max(float(event.metadata.get("grounding_window_end", "")), 0.0)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        spans.append((round(start, 2), round(end, 2)))
    return merge_spans(sorted(spans))


def grounded_scene_spans(events: Sequence[SessionEventRecord]) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for event in events:
        try:
            start = max(float(event.metadata.get("grounding_scene_start", "")), 0.0)
            end = max(float(event.metadata.get("grounding_scene_end", "")), 0.0)
            score = max(float(event.metadata.get("grounding_score", "0")), 0.0)
        except (TypeError, ValueError):
            continue
        if score < 0.56 or end <= start:
            continue
        spans.append((round(start, 2), round(end, 2)))
    return merge_spans(sorted(spans))


def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not spans:
        return []
    merged: list[tuple[float, float]] = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1.2:
            merged[-1] = (last_start, max(last_end, end))
            continue
        merged.append((start, end))
    return merged


def covered_seconds(spans: list[tuple[float, float]]) -> float:
    return round(sum(max(end - start, 0.0) for start, end in spans), 3)


def idle_gap_allowance(spans: list[tuple[float, float]]) -> float:
    allowance = 0.0
    for (_, previous_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        gap = max(next_start - previous_end, 0.0)
        allowance += min(gap, 1.25)
    return round(allowance, 3)


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


def distinct_screen_after_count(events: Sequence[SessionEventRecord]) -> int:
    states = {
        event.metadata.get("screen_after", "").strip()
        for event in events
        if event.metadata.get("screen_after", "").strip() and event.metadata.get("screen_after", "").strip() not in {"generic", "unknown"}
    }
    return len(states)


def coherent_extraction_flow(
    events: Sequence[SessionEventRecord],
    duration_seconds: float,
) -> bool:
    if duration_seconds < LONG_WALKTHROUGH_SECONDS or len(events) < 4:
        return False
    labels = distinct_canonical_labels(events)
    transitions = distinct_screen_after_count(events)
    if len(labels) < minimum_action_count(duration_seconds):
        return False
    if transitions < 3:
        return False
    if duplicate_canonical_ratio(events) > 0.26:
        return False
    return ordered_flow_score(events) >= 0.74


def distinct_canonical_labels(events: Sequence[SessionEventRecord]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for event in events:
        label = canonical_event_label(event)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def duplicate_canonical_ratio(events: Sequence[SessionEventRecord]) -> float:
    labels = [canonical_event_label(event) for event in events if canonical_event_label(event)]
    if not labels:
        return 1.0
    unique = len(set(labels))
    return round(max(len(labels) - unique, 0) / len(labels), 3)


def ordered_flow_score(events: Sequence[SessionEventRecord]) -> float:
    labels = [canonical_event_label(event) for event in events if canonical_event_label(event)]
    if not labels:
        return 0.0
    score = 0.0
    if labels == sorted(labels, key=flow_rank):
        score += 0.4
    if any("continue with google" == label for label in labels):
        score += 0.12
    if any("select a course" == label for label in labels):
        score += 0.12
    if any(label.startswith("pick your") for label in labels):
        score += 0.12
    score += min(distinct_screen_after_count(events) * 0.08, 0.24)
    return round(min(score, 1.0), 3)


def canonical_event_label(event: SessionEventRecord) -> str:
    label = (
        event.metadata.get("canonical_label", "").strip()
        or event.metadata.get("result_label", "").strip()
        or event_label(event).strip()
    )
    return normalize_label(label)


def flow_rank(label: str) -> int:
    if label == "google login":
        return 1
    if label == "continue with google":
        return 2
    if label == "select a course":
        return 3
    if label.startswith("pick your"):
        return 4
    return 5


def normalize_label(value: str) -> str:
    return " ".join(value.lower().split())
