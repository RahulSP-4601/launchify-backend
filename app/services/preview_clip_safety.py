from __future__ import annotations

from app.models.projects import FocusBox
from app.services.preview_manifest import PreviewManifestClip


def pre_render_clip_issue(clip: PreviewManifestClip) -> str | None:
    if clip.trim_end - clip.trim_start < 0.08:
        return "invalid_duration_window"
    if clip.source_end < clip.source_start and not clip.freeze_frame:
        return "invalid_source_window"
    if clip.animated_crop and invalid_zoom_path(clip):
        return "invalid_crop_path"
    if clip.spotlight and invalid_highlight_path(clip):
        return "invalid_highlight_focus"
    if clip.voiceover_line.strip() and clip.source_end - clip.source_start < 0.3 and not clip.freeze_allowed:
        return "source_coverage_insufficient"
    return None


def invalid_zoom_path(clip: PreviewManifestClip) -> bool:
    focus_boxes = [zoom.focus_box for zoom in clip.scene.zooms if zoom.focus_box is not None]
    if not focus_boxes:
        # Scene-driven crop plans may intentionally rely on center anchoring.
        return False
    return not any(valid_focus_box(box) for box in focus_boxes)


def invalid_highlight_path(clip: PreviewManifestClip) -> bool:
    focus_boxes = [highlight.focus_box for highlight in clip.scene.highlights if highlight.focus_box is not None]
    if not focus_boxes:
        # Scene-level spotlight can safely render without a per-highlight bbox.
        return False
    return not any(valid_focus_box(box) for box in focus_boxes)


def valid_focus_box(box: FocusBox | None) -> bool:
    if box is None:
        return False
    if box.width <= 0.01 or box.height <= 0.01:
        return False
    if box.x + box.width > 1.01 or box.y + box.height > 1.01:
        return False
    return True
