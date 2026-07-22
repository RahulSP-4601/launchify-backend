from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.projects import EditPlanScene, ProjectRecord
from app.services.render_motion_staging import stage_motion_clips
from app.services.render_scene_deduper import prune_redundant_render_scenes
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds, session_is_under_grounded

MIN_CLIP_DURATION_SECONDS = 0.45
CLIP_PADDING_SECONDS = 0.1
MAX_CLIP_DURATION_SECONDS = 5.8
MERGE_GAP_SECONDS = 0.18
MIN_ACTION_REEL_SECONDS = 12.0
MIN_SOURCE_COVERAGE_RATIO = 0.6
MIN_WALKTHROUGH_CLIP_SECONDS = 1.2
CHAPTER_GAP_SECONDS = 0.75
CHAPTER_LEAD_SECONDS = 0.35
CHAPTER_TAIL_SECONDS = 0.9
TARGET_WALKTHROUGH_COVERAGE_RATIO = 0.72
TARGET_WALKTHROUGH_SCENE_SECONDS = 6.6
TARGET_RESULT_SCENE_SECONDS = 7.4


@dataclass(frozen=True)
class RenderClip:
    scene: EditPlanScene
    start: float
    end: float
    stage: Literal["establish", "focus", "settle"] = "focus"


def highlight_clips(project: ProjectRecord) -> list[RenderClip]:
    if project.edit_plan is None:
        return []
    if under_grounded_walkthrough(project):
        return stage_motion_clips(contextual_highlight_clips(project))
    if prefer_walkthrough_clips(project):
        return stage_motion_clips(walkthrough_clips(project))
    clips = action_highlight_clips(project)
    if should_use_contextual_clips(project, clips):
        return stage_motion_clips(walkthrough_clips(project))
    return stage_motion_clips(clips)


def prefer_walkthrough_clips(project: ProjectRecord) -> bool:
    if project.guide is not None and project.guide.steps:
        return True
    if project.voiceover is not None and project.voiceover.mode == "voiceover":
        return True
    return False


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


def under_grounded_walkthrough(project: ProjectRecord) -> bool:
    duration_seconds = recording_duration_seconds(project.recording_session, project.transcript)
    return guide_is_under_grounded(project.guide, duration_seconds) or session_is_under_grounded(project.recording_session, project.transcript)


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


def walkthrough_clips(project: ProjectRecord) -> list[RenderClip]:
    if project.edit_plan is None:
        return []
    scenes = prune_redundant_render_scenes(sorted(project.edit_plan.scenes, key=lambda scene: scene.start))
    source_start, source_end = project_source_bounds(project)
    if not scenes or source_end - source_start <= 0:
        return []
    chapters = grouped_chapters(scenes)
    clips: list[RenderClip] = []
    previous_end = source_start
    for chapter in chapters:
        chapter_start, chapter_end = chapter_bounds(chapter, previous_end, source_start, source_end)
        chapter_clips = chapter_scene_clips(chapter, chapter_start, chapter_end, previous_end, source_end)
        if not chapter_clips:
            continue
        clips.extend(chapter_clips)
        previous_end = chapter_clips[-1].end
    if not clips:
        return contextual_highlight_clips(project)
    return clips


def grouped_chapters(scenes: list[EditPlanScene]) -> list[list[EditPlanScene]]:
    chapters: list[list[EditPlanScene]] = []
    current: list[EditPlanScene] = []
    for scene in scenes:
        if not current or join_chapter(current[-1], scene):
            current.append(scene)
            continue
        chapters.append(current)
        current = [scene]
    if current:
        chapters.append(current)
    return chapters


def join_chapter(left: EditPlanScene, right: EditPlanScene) -> bool:
    if right.start - left.end <= CHAPTER_GAP_SECONDS:
        return True
    if left.action_class == "auth_action" and right.action_class in {"auth_action", "navigation", "result_state"}:
        return right.start - left.end <= 2.4
    if left.scene_role == "action" and right.scene_role == "result":
        return right.start - left.end <= 2.8
    if left.action_class == "card_selection" and right.scene_role == "result":
        return right.start - left.end <= 2.6
    return False


def chapter_bounds(
    chapter: list[EditPlanScene],
    previous_end: float,
    source_start: float,
    source_end: float,
) -> tuple[float, float]:
    first = chapter[0]
    last = chapter[-1]
    start = max(previous_end, source_start, round(first.start - chapter_lead(first), 2))
    end = min(source_end, round(last.end + chapter_tail(last), 2))
    return start, max(end, start + MIN_WALKTHROUGH_CLIP_SECONDS)


def chapter_scene_clips(
    chapter: list[EditPlanScene],
    chapter_start: float,
    chapter_end: float,
    previous_end: float,
    source_end: float,
) -> list[RenderClip]:
    if not chapter:
        return []
    chapter_start = max(chapter_start, previous_end)
    available = max(min(chapter_end, source_end) - chapter_start, 0.0)
    if available < MIN_CLIP_DURATION_SECONDS:
        return []
    durations = allocated_chapter_durations(chapter, available)
    clips: list[RenderClip] = []
    cursor = chapter_start
    for index, scene in enumerate(chapter):
        end = cursor + durations[index]
        if index == len(chapter) - 1:
            end = chapter_start + available
        end = min(end, source_end)
        if end - cursor < MIN_WALKTHROUGH_CLIP_SECONDS:
            end = min(source_end, max(end, cursor + MIN_WALKTHROUGH_CLIP_SECONDS))
        if end - cursor < MIN_CLIP_DURATION_SECONDS:
            continue
        clips.append(RenderClip(scene=scene, start=round(cursor, 2), end=round(end, 2)))
        cursor = end
    return clips


