from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, ProjectRecord, VoiceoverMode
from app.services.render_proxy_clips import RenderClip, highlight_clips
from app.services.preview_scene_composition import PreviewSceneComposition, build_scene_composition

MIN_PREVIEW_CLIP_SECONDS = 0.6
MAX_PREVIEW_CLIP_SECONDS = 9.5
VOICEOVER_TAIL_SECONDS = 0.28


@dataclass(frozen=True)
class PreviewCameraKeyframe:
    offset_seconds: float
    scale: float
    x_offset: float
    y_offset: float
    hold_ratio: float
    easing: str


@dataclass(frozen=True)
class PreviewVoiceoverSegment:
    text: str
    start: float
    end: float
    duration_seconds: float


@dataclass(frozen=True)
class PreviewTransition:
    style: str
    duration_seconds: float


@dataclass(frozen=True)
class PreviewManifestClip:
    scene: EditPlanScene
    source_start: float
    source_end: float
    trim_start: float
    trim_end: float
    stage: str
    scene_priority: int
    voiceover_line: str
    voiceover_segment: PreviewVoiceoverSegment | None
    camera_keyframes: list[PreviewCameraKeyframe]
    highlight_events: list[EditPlanHighlight]
    caption_events: list[EditPlanCaption]
    transition_in: PreviewTransition
    transition_out: PreviewTransition
    composition: PreviewSceneComposition
    animated_crop: bool
    spotlight: bool
    freeze_frame: bool = False

    @property
    def duration_seconds(self) -> float:
        return round(self.trim_end - self.trim_start, 2)


@dataclass(frozen=True)
class PreviewManifest:
    clips: list[PreviewManifestClip]
    total_duration_seconds: float
    stage_counts: dict[str, int]
    voiceover_mode: VoiceoverMode

    def diagnostic_payloads(self, voiceover_ready: bool) -> list[dict[str, object]]:
        return [
            {
                "scene_number": clip.scene.scene_number,
                "stage": clip.stage,
                "zoom_count": len(clip.scene.zooms),
                "highlight_count": len(clip.scene.highlights),
                "caption_count": len(clip.scene.captions),
                "animated_crop": clip.animated_crop,
                "spotlight": clip.spotlight,
                "voiceover_line": clip.voiceover_line,
                "clip_start": round(clip.source_start, 2),
                "clip_end": round(clip.source_end, 2),
                "voiceover_ready": voiceover_ready,
            }
            for clip in clips_for_logging(self.clips)
        ]


def build_preview_manifest(
    project: ProjectRecord,
    voiceover_mode: VoiceoverMode,
    quality: str,
) -> PreviewManifest:
    clips = highlight_clips(project)
    voice_map = voiceover_segments(project, voiceover_mode)
    voiced_indexes = voiced_clip_indexes(clips, voice_map)
    manifest_clips = [
        build_manifest_clip(
            clip,
            voice_map.get(clip.scene.scene_number) if index in voiced_indexes else None,
            quality,
            suppress_duplicate_spoken_line=clip.scene.scene_number in voice_map and index not in voiced_indexes,
        )
        for index, clip in enumerate(clips)
    ]
    validated_clips = validate_manifest_clips(manifest_clips)
    return PreviewManifest(
        clips=validated_clips,
        total_duration_seconds=round(sum(clip.duration_seconds for clip in validated_clips), 2),
        stage_counts={stage: sum(1 for clip in validated_clips if clip.stage == stage) for stage in ("establish", "focus", "settle")},
        voiceover_mode=voiceover_mode,
    )


def manifest_edit_plan(project: ProjectRecord, quality: str) -> EditPlanRecord:
    edit_plan = require_edit_plan(project.edit_plan)
    voiceover_mode = resolved_voiceover_mode(project)
    manifest = build_preview_manifest(project, voiceover_mode, quality)
    scenes = [clip.scene for clip in manifest.clips]
    return edit_plan.model_copy(update={"scenes": scenes, "total_duration_seconds": manifest.total_duration_seconds, "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": manifest.total_duration_seconds})})


