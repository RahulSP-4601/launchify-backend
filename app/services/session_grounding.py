from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, RecordingSessionRecord, SessionEventRecord
from app.services.extraction_artifacts import canonical_fact_map
from app.services.timing_sync import synced_highlights, synced_zooms

ACTION_EVENT_TYPES = frozenset({"click", "input", "navigation", "keypress", "focus"})


def apply_session_grounding(
    edit_plan: EditPlanRecord,
    recording_session: RecordingSessionRecord | None,
) -> EditPlanRecord:
    if recording_session is None or not recording_session.events:
        return edit_plan
    facts_by_scene = canonical_fact_map(recording_session)
    scenes = [grounded_scene(scene, recording_session.events, facts_by_scene.get(scene.scene_number)) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def grounded_scene(
    scene: EditPlanScene,
    events: list[SessionEventRecord],
    fact: dict[str, object] | None,
) -> EditPlanScene:
    event = primary_event_for_scene(scene, events)
    if event is None:
        return grounded_from_fact(scene, fact)
    action_time = round(event.timestamp, 2)
    grounded = scene.model_copy(
        update={
            "action_timestamp": action_time,
            "zooms": synced_zooms(scene, action_time),
            "highlights": synced_highlights(scene, action_time),
            "decision_summary": grounded_decision_summary(scene.decision_summary, event),
        }
    )
    return grounded_from_fact(grounded, fact)


def primary_event_for_scene(
    scene: EditPlanScene,
    events: list[SessionEventRecord],
) -> SessionEventRecord | None:
    candidates = [
        event for event in events
        if scene.start <= event.timestamp <= scene.end and event.type in ACTION_EVENT_TYPES
    ]
    if not candidates:
        return None
    target_time = scene.action_timestamp if scene.action_timestamp is not None else scene.end
    return max(candidates, key=lambda event: event_priority(event, target_time))

def event_priority(event: SessionEventRecord, target_time: float) -> tuple[float, float, float]:
    label = (event.target.label or event.target.text or "").strip()
    return (
        1.0 if event.type == "click" else 0.5,
        -abs(event.timestamp - target_time),
        float(len(label)),
    )


def grounded_decision_summary(summary: str, event: SessionEventRecord) -> str:
    label = (event.target.label or event.target.text or event.target.selector).strip()
    if not label:
        return summary
    return f"{summary} Grounded on {event.type} event near '{label}'."


def grounded_from_fact(
    scene: EditPlanScene,
    fact: dict[str, object] | None,
) -> EditPlanScene:
    if not fact:
        return scene
    label = string_field(fact, "canonical_label") or scene.on_screen_text
    screen_after = string_field(fact, "screen_after")
    purpose = grounded_purpose(scene.purpose, label, screen_after)
    highlights = [
        highlight.model_copy(update={"label": label or highlight.label, "ui_label": label or highlight.ui_label})
        for highlight in scene.highlights
    ]
    return scene.model_copy(
        update={
            "on_screen_text": label or scene.on_screen_text,
            "source_excerpt": label or scene.source_excerpt,
            "purpose": purpose,
            "decision_summary": grounded_fact_summary(scene.decision_summary, label, screen_after),
            "highlights": highlights,
        }
    )


def grounded_purpose(purpose: str, label: str, screen_after: str) -> str:
    if screen_after == "course_catalog":
        return "Guide the viewer into the course catalog after authentication completes."
    if screen_after == "difficulty_picker":
        return "Guide the viewer from the selected course into the level picker."
    if screen_after == "account_picker":
        return "Guide the viewer into the account chooser for the selected sign-in path."
    return purpose or f"Show the viewer {label}."


def grounded_fact_summary(summary: str, label: str, screen_after: str) -> str:
    if not label:
        return summary
    if screen_after:
        return f"{summary} Canonical step '{label}' leads into {screen_after.replace('_', ' ')}."
    return f"{summary} Canonical step '{label}' is preserved through downstream planning."


def string_field(payload: dict[str, object], key: str) -> str:
    value = payload.get(key, "")
    return value.strip() if isinstance(value, str) else ""
