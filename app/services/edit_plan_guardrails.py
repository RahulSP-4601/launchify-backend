from __future__ import annotations

from app.models.projects import EditPlanRecord, ProjectRecord
from app.services.camera_strategy import apply_camera_strategy
from app.services.scene_copy_refinement import apply_scene_copy_refinement
from app.services.scene_trimming import trim_edit_plan
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds, session_is_under_grounded


def finalized_edit_plan(project: ProjectRecord, edit_plan: EditPlanRecord) -> EditPlanRecord:
    if should_preserve_walkthrough_coverage(project):
        return apply_scene_copy_refinement(apply_camera_strategy(edit_plan))
    trimmed = trim_edit_plan(edit_plan)
    directed = apply_camera_strategy(trimmed)
    return apply_scene_copy_refinement(directed)


def should_preserve_walkthrough_coverage(project: ProjectRecord) -> bool:
    duration_seconds = recording_duration_seconds(project.recording_session, project.transcript)
    return session_is_under_grounded(project.recording_session, project.transcript) or guide_is_under_grounded(project.guide, duration_seconds)
