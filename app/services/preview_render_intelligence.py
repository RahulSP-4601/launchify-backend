from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Mapping

from app.models.projects import EditPlanHighlight, EditPlanScene, EditPlanZoom, ProjectRecord
from app.services.editorial_coverage import (
    dense_intent_scene,
    minimum_scene_seconds,
    normalized_scene_text,
    readability_floor_seconds,
    scene_hold_budget,
    scene_profile,
    scene_split_weights,
    semantic_clauses,
)
from app.services.editorial_state_machine import semantic_voice_line, should_compress_waiting
from app.services.editorial_transition_signals import requires_stateful_split
from app.services.preview_scene_timing import retime_scene_clips
from app.services.voiceover_pacing import fit_voice_line

if TYPE_CHECKING:
    from app.services.render_proxy_clips import RenderClip

MIN_SCENE_COVERAGE_SECONDS = 1.2
MIN_SPLIT_CLIP_SECONDS = 0.9
MIN_SPLITTABLE_DURATION_SECONDS = 2.35
@dataclass(frozen=True)
class SceneCoveragePlan:
    scene_number: int
    scene_type: str
    visual_coverage_seconds: float
    voiceover_seconds: float
    fitted_voiceover_seconds: float
    hold_budget_seconds: float
    target_coverage_seconds: float
    coverage_gap_seconds: float
    has_real_source_coverage: bool
    has_target_box: bool
    has_action_emphasis: bool
    has_voiceover_fit: bool
    requires_split: bool
    would_freeze_action: bool
    freeze_allowed: bool
    fitted_voiceover_line: str
@dataclass(frozen=True)
class PreviewRenderIntelligence:
    clips: list[RenderClip]
    scene_plans: dict[int, SceneCoveragePlan]
def build_preview_render_intelligence(
    project: ProjectRecord,
    clips: list[RenderClip],
    voice_map: Mapping[int, object],
) -> PreviewRenderIntelligence:
    grouped = group_clips_by_scene(clips)
    optimized: list[RenderClip] = []
    plans: dict[int, SceneCoveragePlan] = {}
    for scene_number, scene_clips in grouped.items():
        scene = scene_clips[0].scene
        voiceover = voice_map.get(scene_number)
        expanded = expanded_scene_clips(scene, scene_clips)
        if requires_scene_split(scene, voiceover, expanded):
            expanded = split_scene_clips(scene, voiceover, expanded)
        plan = scene_coverage_plan(scene, expanded, voiceover)
        expanded = retime_scene_clips(scene, expanded, plan.scene_type, plan.target_coverage_seconds)
        plan = scene_coverage_plan(scene, expanded, voiceover)
        plans[scene_number] = plan
        optimized.extend(apply_scene_profile(expanded, plan))
    return PreviewRenderIntelligence(clips=restore_clip_order(clips, optimized), scene_plans=plans)
def group_clips_by_scene(clips: list[RenderClip]) -> dict[int, list[RenderClip]]:
    grouped: dict[int, list[RenderClip]] = {}
    for clip in clips:
        grouped.setdefault(clip.scene.scene_number, []).append(clip)
    return grouped
def restore_clip_order(source: list[RenderClip], optimized: list[RenderClip]) -> list[RenderClip]:
    if not source or len(source) == len(optimized):
        return optimized
    return sorted(optimized, key=lambda clip: (clip.scene.scene_number, clip.start, clip.end, stage_rank(clip.stage)))
def expanded_scene_clips(scene: EditPlanScene, clips: list[RenderClip]) -> list[RenderClip]:
    if not clips:
        return []
    first = clips[0]
    last = clips[-1]
    expanded: list[RenderClip] = []
    for index, clip in enumerate(clips):
        start = scene.start if index == 0 else clip.start
        end = scene.end if index == len(clips) - 1 else clip.end
        expanded.append(clip.__class__(scene=clip.scene, start=round(start, 2), end=round(max(end, start + 0.1), 2), stage=clip.stage))
    if clip_seconds(expanded) < clip_seconds(clips):
        return clips
    if expanded[0].start > first.start or expanded[-1].end < last.end:
        return clips
    return coalesce_scene_gaps(expanded)
