from __future__ import annotations

from app.models.projects import RecordingSessionRecord


def artifact_payload(
    evidence_timeline: list[dict[str, object]],
    canonical_facts: list[dict[str, object]],
    event_grounding: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "evidence_timeline": evidence_timeline,
        "canonical_facts": canonical_facts,
        "event_grounding": event_grounding or [],
        "artifact_version": "v1",
    }


def canonical_fact_map(recording_session: RecordingSessionRecord | None) -> dict[int, dict[str, object]]:
    if recording_session is None:
        return {}
    facts = recording_session.extraction_artifacts.get("canonical_facts", [])
    if not isinstance(facts, list):
        return {}
    mapped: dict[int, dict[str, object]] = {}
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        scene_number = fact.get("scene_number")
        if isinstance(scene_number, int):
            mapped[scene_number] = fact
    return mapped
