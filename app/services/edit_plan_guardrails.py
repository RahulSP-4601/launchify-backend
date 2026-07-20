from __future__ import annotations

from app.models.projects import EditPlanRecord, ProjectRecord
from app.services.scene_trimming import trim_edit_plan
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds, session_is_under_grounded


def finalized_edit_plan(project: ProjectRecord, edit_plan: EditPlanRecord) -> EditPlanRecord:
    if should_preserve_walkthrough_coverage(project):
        return edit_plan
    return trim_edit_plan(edit_plan)


def should_preserve_walkthrough_coverage(project: ProjectRecord) -> bool:
    duration_seconds = recording_duration_seconds(project.recording_session, project.transcript)
    return session_is_under_grounded(project.recording_session, project.transcript) or guide_is_under_grounded(project.guide, duration_seconds)
