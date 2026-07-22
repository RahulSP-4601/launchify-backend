from __future__ import annotations

from app.models.project_editor import ProjectEditorState
from app.models.projects import ProjectRecord

TIMING_EPSILON = 0.05
MIN_SCENE_SECONDS = 0.5
MIN_CAPTION_SECONDS = 0.2


def validate_project_editor_state(project: ProjectRecord, state: ProjectEditorState) -> None:
    scene_ids = validate_scenes(project, state)
    validate_selected_scene(scene_ids, state.selected_scene_id)
    validate_captions(project_duration_limit(project), state, scene_ids)


def validate_scenes(project: ProjectRecord, state: ProjectEditorState) -> set[str]:
    if not state.scenes:
        raise ValueError("At least one scene is required in the editor.")
    duration_limit = project_duration_limit(project)
    scene_ids: set[str] = set()
    previous_end = 0.0
    for scene in sorted(state.scenes, key=lambda item: item.start):
        if scene.id in scene_ids:
            raise ValueError("Each editor scene must have a unique id.")
        validate_scene_timing(scene.start, scene.end, duration_limit, previous_end)
        scene_ids.add(scene.id)
        previous_end = scene.end
    return scene_ids


def validate_selected_scene(scene_ids: set[str], selected_scene_id: str) -> None:
    if selected_scene_id and selected_scene_id not in scene_ids:
        raise ValueError("The selected editor scene no longer exists.")


def validate_captions(duration_limit: float, state: ProjectEditorState, scene_ids: set[str]) -> None:
    scenes_by_id = {scene.id: scene for scene in state.scenes}
    for caption in state.captions:
        if caption.end - caption.start < MIN_CAPTION_SECONDS:
            raise ValueError("Each caption must be at least 0.2 seconds long.")
        if caption.start < 0 or caption.end > duration_limit + TIMING_EPSILON:
            raise ValueError("Caption timings must stay within the source media duration.")
        if caption.scene_id is None:
            continue
        if caption.scene_id not in scene_ids:
            raise ValueError("Each caption must point to a valid scene.")
        scene = scenes_by_id[caption.scene_id]
        if caption.start < scene.start - TIMING_EPSILON or caption.end > scene.end + TIMING_EPSILON:
            raise ValueError("Caption timings must stay inside their scene boundaries.")


def validate_scene_timing(start: float, end: float, duration_limit: float, previous_end: float) -> None:
    if end - start < MIN_SCENE_SECONDS:
        raise ValueError("Each scene must be at least 0.5 seconds long.")
    if start < 0 or end > duration_limit + TIMING_EPSILON:
        raise ValueError("Scene timings must stay within the source media duration.")
    if start < previous_end - TIMING_EPSILON:
        raise ValueError("Scenes cannot overlap on the editor timeline.")


def project_duration_limit(project: ProjectRecord) -> float:
    candidates = [
        project.preview_video.duration_seconds if project.preview_video else 0.0,
        project.edit_plan.total_duration_seconds if project.edit_plan else 0.0,
        project.voiceover.duration_seconds if project.voiceover else 0.0,
        project.transcript[-1].end if project.transcript else 0.0,
        project.guide.steps[-1].end if project.guide and project.guide.steps else 0.0,
    ]
    return max([candidate for candidate in candidates if candidate > 0], default=12.0)