def voiceover_segments(
    project: ProjectRecord,
    voiceover_mode: VoiceoverMode,
) -> dict[int, PreviewVoiceoverSegment]:
    if not should_apply_voiceover(project, voiceover_mode):
        return {}
    voiceover = project.voiceover
    if voiceover is None:
        return {}
    return {
        clip.scene_number: PreviewVoiceoverSegment(
            text=clip.text.strip(),
            start=round(clip.start, 2),
            end=round(clip.end, 2),
            duration_seconds=round(max(clip.duration_seconds, 0.1), 2),
        )
        for clip in voiceover.clips
        if clip.text.strip()
    }


def voiced_clip_indexes(
    clips: list[RenderClip],
    voice_map: dict[int, PreviewVoiceoverSegment],
) -> set[int]:
    preferred: dict[int, int] = {}
    for index, clip in enumerate(clips):
        scene_number = clip.scene.scene_number
        if scene_number not in voice_map or scene_number in preferred:
            continue
        preferred[scene_number] = index
    for index, clip in enumerate(clips):
        if clip.stage != "focus" or clip.scene.scene_number not in voice_map:
            continue
        preferred[clip.scene.scene_number] = index
    return set(preferred.values())


def build_manifest_clip(
    clip: RenderClip,
    voiceover_segment: PreviewVoiceoverSegment | None,
    quality: str,
    suppress_duplicate_spoken_line: bool = False,
) -> PreviewManifestClip:
    source_start = round(clip.start, 2)
    source_end = round(max(clip.end, clip.start + MIN_PREVIEW_CLIP_SECONDS), 2)
    trim_end = bounded_trim_end(source_start, source_end, voiceover_segment, quality)
    scene_priority = preview_priority(clip)
    filtered_caption_events = filtered_captions(clip.scene, source_start, trim_end, quality, voiceover_segment)
    filtered_highlight_events = stage_highlights(clip.stage, clip.scene.highlights, source_start, trim_end, quality)
    filtered_zoom_events = stage_zooms(clip.stage, clip.scene.zooms, source_start, trim_end, quality)
    scene = clip.scene.model_copy(
        update={
            "start": source_start,
            "end": trim_end,
            "render_duration_seconds": round(trim_end - source_start, 2),
            "spoken_line": resolved_spoken_line(clip, voiceover_segment, suppress_duplicate_spoken_line),
            "captions": filtered_caption_events,
            "highlights": filtered_highlight_events,
            "zooms": filtered_zoom_events,
        }
    )
    return PreviewManifestClip(
        scene=scene,
        source_start=source_start,
        source_end=source_end,
        trim_start=source_start,
        trim_end=trim_end,
        stage=clip.stage,
        scene_priority=scene_priority,
        voiceover_line=voiceover_text(voiceover_segment, scene.spoken_line),
        voiceover_segment=voiceover_segment,
        camera_keyframes=camera_keyframes(filtered_zoom_events, source_start, trim_end),
        highlight_events=filtered_highlight_events,
        caption_events=filtered_caption_events,
        transition_in=PreviewTransition(style=scene.transition_style, duration_seconds=scene.transition_duration_seconds),
        transition_out=PreviewTransition(style="fade", duration_seconds=min(scene.transition_duration_seconds, 0.2)),
        composition=build_scene_composition(scene, clip.stage),
        animated_crop=bool(filtered_zoom_events),
        spotlight=bool(filtered_highlight_events),
        freeze_frame=False,
    )


def filtered_captions(
    scene: EditPlanScene,
    clip_start: float,
    clip_end: float,
    quality: str,
    voiceover_segment: PreviewVoiceoverSegment | None,
) -> list[EditPlanCaption]:
    if not scene.show_captions:
        return []
    captions = scene.captions
    filtered = [caption for caption in captions if overlaps(caption.start, caption.end, clip_start, clip_end)]
    aligned = [align_caption(caption, clip_start, clip_end, voiceover_segment) for caption in filtered]
    if quality == "preview":
        return aligned[:1]
    return aligned


