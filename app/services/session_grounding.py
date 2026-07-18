from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, RecordingSessionRecord, SessionEventRecord
from app.services.timing_sync import synced_highlights, synced_zooms

ACTION_EVENT_TYPES = frozenset({"click", "input", "navigation", "keypress", "focus"})


def apply_session_grounding(
    edit_plan: EditPlanRecord,
    recording_session: RecordingSessionRecord | None,
) -> EditPlanRecord:
    if recording_session is None or not recording_session.events:
        return edit_plan
    scenes = [grounded_scene(scene, recording_session.events) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def grounded_scene(
    scene: EditPlanScene,
    events: list[SessionEventRecord],
) -> EditPlanScene:
    event = primary_event_for_scene(scene, events)
    if event is None:
        return scene
    action_time = round(event.timestamp, 2)
    return scene.model_copy(
        update={
            "action_timestamp": action_time,
            "zooms": synced_zooms(scene, action_time),
            "highlights": synced_highlights(scene, action_time),
            "decision_summary": grounded_decision_summary(scene.decision_summary, event),
        }
    )


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