def coalesce_scene_gaps(clips: list[RenderClip]) -> list[RenderClip]:
    if len(clips) < 2:
        return clips
    adjusted: list[RenderClip] = [clips[0]]
    for clip in clips[1:]:
        previous = adjusted[-1]
        if previous.end >= clip.start:
            boundary = round((previous.end + clip.start) / 2, 2)
            adjusted[-1] = previous.__class__(scene=previous.scene, start=previous.start, end=boundary, stage=previous.stage)
            clip = clip.__class__(scene=clip.scene, start=boundary, end=clip.end, stage=clip.stage)
        adjusted.append(clip)
    return adjusted
def requires_scene_split(scene: EditPlanScene, voiceover: object | None, clips: list[RenderClip]) -> bool:
    if not clips:
        return False
    total_duration = clip_seconds(clips)
    if total_duration < MIN_SPLITTABLE_DURATION_SECONDS:
        return False
    if requires_stateful_split(scene):
        return True
    clauses = semantic_clauses(scene, voiceover)
    if len(clauses) < 2:
        return False
    if len(clips) == 1:
        return scene.scene_role == "action" or len(clauses) >= 3
    return len(clauses) > len(clips) and dense_intent_scene(scene, clauses)
def split_scene_clips(scene: EditPlanScene, voiceover: object | None, clips: list[RenderClip]) -> list[RenderClip]:
    ranges = covered_ranges(clips)
    duration = round(sum(end - start for start, end in ranges), 2)
    if duration < MIN_SPLIT_CLIP_SECONDS * 2:
        return clips
    clauses = semantic_clauses(scene, voiceover)
    split_count = target_split_count(scene, clips, clauses)
    segments = semantic_segments(ranges, split_count, scene_profile(scene))
    parts: list[RenderClip] = []
    for index, (start, end) in enumerate(segments):
        if end - start < MIN_SPLIT_CLIP_SECONDS:
            continue
        parts.append(
            clips[0].__class__(
                scene=clips[0].scene,
                start=round(start, 2),
                end=round(end, 2),
                stage=split_stage(index, len(segments) - 1),
            )
        )
    return parts or clips
def split_stage(index: int, last_index: int) -> Literal["establish", "focus", "settle"]:
    if last_index <= 0:
        return "focus"
    if index == 0:
        return "establish"
    if index == last_index:
        return "settle"
    return "focus"
