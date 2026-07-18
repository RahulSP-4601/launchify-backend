from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanScene, ProjectRecord

MIN_CLIP_DURATION_SECONDS = 0.45
CLIP_PADDING_SECONDS = 0.1
MAX_CLIP_DURATION_SECONDS = 3.4
MERGE_GAP_SECONDS = 0.18
MIN_ACTION_REEL_SECONDS = 12.0
MIN_SOURCE_COVERAGE_RATIO = 0.6


@dataclass(frozen=True)
class RenderClip:
    scene: EditPlanScene
    start: float
    end: float


def highlight_clips(project: ProjectRecord) -> list[RenderClip]:
    if project.edit_plan is None:
        return []
    clips = action_highlight_clips(project)
    if should_use_contextual_clips(project, clips):
        return contextual_highlight_clips(project)
    return clips


def action_highlight_clips(project: ProjectRecord) -> list[RenderClip]:
    if project.edit_plan is None:
        return []
    clips: list[RenderClip] = []
    previous_end = 0.0
    for scene in project.edit_plan.scenes:
        for start, end in normalized_clips(scene, previous_end):
            clips.append(RenderClip(scene=scene, start=start, end=end))
            previous_end = end
    return clips


def proxy_highlight_duration(project: ProjectRecord) -> float:
    return round(sum(clip.end - clip.start for clip in highlight_clips(project)), 2)


def should_use_contextual_clips(project: ProjectRecord, clips: list[RenderClip]) -> bool:
    if not clips:
        return True
    source_duration = project_source_duration(project)
    if source_duration <= 0:
        return False
    action_duration = sum(clip.end - clip.start for clip in clips)
    return action_duration < max(MIN_ACTION_REEL_SECONDS, source_duration * MIN_SOURCE_COVERAGE_RATIO)


def contextual_highlight_clips(project: ProjectRecord) -> list[RenderClip]:
    if project.edit_plan is None:
        return []
    scenes = sorted(project.edit_plan.scenes, key=lambda scene: scene.start)
    source_start, source_end = project_source_bounds(project)
    if not scenes or source_end - source_start <= 0:
        return []
    clips: list[RenderClip] = []
    previous_end = source_start
    for index, scene in enumerate(scenes):
        next_scene = scenes[index + 1] if index + 1 < len(scenes) else None
        clip_start = max(previous_end, source_start)
        clip_end = contextual_scene_end(scene, next_scene, source_end)
        if clip_end - clip_start < MIN_CLIP_DURATION_SECONDS:
            clip_end = min(source_end, max(clip_end, clip_start + MIN_CLIP_DURATION_SECONDS))
        if clip_end - clip_start < MIN_CLIP_DURATION_SECONDS:
            continue
        clips.append(RenderClip(scene=scene, start=round(clip_start, 2), end=round(clip_end, 2)))
        previous_end = clip_end
    return clips


def contextual_scene_end(
    scene: EditPlanScene,
    next_scene: EditPlanScene | None,
    source_end: float,
) -> float:
    if next_scene is None:
        return source_end
    boundary = midpoint(scene.end, next_scene.start)
    return min(source_end, max(boundary, scene.end))


def midpoint(left: float, right: float) -> float:
    return (left + right) / 2


def project_source_bounds(project: ProjectRecord) -> tuple[float, float]:
    start = parse_timestamp(getattr(project.recording_session, "started_at", "")) if project.recording_session is not None else 0.0
    end = parse_timestamp(getattr(project.recording_session, "ended_at", "")) if project.recording_session is not None else 0.0
    if end <= start and project.edit_plan is not None:
        end = max(scene.end for scene in project.edit_plan.scenes)
    return start, end


def project_source_duration(project: ProjectRecord) -> float:
    start, end = project_source_bounds(project)
    return max(end - start, 0.0)


def parse_timestamp(value: str) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalized_clips(scene: EditPlanScene, previous_end: float) -> list[tuple[float, float]]:
    windows = merge_windows(candidate_windows(scene))
    clips: list[tuple[float, float]] = []
    current_floor = previous_end
    for start, end in windows:
        clip = bounded_clip(scene, start, end, current_floor)
        if clip is None:
            continue
        clips.append(clip)
        current_floor = clip[1]
    if clips:
        return clips
    fallback = bounded_clip(scene, scene.start, scene.end, previous_end)
    return [fallback] if fallback is not None else []


def candidate_windows(scene: EditPlanScene) -> list[tuple[float, float]]:
    windows = [(zoom.start, zoom.end) for zoom in scene.zooms]
    windows.extend((highlight.start, highlight.end) for highlight in scene.highlights)
    if scene.action_timestamp is not None:
        windows.append((scene.action_timestamp - 0.45, scene.action_timestamp + 1.15))
    return windows or [(scene.start, scene.end)]


def merge_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(windows):
        if end - start <= 0.05:
            continue
        if not merged or start - merged[-1][1] > MERGE_GAP_SECONDS:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def bounded_clip(
    scene: EditPlanScene,
    start: float,
    end: float,
    previous_end: float,
) -> tuple[float, float] | None:
    clip_start = max(scene.start, start - CLIP_PADDING_SECONDS, previous_end)
    clip_end = min(scene.end, end + CLIP_PADDING_SECONDS)
    if clip_end - clip_start > MAX_CLIP_DURATION_SECONDS:
        clip_start, clip_end = centered_clip_bounds(scene, start, end, clip_start, clip_end)
    if clip_end - clip_start < MIN_CLIP_DURATION_SECONDS:
        clip_end = min(scene.end, max(clip_end, clip_start + MIN_CLIP_DURATION_SECONDS))
    if clip_end - clip_start < MIN_CLIP_DURATION_SECONDS:
        return None
    return round(clip_start, 2), round(clip_end, 2)


def centered_clip_bounds(
    scene: EditPlanScene,
    start: float,
    end: float,
    clip_start: float,
    clip_end: float,
) -> tuple[float, float]:
    anchor = action_anchor(scene, start, end)
    centered_start = max(clip_start, anchor - MAX_CLIP_DURATION_SECONDS * 0.45)
    centered_end = min(clip_end, max(anchor + MAX_CLIP_DURATION_SECONDS * 0.55, centered_start + MAX_CLIP_DURATION_SECONDS))
    centered_start = max(clip_start, centered_end - MAX_CLIP_DURATION_SECONDS)
    return centered_start, min(clip_end, centered_start + MAX_CLIP_DURATION_SECONDS)


def action_anchor(scene: EditPlanScene, start: float, end: float) -> float:
    if scene.action_timestamp is not None and start <= scene.action_timestamp <= end:
        return scene.action_timestamp
    highlighted = [highlight for highlight in scene.highlights if highlight.end > start and highlight.start < end]
    if highlighted:
        focus_peak = max(highlighted, key=lambda highlight: min(highlight.end, end) - max(highlight.start, start))
        return min(max((focus_peak.start + focus_peak.end) / 2, start), end)
    return min(max((start + end) / 2, start), end)
