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
    if clip.end - clip.start < minimum_stageable_seconds(clip):
        return [clip]
    pivot_times = focus_timestamps(clip)
    if not pivot_times:
        return [clip]
    boundaries = stage_boundaries(clip, pivot_times)
    segments = partition_clip(clip, boundaries)
    return segments if len(segments) > 1 else [clip]


def minimum_stageable_seconds(clip: RenderClip) -> float:
    profile = scene_stage_profile(clip)
    if clip.scene.layout_mode in {"screen-only", "dashboard-wide"}:
        if profile in {"auth_button", "auth_card"}:
            return 1.35
        if profile in {"course_card", "setup_choice"}:
            return 1.55
        return 1.45 if clip.scene.scene_role == "action" else 1.7
    return MIN_STAGEABLE_SECONDS


def focus_timestamps(clip: RenderClip) -> list[float]:
    scene = clip.scene
    focus_points = []
    if scene.focus_start_timestamp is not None and clip.start < scene.focus_start_timestamp < clip.end:
        focus_points.append(scene.focus_start_timestamp)
    if scene.focus_end_timestamp is not None and clip.start < scene.focus_end_timestamp < clip.end:
        focus_points.append(scene.focus_end_timestamp)
    focus_points.extend(zoom.start for zoom in scene.zooms if clip.start < zoom.start < clip.end)
    focus_points.extend(highlight.start for highlight in scene.highlights if clip.start < highlight.start < clip.end)
    if scene.action_timestamp is not None and clip.start < scene.action_timestamp < clip.end:
        focus_points.append(scene.action_timestamp)
    if scene.result_anchor_timestamp is not None and clip.start < scene.result_anchor_timestamp < clip.end:
        focus_points.append(scene.result_anchor_timestamp)
    return sorted(focus_points)


def stage_boundaries(clip: RenderClip, pivot_times: list[float]) -> list[float]:
    anchors = sorted({round(point, 2) for point in pivot_times if clip.start + 0.16 < point < clip.end - 0.16})
    if not anchors:
        return [clip.start, clip.end]
    profile = scene_stage_profile(clip)
    intro_ratio, settle_ratio = stage_balance(profile)
    duration = clip.end - clip.start
    establish_floor = max(MIN_SEGMENT_SECONDS, round(duration * intro_ratio, 2))
    settle_floor = max(MIN_SEGMENT_SECONDS, round(duration * settle_ratio, 2))
    first_focus = max(clip.start + establish_floor, anchors[0] - transition_margin(profile, "establish"))
    last_focus = min(clip.end - settle_floor, anchors[-1] + transition_margin(profile, "settle"))
    if last_focus - first_focus < MIN_SEGMENT_SECONDS:
        return [clip.start, clip.end]
    boundaries = [round(clip.start, 2), round(first_focus, 2)]
    inner_anchors = anchors[1:]
    for left, right in zip(anchors, inner_anchors):
        midpoint = round((left + right) / 2, 2)
        if midpoint - boundaries[-1] >= MIN_SEGMENT_SECONDS and clip.end - midpoint >= MIN_SEGMENT_SECONDS:
            boundaries.append(midpoint)
    if last_focus - boundaries[-1] >= MIN_SEGMENT_SECONDS:
        boundaries.append(round(last_focus, 2))
    boundaries.append(round(clip.end, 2))
    return coalesced_boundaries(boundaries)


def coalesced_boundaries(boundaries: list[float]) -> list[float]:
    compact = [boundaries[0]]
    for point in boundaries[1:]:
        if point - compact[-1] >= MIN_SEGMENT_SECONDS:
            compact.append(point)
        else:
            compact[-1] = max(compact[-1], point)
    if compact[-1] != boundaries[-1]:
        compact[-1] = boundaries[-1]
    return compact if len(compact) >= 2 else [boundaries[0], boundaries[-1]]


def stage_balance(profile: str) -> tuple[float, float]:
    if profile == "auth_button":
        return 0.22, 0.2
    if profile == "auth_card":
        return 0.24, 0.22
    if profile == "course_card":
        return 0.26, 0.24
    if profile == "setup_choice":
        return 0.24, 0.28
    if profile == "result_hold":
        return 0.18, 0.34
    return 0.22, 0.24


def transition_margin(profile: str, stage: str) -> float:
    if profile in {"auth_button", "course_card"} and stage == "establish":
        return TRANSITION_MARGIN_SECONDS * 1.25
    if profile in {"setup_choice", "result_hold"} and stage == "settle":
        return TRANSITION_MARGIN_SECONDS * 1.45
    return TRANSITION_MARGIN_SECONDS


def partition_clip(clip: RenderClip, boundaries: list[float]) -> list[RenderClip]:
    if len(boundaries) < 2:
        return [clip]
    parts: list[RenderClip] = []
    last_index = len(boundaries) - 2
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        stage = clip_stage(index, last_index)
        segment = clip_part(clip, start, end, stage)
        if segment is not None:
            parts.append(segment)
    return parts


def clip_stage(index: int, last_index: int) -> Literal["establish", "focus", "settle"]:
    if index == 0 and last_index >= 1:
        return "establish"
    if index == last_index and last_index >= 1:
        return "settle"
    return "focus"


def clip_part(
    clip: RenderClip,
    start: float,
    end: float,
    stage: Literal["establish", "focus", "settle"],
) -> RenderClip | None:
    if end - start < MIN_SEGMENT_SECONDS:
        return None
    return clip.__class__(scene=clip.scene, start=round(start, 2), end=round(end, 2), stage=stage)


def scene_stage_profile(clip: RenderClip) -> str:
    combined = " ".join(
        part.lower()
        for part in (
            clip.scene.title,
            clip.scene.purpose,
            clip.scene.spoken_line,
            clip.scene.on_screen_text,
            clip.scene.source_excerpt,
            clip.scene.specific_target_label,
        )
        if part
    )
    if clip.scene.scene_role == "result":
        return "result_hold"
    if clip.scene.action_class == "auth_action":
        if any(token in combined for token in ("account", "existing", "continue")):
            return "auth_card"
        return "auth_button"
    if clip.scene.action_class == "card_selection":
        return "course_card"
    if any(token in combined for token in ("difficulty", "setup", "preferences", "level")):
        return "setup_choice"
    return "generic"