def filtered_highlights(
    highlights: list[EditPlanHighlight],
    clip_start: float,
    clip_end: float,
    quality: str,
) -> list[EditPlanHighlight]:
    filtered = [highlight for highlight in highlights if overlaps(highlight.start, highlight.end, clip_start, clip_end)]
    if quality == "preview":
        return filtered[:1]
    return filtered


def filtered_zooms(
    zooms: list[EditPlanZoom],
    clip_start: float,
    clip_end: float,
    quality: str,
) -> list[EditPlanZoom]:
    filtered = [zoom for zoom in zooms if overlaps(zoom.start, zoom.end, clip_start, clip_end)]
    if quality == "preview":
        return filtered[:1]
    return filtered


def stage_highlights(
    stage: str,
    highlights: list[EditPlanHighlight],
    clip_start: float,
    clip_end: float,
    quality: str,
) -> list[EditPlanHighlight]:
    return [] if stage == "establish" else filtered_highlights(highlights, clip_start, clip_end, quality)


def stage_zooms(
    stage: str,
    zooms: list[EditPlanZoom],
    clip_start: float,
    clip_end: float,
    quality: str,
) -> list[EditPlanZoom]:
    return [] if stage == "establish" else filtered_zooms(zooms, clip_start, clip_end, quality)


def validate_manifest_clips(clips: list[PreviewManifestClip]) -> list[PreviewManifestClip]:
    validated: list[PreviewManifestClip] = []
    previous_end = 0.0
    for clip in clips:
        validated_clip = validated_manifest_clip(clip, previous_end)
        validated.append(validated_clip)
        previous_end = validated_clip.trim_end
    return validated


def validated_manifest_clip(clip: PreviewManifestClip, previous_end: float) -> PreviewManifestClip:
    start = max(round(clip.source_start, 2), previous_end)
    end = round(max(required_trim_end(start, clip.trim_end, clip.voiceover_segment), start + MIN_PREVIEW_CLIP_SECONDS), 2)
    source_start, source_end, freeze_frame = clip_source_window(clip, start)
    scene = clip.scene.model_copy(update={"start": start, "end": end, "render_duration_seconds": round(end - start, 2)})
    return clip.__class__(
        scene=scene,
        source_start=source_start,
        source_end=source_end,
        trim_start=start,
        trim_end=end,
        stage=clip.stage,
        scene_priority=clip.scene_priority,
        voiceover_line=clip.voiceover_line,
        voiceover_segment=clip.voiceover_segment,
        camera_keyframes=clip.camera_keyframes,
        highlight_events=clip.highlight_events,
        caption_events=clip.caption_events,
        transition_in=clip.transition_in,
        transition_out=clip.transition_out,
        composition=clip.composition,
        animated_crop=clip.animated_crop,
        spotlight=clip.spotlight,
        freeze_frame=freeze_frame,
    )


def clip_source_window(
    clip: PreviewManifestClip,
    start: float,
) -> tuple[float, float, bool]:
    if start < clip.source_end:
        return start, min(clip.source_end, clip.trim_end), clip.freeze_frame or clip.trim_end > clip.source_end
    hold_end = clip.source_end
    hold_start = max(clip.source_start, round(hold_end - MIN_PREVIEW_CLIP_SECONDS, 2))
    if hold_end - hold_start < MIN_PREVIEW_CLIP_SECONDS:
        hold_start = max(0.0, round(hold_end - MIN_PREVIEW_CLIP_SECONDS, 2))
        hold_end = round(max(hold_end, hold_start + MIN_PREVIEW_CLIP_SECONDS), 2)
    return hold_start, hold_end, True


def clips_for_logging(clips: list[PreviewManifestClip]) -> list[PreviewManifestClip]:
    return sorted(clips, key=lambda clip: (clip.source_start, clip.scene.scene_number))


