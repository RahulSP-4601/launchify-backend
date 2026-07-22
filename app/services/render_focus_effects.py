from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanHighlight, EditPlanScene, EditPlanZoom, FocusBox

INTRO_LEAD_SECONDS = 0.18
LOOKBACK_SECONDS = 0.75
MIN_DYNAMIC_CROP_SECONDS = 0.9
MIN_ANIMATED_CROP_FPS = 24


@dataclass(frozen=True)
class CropState:
    focus_box: FocusBox
    origin_x: float
    origin_y: float
    crop_width: float
    crop_height: float


def scene_crop_plan(
    scene: EditPlanScene | None,
    clip_start: float,
    clip_end: float,
    stage: str = "focus",
    target_width: int = 1280,
    target_height: int = 720,
    fps: int = 30,
) -> tuple[str | None, FocusBox | None, tuple[float, float, float, float] | None]:
    if scene is None:
        return None, None, None
    start_state = crop_state(scene, clip_start, clip_end, "start", stage)
    end_state = crop_state(scene, clip_end, clip_end, "end", stage) or start_state
    if start_state is None and end_state is None:
        return None, None, None
    end_state = end_state or start_state
    start_state = start_state or staged_state(end_state, stage, "start")
    end_state = staged_state(end_state, stage, "end")
    if start_state is None or end_state is None or neutral_crop(start_state, end_state):
        return None, end_state.focus_box if end_state is not None else None, None
    animated = clip_end - clip_start > MIN_DYNAMIC_CROP_SECONDS and fps >= MIN_ANIMATED_CROP_FPS
    crop_bounds = (end_state.origin_x, end_state.origin_y, end_state.crop_width, end_state.crop_height)
    filter_text = animated_crop_filter(start_state, end_state, clip_end - clip_start, target_width, target_height, fps)
    if animated:
        return filter_text, end_state.focus_box, None
    return filter_text, rebased_box(end_state.focus_box, *crop_bounds), crop_bounds


def crop_state(
    scene: EditPlanScene,
    timestamp: float,
    clip_end: float,
    phase: str,
    stage: str,
) -> CropState | None:
    zoom = motion_zoom(scene, timestamp, phase)
    focus_box = crop_focus_box(scene, timestamp, clip_end)
    if focus_box is None and zoom is not None and zoom.focus_box is not None:
        focus_box = zoom.focus_box
    if focus_box is None:
        return None
    if zoom is None and phase == "start" and clip_end - timestamp >= MIN_DYNAMIC_CROP_SECONDS:
        return staged_state(box_state(focus_box, 1.12, 0.0, 0.0), stage, phase)
    if zoom is None:
        return staged_state(box_state(focus_box, 1.0, 0.0, 0.0), stage, phase)
    scale = softened_scale(zoom.scale, 0.32 if phase == "start" and zoom.start > timestamp else 1.0)
    return staged_state(box_state(focus_box, scale, zoom.x_offset, zoom.y_offset), stage, phase)


def box_state(focus_box: FocusBox, scale: float, x_offset: float, y_offset: float) -> CropState:
    crop_width = max(min(round(1 / min(scale, 1.32), 4), 0.96), 0.64)
    crop_height = max(min(round(crop_width * 0.8, 4), 0.92), 0.54)
    center_x = focus_box.x + focus_box.width / 2 + x_offset * 0.7
    center_y = focus_box.y + focus_box.height / 2 + y_offset * 0.7
    origin_x = clamp(center_x - crop_width / 2, 0.0, 1.0 - crop_width)
    origin_y = clamp(center_y - crop_height / 2, 0.0, 1.0 - crop_height)
    return CropState(focus_box=focus_box, origin_x=origin_x, origin_y=origin_y, crop_width=crop_width, crop_height=crop_height)


def motion_zoom(scene: EditPlanScene, timestamp: float, phase: str) -> EditPlanZoom | None:
    active = [zoom for zoom in scene.zooms if zoom.start <= timestamp + INTRO_LEAD_SECONDS < zoom.end]
    if active:
        return max(active, key=lambda zoom: zoom.scale)
    recent = [zoom for zoom in scene.zooms if 0.0 <= timestamp - zoom.end <= LOOKBACK_SECONDS]
    if recent:
        return max(recent, key=lambda zoom: zoom.scale)
    return None


