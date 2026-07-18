from __future__ import annotations

from app.models.projects import EditPlanScene, ProjectRecord

MIN_CLIP_DURATION_SECONDS = 0.5
CLIP_PADDING_SECONDS = 0.12


def highlight_clips(project: ProjectRecord) -> list[tuple[float, float]]:
    if project.edit_plan is None:
        return []
    clips: list[tuple[float, float]] = []
    previous_end = 0.0
    for scene in project.edit_plan.scenes:
        clip = normalized_clip(scene, previous_end)
        if clip is None:
            continue
        clips.append(clip)
        previous_end = clip[1]
    return clips


def proxy_highlight_duration(project: ProjectRecord) -> float:
    return round(sum(end - start for start, end in highlight_clips(project)), 2)


def normalized_clip(scene: EditPlanScene, previous_end: float) -> tuple[float, float] | None:
    start = max(scene.start - CLIP_PADDING_SECONDS, previous_end)
    end = max(scene.end + CLIP_PADDING_SECONDS, start + MIN_CLIP_DURATION_SECONDS)
    if end - start < MIN_CLIP_DURATION_SECONDS:
        return None
    return round(start, 2), round(end, 2)
