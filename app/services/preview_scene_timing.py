from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.projects import EditPlanScene

if TYPE_CHECKING:
    from app.services.render_proxy_clips import RenderClip

MIN_STAGE_SECONDS = 0.72


def retime_scene_clips(
    scene: EditPlanScene,
    clips: list[RenderClip],
    scene_type: str,
    target_coverage_seconds: float,
) -> list[RenderClip]:
    if not clips:
        return clips
    if len(clips) == 1:
        clip = clips[0]
        start = max(scene.start, clip.start)
        end = min(scene.end, max(start + MIN_STAGE_SECONDS, start + min(target_coverage_seconds, scene_span(scene))))
        return [clip.__class__(scene=clip.scene, start=round(start, 2), end=round(end, 2), stage=clip.stage)]
    span_start = max(scene.start, min(clip.start for clip in clips))
    span_end = min(scene.end, max(clip.end for clip in clips))
    available = max(min(target_coverage_seconds, span_end - span_start), len(clips) * MIN_STAGE_SECONDS)
    weights = normalized_weights(scene_type, len(clips))
    starts = [span_start]
    cursor = span_start
    for weight in weights[:-1]:
        cursor += available * weight
        starts.append(round(cursor, 2))
    boundaries = starts + [round(min(span_end, span_start + available), 2)]
    compact = coalesced(boundaries)
    retimed: list[RenderClip] = []
    last_index = min(len(compact) - 2, len(clips) - 1)
    for index in range(last_index + 1):
        start = compact[index]
        end = compact[index + 1]
        if end - start < MIN_STAGE_SECONDS:
            end = round(start + MIN_STAGE_SECONDS, 2)
        source = clips[min(index, len(clips) - 1)]
        retimed.append(source.__class__(scene=source.scene, start=round(start, 2), end=round(min(end, scene.end), 2), stage=source.stage))
    return retimed or clips


def scene_span(scene: EditPlanScene) -> float:
    return max(scene.end - scene.start, MIN_STAGE_SECONDS)


def normalized_weights(scene_type: str, count: int) -> list[float]:
    if count <= 1:
        return [1.0]
    if count == 2:
        if scene_type == "result_hold":
            return [0.42, 0.58]
        if scene_type in {"course_card", "setup_choice"}:
            return [0.46, 0.54]
        return [0.48, 0.52]
    if scene_type == "auth_button":
        return [0.22, 0.5, 0.28]
    if scene_type == "auth_card":
        return [0.26, 0.42, 0.32]
    if scene_type == "course_card":
        return [0.3, 0.42, 0.28]
    if scene_type == "setup_choice":
        return [0.28, 0.34, 0.38]
    if scene_type == "result_hold":
        return [0.18, 0.32, 0.5]
    return [0.26, 0.42, 0.32]


def coalesced(boundaries: list[float]) -> list[float]:
    compact = [boundaries[0]]
    for point in boundaries[1:]:
        if point - compact[-1] >= MIN_STAGE_SECONDS:
            compact.append(point)
        else:
            compact[-1] = max(compact[-1], point)
    if compact[-1] != boundaries[-1]:
        compact[-1] = boundaries[-1]
    return compact if len(compact) >= 2 else [boundaries[0], boundaries[-1]]
