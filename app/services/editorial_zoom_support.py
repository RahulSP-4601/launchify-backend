from __future__ import annotations

from typing import Any

from app.models.projects import EditPlanScene, EditPlanZoom, FocusBox


def default_screen_zoom(scene: EditPlanScene, focus_box: FocusBox) -> EditPlanZoom:
    return EditPlanZoom(
        start=scene.start,
        end=scene.end,
        scale=1.0,
        focus_region="center",
        reason="editorial setup focus",
        confidence=0.78,
        focus_box=focus_box,
        x_offset=0.0,
        y_offset=0.0,
        hold_ratio=0.82,
        smoothing=0.18,
    )


def editorial_zoom_window(
    scene: EditPlanScene,
    beats: Any,
    zoom: EditPlanZoom,
    index: int,
) -> tuple[float, float]:
    windows = zoom_windows(scene, beats)
    start, end = windows[min(index, len(windows) - 1)]
    base_start = beats.focus_start if zoom.start <= beats.focus_start else zoom.start
    base_end = beats.focus_end if index == 0 else beats.settle_end
    return (
        round(max(scene.start, min(start, base_start)), 2),
        round(min(scene.end, max(end, base_end)), 2),
    )


def zoom_windows(scene: EditPlanScene, beats: Any) -> list[tuple[float, float]]:
    if len(scene.zooms) <= 1:
        return [(beats.focus_start, min(scene.end, max(beats.focus_end, beats.focus_start + 0.86)))]
    if len(scene.zooms) == 2:
        return [(beats.focus_start, beats.focus_end), (beats.focus_end, beats.settle_end)]
    pre_focus = round(max(scene.start, min(beats.focus_start, beats.focus_end - 0.28)), 2)
    return [
        (scene.start, pre_focus),
        (pre_focus, beats.focus_end),
        (beats.focus_end, beats.settle_end),
        (beats.settle_end, scene.end),
    ]


def editorial_zoom_scale(index: int, action_peak_scale: float, settle_scale: float) -> float:
    if index == 0:
        return action_peak_scale
    if index == 1:
        return settle_scale
    return max(1.02, settle_scale - 0.01)


def editorial_zoom_smoothing(scene: EditPlanScene, index: int, current: float) -> float:
    baseline = 0.16 if scene.action_class == "card_selection" else 0.14
    return max(current, baseline if index <= 1 else baseline + 0.06)


def editorial_zoom_hold_ratio(scene: EditPlanScene, index: int, current: float) -> float:
    baseline = 0.76 if scene.action_class == "card_selection" else 0.72
    if index == 0:
        return max(current, baseline)
    if index == 1:
        return max(current, baseline + 0.08)
    return max(current, baseline + 0.14)


def premium_zoom_window(
    index: int,
    zoom_count: int,
    scene: EditPlanScene,
    beats: Any,
    focus_end: float,
    settle_end: float,
    zoom: EditPlanZoom,
) -> tuple[float, float]:
    if zoom_count <= 2:
        if index == 0:
            return round(beats.focus_start, 2), focus_end
        return focus_end, settle_end
    windows = [
        (round(scene.start, 2), round(min(beats.focus_start, focus_end - 0.28), 2)),
        (round(beats.focus_start, 2), focus_end),
        (focus_end, settle_end),
        (settle_end, round(scene.end, 2)),
    ]
    start, end = windows[min(index, len(windows) - 1)]
    return round(max(scene.start, min(start, zoom.start)), 2), round(min(scene.end, max(end, zoom.end)), 2)


def premium_zoom_scale(index: int, current: float, peak_scale: float, settle_scale: float) -> float:
    if index == 0:
        return max(current, max(1.04, peak_scale - 0.04))
    if index == 1:
        return max(current, peak_scale)
    if index == 2:
        return max(current, settle_scale)
    return max(1.02, min(current, settle_scale))


def premium_hold_ratio(index: int, current: float) -> float:
    if index == 0:
        return max(current, 0.8)
    if index == 1:
        return max(current, 0.86)
    if index == 2:
        return max(current, 0.9)
    return max(current, 0.92)


def premium_smoothing(index: int, current: float) -> float:
    if index == 0:
        return max(current, 0.2)
    if index == 1:
        return max(current, 0.24)
    if index == 2:
        return max(current, 0.26)
    return max(current, 0.28)


def premium_reason(index: int, current: str) -> str:
    if index == 0:
        return current or "editorial establish move"
    if index == 1:
        return current or "editorial action push"
    if index == 2:
        return "editorial settle hold"
    return "editorial result hold"
