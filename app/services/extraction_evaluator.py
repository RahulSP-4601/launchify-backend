from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.services.inferred_recording_support import normalize_label


@dataclass(frozen=True)
class ExtractionMetric:
    name: str
    score: float
    detail: str


@dataclass(frozen=True)
class ExtractionEvaluation:
    overall_score: int
    verdict: str
    metrics: list[ExtractionMetric]
    findings: list[str]


def evaluate_probe_output(
    output_dir: Path,
    expected: dict[str, object],
) -> ExtractionEvaluation:
    recording = load_json(output_dir / "recording_session.json")
    launch_script = load_json(output_dir / "launch_script.json")
    summary = load_json(output_dir / "summary.json")
    actual_events = event_labels(recording)
    actual_script = script_labels(launch_script)
    actual_facts = canonical_fact_labels(recording)
    expected_events = string_list(expected.get("expected_events"))
    expected_script = string_list(expected.get("expected_canonical_script"))
    expected_states = string_list(expected.get("expected_screen_after"))
    metrics = [
        metric("event_sequence", sequence_score(actual_events, expected_events), sequence_detail(actual_events, expected_events)),
        metric("canonical_script", sequence_score(actual_script, expected_script), sequence_detail(actual_script, expected_script)),
        metric("canonical_facts", sequence_score(actual_facts, expected_script), sequence_detail(actual_facts, expected_script)),
        metric("screen_transitions", state_score(recording, expected_states), state_detail(recording, expected_states)),
        metric("grounding_health", grounding_score(summary, recording), grounding_detail(summary, recording)),
    ]
    findings = evaluation_findings(metrics, actual_events, actual_script, actual_facts)
    overall = round(sum(item.score for item in metrics) / max(len(metrics), 1) * 100)
    return ExtractionEvaluation(
        overall_score=overall,
        verdict=evaluation_verdict(overall),
        metrics=metrics,
        findings=findings,
    )


def load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def event_labels(recording: dict[str, object]) -> list[str]:
    events = object_list(recording.get("events"))
    return [best_label(event) for event in events if best_label(event)]


def script_labels(launch_script: dict[str, object]) -> list[str]:
    scenes = object_list(launch_script.get("scenes"))
    labels: list[str] = []
    for scene in scenes:
        label = string_value(scene.get("on_screen_text"))
        if label:
            labels.append(label)
    return labels


def canonical_fact_labels(recording: dict[str, object]) -> list[str]:
    artifacts = object_dict(recording.get("extraction_artifacts"))
    facts = object_list(artifacts.get("canonical_facts"))
    labels: list[str] = []
    for fact in facts:
        label = string_value(fact.get("canonical_label"))
        if label:
            labels.append(label)
    return labels


def state_score(recording: dict[str, object], expected_states: list[str]) -> float:
    if not expected_states:
        return 1.0
    actual = actual_states(recording)
    if not actual:
        return 0.0
    filtered = meaningful_state_chain(actual)
    matches = subsequence_matches(filtered, expected_states)
    return round(matches / max(len(expected_states), 1), 2)


def actual_states(recording: dict[str, object]) -> list[str]:
    states: list[str] = []
    for event in object_list(recording.get("events")):
        metadata = object_dict(event.get("metadata"))
        state = string_value(metadata.get("screen_after"))
        if state:
            states.append(state)
    return states


def state_detail(recording: dict[str, object], expected_states: list[str]) -> str:
    actual = actual_states(recording)
    return f"actual={actual} filtered={meaningful_state_chain(actual)} expected={expected_states}"


def grounding_score(
    summary: dict[str, object],
    recording: dict[str, object],
) -> float:
    diagnostics = object_dict(summary.get("grounding_diagnostics")) or object_dict(recording.get("grounding_diagnostics"))
    coverage = float_value(diagnostics.get("timeline_coverage_ratio"))
    average_grounding = float_value(diagnostics.get("average_grounding_score"))
    grounded_ratio = float_value(diagnostics.get("grounded_event_ratio"))
    result_ratio = float_value(diagnostics.get("result_grounding_ratio"))
    branch_ratio = float_value(diagnostics.get("branch_grounding_ratio"))
    under_grounded = string_value(diagnostics.get("under_grounded")) == "true"
    base = (
        (coverage * 0.32)
        + (average_grounding * 0.28)
        + (grounded_ratio * 0.18)
        + (result_ratio * 0.12)
        + (branch_ratio * 0.10)
    )
    if under_grounded:
        base -= 0.2
    return round(max(min(base, 1.0), 0.0), 2)