def softened_scale(scale: float, amount: float) -> float:
    if amount >= 1.0:
        return scale
    return round(1.0 + (scale - 1.0) * max(amount, 0.0), 2)


def softened_state(state: CropState | None, amount: float) -> CropState | None:
    if state is None:
        return None
    width = clamp(state.crop_width + (1.0 - state.crop_width) * amount, 0.72, 0.98)
    height = clamp(state.crop_height + (1.0 - state.crop_height) * amount, 0.72, 0.98)
    center_x = state.origin_x + state.crop_width / 2
    center_y = state.origin_y + state.crop_height / 2
    origin_x = clamp(center_x - width / 2, 0.0, 1.0 - width)
    origin_y = clamp(center_y - height / 2, 0.0, 1.0 - height)
    return CropState(focus_box=state.focus_box, origin_x=origin_x, origin_y=origin_y, crop_width=width, crop_height=height)


def staged_state(state: CropState | None, stage: str, phase: str) -> CropState | None:
    if state is None:
        return None
    if stage == "establish":
        return softened_state(state, 0.66 if phase == "start" else 0.5)
    if stage == "settle":
        return softened_state(state, 0.22 if phase == "start" else 0.34)
    return shifted_state(softened_state(state, 0.24 if phase == "start" else 0.06), phase, drift=0.008)


def shifted_state(state: CropState | None, phase: str, drift: float) -> CropState | None:
    if state is None or drift <= 0:
        return state
    direction = -1 if phase == "start" else 1
    origin_x = clamp(state.origin_x + direction * drift, 0.0, 1.0 - state.crop_width)
    return CropState(
        focus_box=state.focus_box,
        origin_x=origin_x,
        origin_y=state.origin_y,
        crop_width=state.crop_width,
        crop_height=state.crop_height,
    )


def neutral_crop(start_state: CropState, end_state: CropState) -> bool:
    return end_state.crop_width >= 0.96 and abs(end_state.crop_width - start_state.crop_width) < 0.018


def animated_crop_filter(
    start_state: CropState,
    end_state: CropState,
    duration: float,
    target_width: int,
    target_height: int,
    fps: int,
) -> str:
    if duration <= MIN_DYNAMIC_CROP_SECONDS:
        return static_crop_filter(end_state)
    progress = frame_progress(duration, fps)
    eased = eased_progress(progress)
    zoom = animated_zoom(start_state.crop_width, end_state.crop_width, eased)
    origin_x = animated_value(start_state.origin_x, end_state.origin_x, eased)
    origin_y = animated_value(start_state.origin_y, end_state.origin_y, eased)
    return (
        "zoompan="
        f"z='{zoom}':"
        f"x='iw*{origin_x}':"
        f"y='ih*{origin_y}':"
        "d=1:"
        f"s={target_width}x{target_height}:"
        f"fps={fps}"
    )


def static_crop_filter(state: CropState) -> str:
    return f"crop=w=iw*{state.crop_width}:h=ih*{state.crop_height}:x=iw*{state.origin_x}:y=ih*{state.origin_y}"


def frame_progress(duration: float, fps: int) -> str:
    frames = max(int(round(max(duration, 0.2) * max(fps, 1))) - 1, 1)
    return f"min(on/{frames},1)"


def eased_progress(progress: str) -> str:
    return f"if(lt({progress},0.5),2*pow({progress},2),1-pow(-2*{progress}+2,2)/2)"


def animated_value(start: float, end: float, progress: str) -> str:
    return f"({round(start, 4)}+({round(end - start, 4)})*{progress})"


def animated_zoom(start_width: float, end_width: float, progress: str) -> str:
    start_zoom = round(1 / max(start_width, 0.01), 4)
    end_zoom = round(1 / max(end_width, 0.01), 4)
    return f"({start_zoom}+({round(end_zoom - start_zoom, 4)})*{progress})"