def scene_coverage_plan(
    scene: EditPlanScene,
    clips: list[RenderClip],
    voiceover: object | None,
) -> SceneCoveragePlan:
    visual_coverage = round(max(clip_seconds(clips), MIN_SCENE_COVERAGE_SECONDS if clips else 0.0), 2)
    voiceover_seconds = round(max(getattr(voiceover, "duration_seconds", 0.0), 0.0), 2)
    hold_budget = scene_hold_budget(scene)
    scene_budget = scene_duration(scene)
    readability_floor = min(scene_budget, max(minimum_scene_seconds(scene), readability_floor_seconds(scene)))
    voice_budget = min(scene_budget, voiceover_seconds + hold_budget)
    target_coverage = round(max(visual_coverage, readability_floor, voice_budget), 2)
    if should_compress_waiting(scene):
        target_coverage = round(max(visual_coverage, min(target_coverage, max(readability_floor * 0.82, 1.05))), 2)
    elif scene.response_state_kind == "response" and scene.transition_confidence >= 0.7:
        target_coverage = round(min(scene_budget, max(target_coverage, readability_floor + 0.28)), 2)
    coverage_gap = round(max(target_coverage - visual_coverage, 0.0), 2)
    scene_type = scene_profile(scene)
    has_target = bool(primary_focus_signal(scene))
    has_emphasis = bool(scene.zooms or scene.highlights or has_target)
    freeze_allowed = scene.scene_role != "action"
    would_freeze_action = not freeze_allowed and visual_coverage < voiceover_seconds + max(hold_budget * 0.65, 0.55)
    fitted_voiceover_seconds = round(
        min(
            voiceover_seconds or visual_coverage,
            max(visual_coverage - max(hold_budget * 0.14, 0.06), min(readability_floor * 0.82, visual_coverage)),
        ),
        2,
    )
    fitted_voiceover_line = fitted_line(scene, voiceover, fitted_voiceover_seconds)
    return SceneCoveragePlan(
        scene_number=scene.scene_number,
        scene_type=scene_type,
        visual_coverage_seconds=visual_coverage,
        voiceover_seconds=voiceover_seconds,
        fitted_voiceover_seconds=fitted_voiceover_seconds,
        hold_budget_seconds=hold_budget,
        target_coverage_seconds=target_coverage,
        coverage_gap_seconds=coverage_gap,
        has_real_source_coverage=visual_coverage >= 0.35,
        has_target_box=has_target,
        has_action_emphasis=has_emphasis,
        has_voiceover_fit=voiceover_seconds <= visual_coverage + 0.45 or fitted_voiceover_seconds <= visual_coverage + 0.2,
        requires_split=requires_scene_split(scene, voiceover, clips),
        would_freeze_action=would_freeze_action,
        freeze_allowed=freeze_allowed,
        fitted_voiceover_line=fitted_voiceover_line,
    )
def apply_scene_profile(clips: list[RenderClip], plan: SceneCoveragePlan) -> list[RenderClip]:
    updated: list[RenderClip] = []
    for clip in clips:
        tuned_zooms = tuned_scene_zooms(clip.scene, clip.stage, plan.scene_type)
        tuned_highlights = tuned_scene_highlights(clip.scene, clip.stage, plan.scene_type)
        scene = clip.scene.model_copy(
            update={
                "spoken_line": plan.fitted_voiceover_line or clip.scene.spoken_line,
                "render_duration_seconds": round(max(clip.end - clip.start, clip.scene.render_duration_seconds or 0.0), 2),
                "readable_hold_seconds": tuned_readable_hold(clip.scene, plan.scene_type, clip.stage),
                "layout_mode": tuned_layout_mode(clip.scene, plan.scene_type, clip.stage),
                "transition_style": tuned_transition_style(clip.scene, plan.scene_type, clip.stage),
                "transition_duration_seconds": tuned_transition_duration(clip.scene, plan.scene_type, clip.stage),
                "zooms": tuned_zooms,
                "highlights": tuned_highlights,
            }
        )
        updated.append(clip.__class__(scene=scene, start=clip.start, end=clip.end, stage=clip.stage))
    return updated


def target_split_count(scene: EditPlanScene, clips: list[RenderClip], clauses: list[str]) -> int:
    base = min(max(len(clauses), 2), 4)
    total_duration = clip_seconds(clips)
    if scene_profile(scene) in {"course_card", "setup_choice"} and total_duration >= 4.8:
        return max(base, 4)
    if scene_profile(scene) == "auth_card" and total_duration >= 3.4:
        return max(base, 3)
    return base
def fitted_line(scene: EditPlanScene, voiceover: object | None, available_seconds: float) -> str:
    candidates = [
        getattr(voiceover, "text", "").strip(),
        semantic_voice_line(scene).strip(),
        scene.spoken_line.strip(),
        scene.purpose.strip(),
        scene.on_screen_text.strip(),
    ]
    for candidate in candidates:
        if candidate:
            return fit_voice_line(candidate, available_seconds)
    return ""


