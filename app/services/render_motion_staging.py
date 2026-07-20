from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.render_proxy_clips import RenderClip

MIN_SEGMENT_SECONDS = 0.72
MIN_STAGEABLE_SECONDS = 1.9
TRANSITION_MARGIN_SECONDS = 0.08


def stage_motion_clips(clips: list[RenderClip]) -> list[RenderClip]:
    staged: list[RenderClip] = []
    for clip in clips:
        staged.extend(stage_motion_clip(clip))
    return staged


def stage_motion_clip(clip: RenderClip) -> list[RenderClip]:
    if clip.end - clip.start < MIN_STAGEABLE_SECONDS:
        return [clip]
    pivot_times = focus_timestamps(clip)
    if not pivot_times:
        return [clip]
    first_focus = max(clip.start + MIN_SEGMENT_SECONDS, pivot_times[0] - TRANSITION_MARGIN_SECONDS)
    last_focus = min(clip.end - MIN_SEGMENT_SECONDS, pivot_times[-1] + TRANSITION_MARGIN_SECONDS)
    if last_focus - first_focus < MIN_SEGMENT_SECONDS:
        return [clip]
    segments = partition_clip(clip, first_focus, last_focus)
    return segments if len(segments) > 1 else [clip]


def focus_timestamps(clip: RenderClip) -> list[float]:
    scene = clip.scene
    focus_points = [zoom.start for zoom in scene.zooms if clip.start < zoom.start < clip.end]
    focus_points.extend(highlight.start for highlight in scene.highlights if clip.start < highlight.start < clip.end)
    if scene.action_timestamp is not None and clip.start < scene.action_timestamp < clip.end:
        focus_points.append(scene.action_timestamp)
    return sorted(focus_points)


def partition_clip(clip: RenderClip, first_focus: float, last_focus: float) -> list[RenderClip]:
    segments = [
        clip_part(clip, clip.start, first_focus, "establish"),
        clip_part(clip, first_focus, last_focus, "focus"),
        clip_part(clip, last_focus, clip.end, "settle"),
    ]
    return [segment for segment in segments if segment is not None]


def clip_part(
    clip: RenderClip,
    start: float,
    end: float,
    stage: Literal["establish", "focus", "settle"],
) -> RenderClip | None:
    if end - start < MIN_SEGMENT_SECONDS:
        return None
    return clip.__class__(scene=clip.scene, start=round(start, 2), end=round(end, 2), stage=stage)