def overlaps(start: float, end: float, clip_start: float, clip_end: float) -> bool:
    return end > clip_start and start < clip_end


def align_caption(
    caption: EditPlanCaption,
    clip_start: float,
    clip_end: float,
    voiceover_segment: PreviewVoiceoverSegment | None,
) -> EditPlanCaption:
    start = max(round(caption.start, 2), clip_start)
    end = min(round(caption.end, 2), clip_end)
    if voiceover_segment is not None:
        end = min(end, round(clip_start + voiceover_segment.duration_seconds + 0.12, 2))
    end = round(max(end, start + MIN_PREVIEW_CLIP_SECONDS), 2)
    return caption.model_copy(update={"start": start, "end": min(end, clip_end)})


def extended_trim_end(
    source_start: float,
    source_end: float,
    voiceover_segment: PreviewVoiceoverSegment | None,
) -> float:
    if voiceover_segment is None:
        return source_end
    return round(max(source_end, source_start + voiceover_segment.duration_seconds + VOICEOVER_TAIL_SECONDS), 2)


def required_trim_end(
    clip_start: float,
    current_end: float,
    voiceover_segment: PreviewVoiceoverSegment | None,
) -> float:
    if voiceover_segment is None:
        return current_end
    return round(max(current_end, clip_start + voiceover_segment.duration_seconds + VOICEOVER_TAIL_SECONDS), 2)


def bounded_trim_end(
    source_start: float,
    source_end: float,
    voiceover_segment: PreviewVoiceoverSegment | None,
    quality: str,
) -> float:
    trim_end = extended_trim_end(source_start, source_end, voiceover_segment)
    if quality != "preview" or voiceover_segment is not None:
        return trim_end
    return round(min(trim_end, source_start + MAX_PREVIEW_CLIP_SECONDS), 2)


def voiceover_text(voiceover_segment: PreviewVoiceoverSegment | None, fallback: str) -> str:
    return voiceover_segment.text if voiceover_segment is not None else fallback


def resolved_spoken_line(
    clip: RenderClip,
    voiceover_segment: PreviewVoiceoverSegment | None,
    suppress_duplicate_spoken_line: bool,
) -> str:
    if voiceover_segment is not None:
        return voiceover_segment.text
    if suppress_duplicate_spoken_line:
        return ""
    return clip.scene.spoken_line


def should_apply_voiceover(project: ProjectRecord, voiceover_mode: VoiceoverMode) -> bool:
    voiceover = project.voiceover
    return bool(voiceover_mode in {"voiceover", "mixed"} and voiceover is not None and voiceover.status == "ready" and voiceover.clips)


def resolved_voiceover_mode(project: ProjectRecord) -> VoiceoverMode:
    if project.voiceover is None or project.voiceover.status != "ready":
        return "original"
    return project.voiceover.mode


def camera_keyframes(
    zooms: list[EditPlanZoom],
    clip_start: float,
    clip_end: float,
) -> list[PreviewCameraKeyframe]:
    keyframes = [
        PreviewCameraKeyframe(
            offset_seconds=round(max(zoom.start - clip_start, 0.0), 2),
            scale=round(max(zoom.scale, 1.0), 2),
            x_offset=round(zoom.x_offset, 3),
            y_offset=round(zoom.y_offset, 3),
            hold_ratio=round(zoom.hold_ratio, 3),
            easing=zoom.easing,
        )
        for zoom in zooms
        if overlaps(zoom.start, zoom.end, clip_start, clip_end)
    ]
    return keyframes[:2]


def preview_priority(clip: RenderClip) -> int:
    return {"focus": 3, "settle": 2, "establish": 1}.get(clip.stage, 1)


def require_edit_plan(edit_plan: EditPlanRecord | None) -> EditPlanRecord:
    if edit_plan is None:
        raise RuntimeError("Edit plan is required before building the preview manifest.")
    return edit_plan