def semantic_segments(
    ranges: list[tuple[float, float]],
    split_count: int,
    scene_type: str,
) -> list[tuple[float, float]]:
    if not ranges:
        return []
    total_duration = round(sum(end - start for start, end in ranges), 2)
    if total_duration <= 0:
        return []
    weights = scene_split_weights(scene_type, split_count)
    targets = [round(total_duration * weight, 2) for weight in weights]
    beats: list[list[tuple[float, float]]] = []
    range_index = 0
    cursor = ranges[0][0]
    for segment_index, target in enumerate(targets):
        remaining_target = max(target, MIN_SPLIT_CLIP_SECONDS)
        beat_parts: list[tuple[float, float]] = []
        while range_index < len(ranges):
            range_start, range_end = ranges[range_index]
            cursor = max(cursor, range_start)
            available = round(range_end - cursor, 2)
            if available <= 0.0:
                range_index += 1
                if range_index < len(ranges):
                    cursor = ranges[range_index][0]
                continue
            take = available if segment_index == len(targets) - 1 else min(available, remaining_target)
            segment_start = cursor
            segment_end = round(cursor + take, 2)
            if segment_end - segment_start >= 0.05:
                beat_parts.append((segment_start, segment_end))
            remaining_target = round(remaining_target - take, 2)
            cursor = segment_end
            if remaining_target <= 0.02:
                break
            range_index += 1
            if range_index < len(ranges):
                cursor = ranges[range_index][0]
        if round(sum(end - start for start, end in beat_parts), 2) >= MIN_SPLIT_CLIP_SECONDS:
            beats.append(beat_parts)
    return flatten_beats(beats)


def covered_ranges(clips: list[RenderClip]) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for clip in sorted(clips, key=lambda item: (item.start, item.end)):
        if not ranges or clip.start > ranges[-1][1]:
            ranges.append((clip.start, clip.end))
            continue
        ranges[-1] = (ranges[-1][0], max(ranges[-1][1], clip.end))
    return [(round(start, 2), round(end, 2)) for start, end in ranges if end - start > 0.05]


