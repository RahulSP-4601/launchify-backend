from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

from app.models.projects import ProjectRecord, VoiceoverMode
from app.services.preview_manifest import PreviewManifest, PreviewManifestClip, PreviewTransition
from app.services.render_runtime_helpers import output_duration_seconds

RenderProfile = Literal["balanced", "no_motion", "no_spotlight", "simple_crop", "static_frame"]
MIN_RENDER_BYTES = 1024
MAX_CLIP_DURATION_SLACK_SECONDS = 0.9
MAX_CLIP_DURATION_SCALE = 1.8


def render_profiles(manifest: PreviewManifest, quality: str) -> list[tuple[RenderProfile, PreviewManifest]]:
    profiles: list[tuple[RenderProfile, PreviewManifest]] = [("balanced", manifest)]
    if quality != "preview":
        return profiles
    profiles.append(("no_motion", degrade_manifest(manifest, disable_motion=True)))
    profiles.append(("no_spotlight", degrade_manifest(manifest, disable_motion=True, disable_spotlight=True)))
    profiles.append(("simple_crop", degrade_manifest(manifest, disable_motion=True, disable_spotlight=True, simple_crop=True)))
    profiles.append(("static_frame", degrade_manifest(manifest, disable_motion=True, disable_spotlight=True, simple_crop=True, freeze_frame=True)))
    return profiles


def validate_rendered_preview(
    manifest: PreviewManifest,
    output_path: Path,
    voiceover_mode: VoiceoverMode,
    voiceover_audio: Path | None,
) -> str | None:
    if voiceover_mode == "voiceover" and voiceover_audio is None:
        return "missing_voiceover_audio"
    duration = output_duration_seconds(output_path, fallback=manifest.total_duration_seconds)
    if not output_path.exists() or (output_path.stat().st_size < MIN_RENDER_BYTES and duration <= 0.05):
        return "empty_output"
    lower_bound = max(manifest.total_duration_seconds * 0.65, 0.3)
    upper_bound = max(manifest.total_duration_seconds * 1.35, manifest.total_duration_seconds + 1.0)
    if duration < lower_bound or duration > upper_bound:
        return f"duration_mismatch:{duration:.2f}"
    if any(clip.duration_seconds <= 0.05 for clip in manifest.clips):
        return "zero_visible_scene"
    if any(clip.voiceover_segment and clip.voiceover_segment.duration_seconds > clip.duration_seconds + 0.45 for clip in manifest.clips):
        return "voiceover_outlives_visual"
    if len(manifest.clips) > 24:
        return "clip_count_excess"
    if any(clip.duration_seconds > max(clip.scene.render_duration_seconds or clip.duration_seconds, clip.duration_seconds) + 0.6 for clip in manifest.clips):
        return "scene_inflation"
    if any(clip.scene.confidence < 0.18 for clip in manifest.clips):
        return "low_visible_confidence"
    return None


def validate_rendered_clip(clip: PreviewManifestClip, clip_path: Path) -> str | None:
    duration = output_duration_seconds(clip_path, fallback=clip.duration_seconds)
    if not clip_path.exists() or (clip_path.stat().st_size < MIN_RENDER_BYTES and duration <= 0.05):
        return "empty_clip"
    upper_bound = max(clip.duration_seconds * MAX_CLIP_DURATION_SCALE, clip.duration_seconds + MAX_CLIP_DURATION_SLACK_SECONDS)
    if duration > upper_bound:
        return f"clip_duration_mismatch:{duration:.2f}"
    return None


def degrade_manifest(
    manifest: PreviewManifest,
    *,
    disable_motion: bool = False,
    disable_spotlight: bool = False,
    simple_crop: bool = False,
    freeze_frame: bool = False,
) -> PreviewManifest:
    clips = [degraded_clip(clip, disable_motion, disable_spotlight, simple_crop, freeze_frame) for clip in manifest.clips]
    return manifest.__class__(
        clips=clips,
        total_duration_seconds=round(sum(clip.duration_seconds for clip in clips), 2),
        stage_counts=manifest.stage_counts,
        voiceover_mode=manifest.voiceover_mode,
    )


def degraded_clip(
    clip: PreviewManifestClip,
    disable_motion: bool,
    disable_spotlight: bool,
    simple_crop: bool,
    freeze_frame: bool,
) -> PreviewManifestClip:
    scene = clip.scene.model_copy(
        update={
            "zooms": [] if disable_motion else clip.scene.zooms,
            "highlights": [] if disable_spotlight else clip.scene.highlights[:1],
            "captions": clip.scene.captions[:1],
            "transition_style": "fade" if simple_crop else clip.scene.transition_style,
            "camera_mode": "static" if simple_crop else clip.scene.camera_mode,
        }
    )
    return replace(
        clip,
        scene=scene,
        camera_keyframes=[] if disable_motion else clip.camera_keyframes,
        highlight_events=[] if disable_spotlight else clip.highlight_events[:1],
        caption_events=clip.caption_events[:1],
        transition_in=PreviewTransition(style="fade", duration_seconds=min(clip.transition_in.duration_seconds, 0.18)) if simple_crop else clip.transition_in,
        transition_out=PreviewTransition(style="fade", duration_seconds=min(clip.transition_out.duration_seconds, 0.18)) if simple_crop else clip.transition_out,
        animated_crop=not disable_motion and clip.animated_crop,
        spotlight=not disable_spotlight and clip.spotlight,
        freeze_frame=freeze_frame,
    )
