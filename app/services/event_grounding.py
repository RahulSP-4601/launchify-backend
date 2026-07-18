from __future__ import annotations

from app.models.projects import FocusBox, RecordingSessionRecord, SessionEventRecord

DEFAULT_BOX_SIZE = 0.18
MIN_BOX_SIZE = 0.08
MAX_BOX_SIZE = 0.36


def primary_event_for_window(
    recording_session: RecordingSessionRecord | None,
    start: float,
    end: float,
    preferred_label: str = "",
) -> SessionEventRecord | None:
    if recording_session is None or not recording_session.events:
        return None
    candidates = [
        event
        for event in recording_session.events
        if start <= normalize_event_timestamp(event.timestamp) <= end
    ]
    if not candidates:
        return None
    preferred = preferred_label.strip().lower()
    ranked = sorted(
        candidates,
        key=lambda event: event_rank(event, preferred, end),
        reverse=True,
    )
    return ranked[0]


def event_rank(event: SessionEventRecord, preferred_label: str, target_time: float) -> tuple[float, float, float, float]:
    label = (event.target.label or event.target.text or event.target.selector).strip().lower()
    return (
        1.0 if event.type == "click" else 0.92 if event.type == "input" else 0.8,
        1.0 if preferred_label and preferred_label in label else 0.0,
        -abs(normalize_event_timestamp(event.timestamp) - target_time),
        float(len(label)),
    )


def normalize_event_timestamp(value: float) -> float:
    if value > 10_000:
        return round(value / 1000.0, 2)
    return round(max(value, 0.0), 2)


def focus_box_for_event(
    recording_session: RecordingSessionRecord | None,
    event: SessionEventRecord | None,
) -> FocusBox | None:
    if recording_session is None or event is None:
        return None
    width = max(recording_session.viewport_width, 1)
    height = max(recording_session.viewport_height, 1)
    if all(
        value is not None
        for value in (event.target.bbox_x, event.target.bbox_y, event.target.bbox_width, event.target.bbox_height)
    ):
        return normalized_box(
            float(event.target.bbox_x or 0.0),
            float(event.target.bbox_y or 0.0),
            float(event.target.bbox_width or 0.0),
            float(event.target.bbox_height or 0.0),
            width,
            height,
        )
    if event.x is None or event.y is None:
        return None
    pixel_width = width * DEFAULT_BOX_SIZE
    pixel_height = height * DEFAULT_BOX_SIZE
    return normalized_box(
        max(float(event.x) - pixel_width / 2, 0.0),
        max(float(event.y) - pixel_height / 2, 0.0),
        pixel_width,
        pixel_height,
        width,
        height,
    )


def normalized_box(x: float, y: float, width: float, height: float, viewport_width: int, viewport_height: int) -> FocusBox:
    safe_width = max(min(width / viewport_width, MAX_BOX_SIZE), MIN_BOX_SIZE)
    safe_height = max(min(height / viewport_height, MAX_BOX_SIZE), MIN_BOX_SIZE)
    safe_x = min(max(x / viewport_width, 0.0), 1.0 - safe_width)
    safe_y = min(max(y / viewport_height, 0.0), 1.0 - safe_height)
    return FocusBox(
        x=round(safe_x, 4),
        y=round(safe_y, 4),
        width=round(safe_width, 4),
        height=round(safe_height, 4),
    )


def region_for_box(box: FocusBox | None) -> str:
    if box is None:
        return "center"
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    horizontal = "left" if center_x < 0.38 else "right" if center_x > 0.62 else "center"
    vertical = "top" if center_y < 0.38 else "bottom" if center_y > 0.62 else "center"
    if horizontal == "center" and vertical == "center":
        return "center"
    if horizontal == "center":
        return f"{vertical}-center"
    if vertical == "center":
        return f"mid-{horizontal}"
    return f"{vertical}-{horizontal}"
