from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Literal

from app.services.preview_clip_safety import pre_render_clip_issue
from app.services.preview_manifest import PreviewManifestClip
from app.services.preview_render_report import RenderedClipSegment
from app.services.preview_render_guard import clip_render_variants, validate_rendered_clip
from app.services.render_runtime_helpers import output_duration_seconds, run_process_with_heartbeat

logger = logging.getLogger(__name__)
ExportQuality = Literal["preview", "final"]
Heartbeat = Callable[[], None]
BuildClipCommand = Callable[[Path, PreviewManifestClip, Path, bool, ExportQuality, Path], list[str]]
TimeoutSeconds = Callable[[float], int]


def render_clip_segment_with_fallback(
    source_video: Path,
    clip: PreviewManifestClip,
    index: int,
    render_audio: bool,
    quality: ExportQuality,
    working_dir: Path,
    heartbeat: Heartbeat | None,
    deadline: float,
    clip_count: int,
    build_clip_command: BuildClipCommand,
    remaining_timeout_seconds: TimeoutSeconds,
) -> RenderedClipSegment | None:
    last_error: str | None = None
    for variant_name, candidate in clip_render_variants(clip, quality):
        if skip_variant_for_preflight(candidate, variant_name):
            last_error = pre_render_clip_issue(candidate)
            continue
        segment, validation_error = render_variant_segment(
            source_video, candidate, index, render_audio, quality, working_dir, heartbeat, deadline, variant_name, build_clip_command, remaining_timeout_seconds,
        )
        if validation_error is not None:
            if should_skip_empty_settle(validation_error, index, clip_count, candidate, variant_name):
                return None
            last_error = validation_error
            continue
        return segment
    raise RuntimeError(f"clip_guard_failed:{index}:{last_error or 'render_failed'}")


def skip_variant_for_preflight(candidate: PreviewManifestClip, variant_name: str) -> bool:
    issue = pre_render_clip_issue(candidate)
    if issue is None:
        return False
    logger.warning("Preview clip preflight fallback: scene=%s, stage=%s, profile=%s, reason=%s.", candidate.scene.scene_number, candidate.stage, variant_name, issue)
    return True


def render_variant_segment(
    source_video: Path,
    candidate: PreviewManifestClip,
    index: int,
    render_audio: bool,
    quality: ExportQuality,
    working_dir: Path,
    heartbeat: Heartbeat | None,
    deadline: float,
    variant_name: str,
    build_clip_command: BuildClipCommand,
    remaining_timeout_seconds: TimeoutSeconds,
) -> tuple[RenderedClipSegment, str | None]:
    clip_path = working_dir / f"clip-{index:02d}.mp4"
    command = build_clip_command(source_video, candidate, clip_path, render_audio, quality, working_dir)
    run_process_with_heartbeat(command, timeout_seconds=remaining_timeout_seconds(deadline), heartbeat=heartbeat)
    validation_error = validate_rendered_clip(candidate, clip_path)
    if validation_error is not None:
        logger.warning("Preview clip fallback triggered: scene=%s, stage=%s, profile=%s, reason=%s.", candidate.scene.scene_number, candidate.stage, variant_name, validation_error)
    else:
        logger.info("Preview clip rendered: scene=%s, stage=%s, expected_duration=%.2f, output_duration=%.2f, motion=%s, spotlight=%s, freeze_frame=%s, profile=%s.", candidate.scene.scene_number, candidate.stage, candidate.duration_seconds, output_duration_seconds(clip_path, fallback=candidate.duration_seconds), candidate.animated_crop, candidate.spotlight, candidate.freeze_frame, variant_name)
    return RenderedClipSegment(path=clip_path, clip=candidate, profile_name=variant_name), validation_error


def should_skip_empty_settle(
    validation_error: str,
    index: int,
    clip_count: int,
    candidate: PreviewManifestClip,
    variant_name: str,
) -> bool:
    if validation_error != "empty_clip" or index != clip_count - 1 or candidate.stage != "settle":
        return False
    logger.warning("Skipping empty trailing clip: scene=%s, stage=%s, profile=%s.", candidate.scene.scene_number, candidate.stage, variant_name)
    return True