def allocated_chapter_durations(
    chapter: list[EditPlanScene],
    available: float,
) -> list[float]:
    minimums = [MIN_WALKTHROUGH_CLIP_SECONDS] * len(chapter)
    desired = [max(scene_target_seconds(scene), MIN_WALKTHROUGH_CLIP_SECONDS) for scene in chapter]
    minimum_total = sum(minimums)
    if available <= minimum_total:
        base = available / max(len(chapter), 1)
        return [base] * len(chapter)
    remaining = available - minimum_total
    desired_headroom = max(sum(desired) - minimum_total, 0.0)
    if desired_headroom <= 0.0:
        weights = [scene_weight(scene) for scene in chapter]
        weight_total = sum(weights) or float(len(chapter))
        return [minimum + (remaining * (weight / weight_total)) for minimum, weight in zip(minimums, weights)]
    return [
        minimum + (remaining * ((target - minimum) / desired_headroom))
        for minimum, target in zip(minimums, desired)
    ]


def scene_target_seconds(scene: EditPlanScene) -> float:
    target = scene.render_duration_seconds or (scene.end - scene.start)
    floor = TARGET_RESULT_SCENE_SECONDS if scene.scene_role == "result" else TARGET_WALKTHROUGH_SCENE_SECONDS
    return max(target, floor, MIN_WALKTHROUGH_CLIP_SECONDS)


def scene_weight(scene: EditPlanScene) -> float:
    if scene.scene_role == "result":
        return 1.3
    if scene.action_class in {"auth_action", "card_selection"}:
        return 1.15
    return 1.0


def chapter_lead(scene: EditPlanScene) -> float:
    if scene.action_class == "auth_action":
        return CHAPTER_LEAD_SECONDS + 0.2
    if scene.scene_role == "result":
        return 0.15
    return CHAPTER_LEAD_SECONDS


def chapter_tail(scene: EditPlanScene) -> float:
    if scene.scene_role == "result":
        return CHAPTER_TAIL_SECONDS + 0.35
    if scene.action_class in {"auth_action", "navigation", "tab_switch"}:
        return CHAPTER_TAIL_SECONDS + 0.55
    if scene.action_class == "card_selection":
        return CHAPTER_TAIL_SECONDS + 0.8
    return CHAPTER_TAIL_SECONDS


def carried_scene_gap(scene: EditPlanScene, next_scene: EditPlanScene) -> float:
    if scene.action_class == "auth_action":
        return 1.4
    if scene.action_class == "card_selection" and next_scene.scene_role == "result":
        return 1.8
    if scene.action_class == "card_selection" and next_scene.action_class in {"button_click", "focus", "generic_action"}:
        return 2.1
    if scene.scene_role == "action" and next_scene.scene_role == "result":
        return 1.2
    return CHAPTER_GAP_SECONDS


def scene_lead(scene: EditPlanScene) -> float:
    if scene.action_class == "auth_action":
        return 0.45
    if scene.action_class == "card_selection":
        return 0.55
    if scene.scene_role == "result":
        return 0.22
    return 0.28


def rebalance_walkthrough_coverage(
    clips: list[RenderClip],
    source_start: float,
    source_end: float,
) -> list[RenderClip]:
    target = target_walkthrough_seconds(clips, source_start, source_end)
    expanded = clips
    for _ in range(3):
        current = sum(clip.end - clip.start for clip in expanded)
        if current >= target - 0.15:
            return expanded
        expanded = expand_clips_once(expanded, source_start, source_end, target - current)
    return expanded


def target_walkthrough_seconds(
    clips: list[RenderClip],
    source_start: float,
    source_end: float,
) -> float:
    source_duration = max(source_end - source_start, 0.0)
    coverage_floor = source_duration * TARGET_WALKTHROUGH_COVERAGE_RATIO
    scene_floor = len(clips) * TARGET_WALKTHROUGH_SCENE_SECONDS
    result_floor = sum(TARGET_RESULT_SCENE_SECONDS if clip.scene.scene_role == "result" else TARGET_WALKTHROUGH_SCENE_SECONDS for clip in clips)
    return min(source_duration * 0.78, max(coverage_floor, scene_floor, result_floor, MIN_ACTION_REEL_SECONDS))


def expand_clips_once(
    clips: list[RenderClip],
    source_start: float,
    source_end: float,
    remaining: float,
) -> list[RenderClip]:
    expanded: list[RenderClip] = []
    for index, clip in enumerate(clips):
        previous_end = expanded[-1].end if expanded else source_start
        next_start = clips[index + 1].start if index + 1 < len(clips) else source_end
        left_slack = outer_left_slack(clips, index, clip, source_start)
        right_slack = outer_right_slack(clips, index, clip, source_end)
        desired = remaining / max(len(clips) - index, 1)
        left_take = min(left_slack, desired * left_share(clip))
        right_take = min(right_slack, desired - left_take)
        expanded.append(
            clip.__class__(
                scene=clip.scene,
                start=round(max(previous_end, clip.start - left_take), 2),
                end=round(min(next_start, clip.end + right_take), 2),
                stage=clip.stage,
            )
        )
    return expanded


def outer_left_slack(
    clips: list[RenderClip],
    index: int,
    clip: RenderClip,
    source_start: float,
) -> float:
    if index != 0:
        return 0.0
    return max(clip.start - source_start, 0.0)


def outer_right_slack(
    clips: list[RenderClip],
    index: int,
    clip: RenderClip,
    source_end: float,
) -> float:
    if index != len(clips) - 1:
        return 0.0
    return max(source_end - clip.end, 0.0)


def left_share(clip: RenderClip) -> float:
    if clip.scene.scene_role == "result":
        return 0.28
    if clip.scene.action_class == "auth_action":
        return 0.45
    return 0.38


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