def crop_focus_box(scene: EditPlanScene, clip_start: float, clip_end: float) -> FocusBox | None:
    for highlight in active_highlights(scene, clip_start):
        if highlight.focus_box is not None:
            return highlight.focus_box
    for zoom in active_zooms(scene, clip_start):
        if zoom.focus_box is not None:
            return zoom.focus_box
    for highlight in overlapping_highlights(scene, clip_start, clip_end):
        if highlight.focus_box is not None:
            return highlight.focus_box
    return None


def crop_zoom_scale(scene: EditPlanScene, clip_start: float) -> float:
    zooms = active_zooms(scene, clip_start)
    return max((zoom.scale for zoom in zooms), default=1.0)


def active_zooms(scene: EditPlanScene, clip_start: float) -> list[EditPlanZoom]:
    return [zoom for zoom in scene.zooms if zoom.start <= clip_start + INTRO_LEAD_SECONDS < zoom.end]


def active_highlights(scene: EditPlanScene, clip_start: float) -> list[EditPlanHighlight]:
    return [highlight for highlight in scene.highlights if highlight.start <= clip_start + INTRO_LEAD_SECONDS < highlight.end]


def overlapping_highlights(scene: EditPlanScene, clip_start: float, clip_end: float) -> list[EditPlanHighlight]:
    return [highlight for highlight in scene.highlights if highlight.end > clip_start and highlight.start < clip_end]


def rebased_highlight_box(
    box: FocusBox | None,
    crop_bounds: tuple[float, float, float, float] | None,
) -> FocusBox | None:
    if box is None or crop_bounds is None:
        return box
    return rebased_box(box, *crop_bounds)


def rebased_box(box: FocusBox, origin_x: float, origin_y: float, crop_width: float, crop_height: float) -> FocusBox:
    return FocusBox(
        x=clamp((box.x - origin_x) / crop_width, 0.0, 1.0),
        y=clamp((box.y - origin_y) / crop_height, 0.0, 1.0),
        width=clamp(box.width / crop_width, 0.04, 1.0),
        height=clamp(box.height / crop_height, 0.04, 1.0),
    )


def spotlight_filters(box: FocusBox, start: float, end: float, style: str) -> list[str]:
    left = round(box.x, 4)
    top = round(box.y, 4)
    right = round(clamp(box.x + box.width, 0.0, 1.0), 4)
    bottom = round(clamp(box.y + box.height, 0.0, 1.0), 4)
    alpha = highlight_alpha(style)
    border = highlight_border(style)
    enable = f"between(t,{round(start, 2)},{round(end, 2)})"
    return [
        draw_mask(0.0, 0.0, left, 1.0, alpha, enable),
        draw_mask(right, 0.0, 1.0 - right, 1.0, alpha, enable),
        draw_mask(left, 0.0, max(right - left, 0.02), top, alpha, enable),
        draw_mask(left, bottom, max(right - left, 0.02), max(1.0 - bottom, 0.02), alpha, enable),
        "drawbox="
        f"x=iw*{left}:y=ih*{top}:w=iw*{max(right - left, 0.04)}:h=ih*{max(bottom - top, 0.04)}:"
        f"color=0xFFF1B8@{border}:t=2:enable='{enable}'",
        "drawbox="
        f"x=iw*{left}:y=ih*{top}:w=iw*{max(right - left, 0.04)}:h=ih*{max(bottom - top, 0.04)}:"
        f"color=0xFFE39A@{round(border * 0.45, 3)}:t=fill:enable='{enable}'",
    ]


def draw_mask(x: float, y: float, width: float, height: float, alpha: float, enable: str) -> str:
    return (
        "drawbox="
        f"x=iw*{round(x, 4)}:y=ih*{round(y, 4)}:w=iw*{round(max(width, 0.0), 4)}:h=ih*{round(max(height, 0.0), 4)}:"
        f"color=black@{alpha}:t=fill:enable='{enable}'"
    )


def highlight_alpha(style: str) -> float:
    if style == "ambient":
        return 0.05
    if style == "ambient-lift":
        return 0.065
    if style == "spotlight":
        return 0.08
    return 0.05


def highlight_border(style: str) -> float:
    if style == "ambient":
        return 0.16
    if style == "ambient-lift":
        return 0.24
    if style == "spotlight":
        return 0.28
    return 0.18


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))