def coalesce_segments(segments: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not segments:
        return []
    compact = [segments[0]]
    for start, end in segments[1:]:
        previous_start, previous_end = compact[-1]
        if start <= previous_end:
            compact[-1] = (previous_start, max(previous_end, end))
        else:
            compact.append((start, end))
    return [(round(start, 2), round(end, 2)) for start, end in compact if end - start >= MIN_SPLIT_CLIP_SECONDS]


def flatten_beats(beats: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    segments: list[tuple[float, float]] = []
    for index, parts in enumerate(beats):
        if not parts:
            continue
        start = parts[0][0]
        end = parts[-1][1]
        if len(parts) == 1 and end - start >= MIN_SPLIT_CLIP_SECONDS:
            segments.append((start, end))
            continue
        duration = round(sum(part_end - part_start for part_start, part_end in parts), 2)
        if duration < MIN_SPLIT_CLIP_SECONDS:
            continue
        if index == len(beats) - 1:
            segments.extend(parts)
            continue
        accumulated = 0.0
        for part_index, (part_start, part_end) in enumerate(parts):
            accumulated = round(accumulated + (part_end - part_start), 2)
            segments.append((part_start, part_end))
            if accumulated >= MIN_SPLIT_CLIP_SECONDS or part_index == len(parts) - 1:
                break
    return coalesce_segments(segments)


def tuned_readable_hold(scene: EditPlanScene, scene_type: str, stage: str) -> float:
    base = max(scene.readable_hold_seconds, 0.0)
    if stage == "settle":
        if scene_type == "result_hold":
            return round(max(base, 1.3), 2)
        if scene_type in {"course_card", "setup_choice"}:
            return round(max(base, 0.9), 2)
    if stage == "focus" and scene_type in {"auth_button", "auth_card"}:
        return round(max(base, 0.6), 2)
    return round(base, 2)


def tuned_layout_mode(scene: EditPlanScene, scene_type: str, stage: str) -> str:
    if scene.layout_mode != "auto":
        return scene.layout_mode
    if stage == "establish" and scene_type in {"course_card", "setup_choice", "generic"}:
        return "dashboard-wide"
    return "screen-only"


def tuned_transition_style(scene: EditPlanScene, scene_type: str, stage: str) -> str:
    if stage == "focus" and scene_type in {"auth_button", "course_card"}:
        return "focus-push"
    if stage == "settle" and scene_type == "result_hold":
        return "fade"
    return scene.transition_style


def tuned_transition_duration(scene: EditPlanScene, scene_type: str, stage: str) -> float:
    if stage == "establish":
        return round(min(max(scene.transition_duration_seconds, 0.18), 0.26), 2)
    if stage == "focus" and scene_type in {"auth_button", "auth_card", "course_card"}:
        return round(min(max(scene.transition_duration_seconds, 0.16), 0.24), 2)
    if stage == "settle":
        return round(min(max(scene.transition_duration_seconds, 0.2), 0.3), 2)
    return round(scene.transition_duration_seconds, 2)


def tuned_scene_zooms(scene: EditPlanScene, stage: str, scene_type: str) -> list[EditPlanZoom]:
    if not scene.zooms:
        return []
    profile_scale = {"auth_button": 1.12, "auth_card": 1.08, "course_card": 1.1, "setup_choice": 1.04}.get(scene_type, 1.02)
    tuned: list[EditPlanZoom] = []
    for zoom in scene.zooms:
        scale = zoom.scale
        if stage == "focus":
            scale = max(scale, profile_scale)
        elif stage == "establish":
            scale = min(scale, max(profile_scale - 0.08, 1.0))
        elif stage == "settle":
            scale = min(max(scale, profile_scale - 0.04), profile_scale + 0.03)
        tuned.append(zoom.model_copy(update={"scale": round(scale, 2), "hold_ratio": tuned_hold_ratio(zoom.hold_ratio, stage, scene_type)}))
    return tuned


def tuned_scene_highlights(scene: EditPlanScene, stage: str, scene_type: str) -> list[EditPlanHighlight]:
    if not scene.highlights:
        return []
    style = {"auth_button": "spotlight", "auth_card": "ambient-lift", "course_card": "ambient-lift", "setup_choice": "ambient"}.get(scene_type, "ambient-lift")
    tuned: list[EditPlanHighlight] = []
    for highlight in scene.highlights:
        tuned.append(highlight.model_copy(update={"style": style if stage != "establish" else "ambient"}))
    return tuned


def tuned_hold_ratio(value: float, stage: str, scene_type: str) -> float:
    baseline = max(value, 0.0)
    if stage == "establish":
        return round(max(baseline, 0.52), 2)
    if stage == "focus" and scene_type in {"auth_button", "course_card"}:
        return round(max(baseline, 0.72), 2)
    if stage == "settle":
        return round(max(baseline, 0.8), 2)
    return round(max(baseline, 0.62), 2)


def primary_focus_signal(scene: EditPlanScene) -> bool:
    return any(
        box is not None
        for box in (
            next((zoom.focus_box for zoom in scene.zooms if zoom.focus_box is not None), None),
            next((highlight.focus_box for highlight in scene.highlights if highlight.focus_box is not None), None),
        )
    )


def scene_duration(scene: EditPlanScene) -> float:
    base = scene.render_duration_seconds or (scene.end - scene.start)
    return round(max(base, scene.end - scene.start, minimum_scene_seconds(scene), MIN_SCENE_COVERAGE_SECONDS), 2)


def clip_seconds(clips: list[RenderClip]) -> float:
    return round(sum(max(clip.end - clip.start, 0.0) for clip in clips), 2)


def stage_rank(stage: str) -> int:
    if stage == "establish":
        return 0
    if stage == "focus":
        return 1
    if stage == "settle":
        return 2
    return 3