def grounding_detail(
    summary: dict[str, object],
    recording: dict[str, object],
) -> str:
    diagnostics = object_dict(summary.get("grounding_diagnostics")) or object_dict(recording.get("grounding_diagnostics"))
    return (
        f"coverage={string_value(diagnostics.get('timeline_coverage_ratio')) or '0'} "
        f"avg_grounding={string_value(diagnostics.get('average_grounding_score')) or '0'} "
        f"grounded_ratio={string_value(diagnostics.get('grounded_event_ratio')) or '0'} "
        f"under_grounded={string_value(diagnostics.get('under_grounded')) or 'false'}"
    )


def sequence_score(actual: list[str], expected: list[str]) -> float:
    if not expected:
        return 1.0 if actual else 0.0
    matches = subsequence_matches(actual, expected)
    missing_penalty = max(len(expected) - matches, 0)
    extra_penalty = max(len(actual) - matches, 0)
    denominator = len(expected) + extra_penalty
    if denominator <= 0:
        return 0.0
    score = (matches - (missing_penalty * 0.35)) / denominator
    return round(max(min(score, 1.0), 0.0), 2)


def aligned_matches(actual: list[str], expected: list[str]) -> int:
    count = 0
    for left, right in zip(normalized(actual), normalized(expected), strict=False):
        if left == right:
            count += 1
    return count


def sequence_detail(actual: list[str], expected: list[str]) -> str:
    return f"actual={actual} expected={expected}"


def meaningful_state_chain(states: list[str]) -> list[str]:
    filtered: list[str] = []
    for state in states:
        if state in {"generic", "unknown", "auth_provider"}:
            continue
        if filtered and filtered[-1] == state:
            continue
        filtered.append(state)
    return filtered


def subsequence_matches(actual: list[str], expected: list[str]) -> int:
    actual_norm = normalized(actual)
    expected_norm = normalized(expected)
    index = 0
    matches = 0
    for value in actual_norm:
        if index >= len(expected_norm):
            break
        if value == expected_norm[index]:
            matches += 1
            index += 1
    return matches


def evaluation_findings(
    metrics: list[ExtractionMetric],
    actual_events: list[str],
    actual_script: list[str],
    actual_facts: list[str],
) -> list[str]:
    findings: list[str] = []
    weak = [metric for metric in metrics if metric.score < 0.75]
    for item in weak:
        findings.append(f"{item.name}: {item.detail}")
    if normalized(actual_script) != normalized(actual_facts):
        findings.append("canonical_script_mismatch: launch_script labels diverge from canonical fact labels.")
    if not actual_events:
        findings.append("missing_events: recording_session.json has no labeled events.")
    return findings


def evaluation_verdict(overall: int) -> str:
    if overall >= 90:
        return "Production-ready candidate"
    if overall >= 78:
        return "Promising but verify edge cases"
    return "Needs more extraction tuning"


def metric(name: str, score: float, detail: str) -> ExtractionMetric:
    return ExtractionMetric(name=name, score=score, detail=detail)


def best_label(event: dict[str, object]) -> str:
    target = object_dict(event.get("target"))
    metadata = object_dict(event.get("metadata"))
    return (
        string_value(metadata.get("canonical_label"))
        or string_value(target.get("label"))
        or string_value(metadata.get("result_label"))
        or string_value(target.get("text"))
    )


def normalized(values: list[str]) -> list[str]:
    return [normalize_label(value) for value in values if value.strip()]


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def object_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def object_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def float_value(value: object) -> float:
    if isinstance(value, (float, int)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0
