from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanZoom, FocusBox, GuideStepRecord, ProjectRecord, SessionEventRecord
from app.services.event_grounding import focus_box_for_event, normalize_event_timestamp, region_for_box
from app.services.motion_director import offset_for_box
from app.services.walkthrough_windows import action_result_window


def focus_box_area(box: FocusBox) -> float:
    return box.width * box.height


def apply_grounded_focus(
    project: ProjectRecord,
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
) -> tuple[list[EditPlanZoom], list[EditPlanHighlight]]:
    event_focus_box = grounded_event_focus_box(project, primary_event)
    if event_focus_box is None:
        return zooms, highlights
    focus_box = preferred_grounded_focus_box(event_focus_box, zooms, highlights)
    focus_region = region_for_box(focus_box)
    return (
        grounded_zoom_track(step, primary_event, focus_box, focus_region, zooms),
        grounded_highlight_track(step, primary_event, focus_box, focus_region, highlights),
    )


def grounded_event_focus_box(project: ProjectRecord, primary_event: SessionEventRecord | None) -> FocusBox | None:
    session = project.recording_session
    event_focus_box = focus_box_for_event(session, primary_event)
    if event_focus_box is None or primary_event is None or session is None:
        return event_focus_box
    if primary_event.x is None or primary_event.y is None or focus_box_area(event_focus_box) <= 0.035:
        return event_focus_box
    width = max(session.viewport_width, 1)
    height = max(session.viewport_height, 1)
    compact_width = min(max(event_focus_box.width * 0.42, 0.1), 0.16) * width
    compact_height = min(max(event_focus_box.height * 0.36, 0.08), 0.14) * height
    return FocusBox(
        x=round(max(min((float(primary_event.x) - compact_width / 2) / width, 0.94), 0.0), 4),
        y=round(max(min((float(primary_event.y) - compact_height / 2) / height, 0.92), 0.0), 4),
        width=round(compact_width / width, 4),
        height=round(compact_height / height, 4),
    )


def preferred_grounded_focus_box(
    event_focus_box: FocusBox,
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
) -> FocusBox:
    candidates = [event_focus_box]
    candidates.extend(zoom.focus_box for zoom in zooms if zoom.focus_box is not None)
    candidates.extend(highlight.focus_box for highlight in highlights if highlight.focus_box is not None)
    return min(candidates, key=focus_box_area)


def has_usable_zooms(zooms: list[EditPlanZoom]) -> bool:
    return any(
        zoom.focus_box is not None
        and zoom.confidence >= 0.72
        and zoom.end - zoom.start >= 0.4
        and is_supported_zoom_reason(zoom.reason)
        for zoom in zooms
    )


def has_usable_highlights(highlights: list[EditPlanHighlight]) -> bool:
    return any(
        highlight.focus_box is not None
        and highlight.confidence >= 0.84
        and highlight.end - highlight.start >= 0.4
        and highlight.style in {"ambient", "ambient-lift", "soft-glow"}
        for highlight in highlights
    )


def is_supported_zoom_reason(reason: str) -> bool:
    lowered = reason.lower()
    return lowered.startswith("editorial ") or lowered == "calm setup focus"


def grounded_zoom_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    zooms: list[EditPlanZoom],
) -> list[EditPlanZoom]:
    hydrated = [hydrate_grounded_zoom(zoom, event_focus_box, focus_region) for zoom in zooms]
    if has_usable_zooms(hydrated):
        return retimed_grounded_zooms(step, primary_event, hydrated)
    if primary_event is not None:
        return segmented_grounded_zooms(step, primary_event, event_focus_box, focus_region)
    return hydrated or [seed_grounded_zoom(step, event_focus_box, focus_region)]


def grounded_highlight_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    highlights: list[EditPlanHighlight],
) -> list[EditPlanHighlight]:
    hydrated = [hydrate_grounded_highlight(highlight, step, event_focus_box, focus_region) for highlight in highlights]
    if has_usable_highlights(hydrated):
        return retimed_grounded_highlights(step, primary_event, hydrated)
    if primary_event is not None:
        return [segmented_grounded_highlight(step, primary_event, event_focus_box, focus_region)]
    return hydrated or [seed_grounded_highlight(step, event_focus_box, focus_region)]


def retimed_grounded_zooms(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    zooms: list[EditPlanZoom],
) -> list[EditPlanZoom]:
    if primary_event is None or not zooms:
        return zooms
    focus_start, focus_peak_end, settle_end = grounded_focus_windows(step, primary_event)
    if len(zooms) >= 3:
        third_start = round(focus_start + (focus_peak_end - focus_start) * 0.55, 2)
        windows = [
            (focus_start, max(third_start, focus_start + 0.34)),
            (third_start, max(focus_peak_end, third_start + 0.34)),
            (focus_peak_end, max(settle_end, focus_peak_end + 0.34)),
        ]
    else:
        windows = [(focus_start, focus_peak_end), (focus_peak_end, settle_end)]
    retimed: list[EditPlanZoom] = []
    for index, zoom in enumerate(zooms):
        start, end = windows[min(index, len(windows) - 1)]
        retimed.append(zoom.model_copy(update={"start": round(start, 2), "end": round(max(end, start + 0.46), 2)}))
    return retimed


def retimed_grounded_highlights(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    highlights: list[EditPlanHighlight],
) -> list[EditPlanHighlight]:
    if primary_event is None or not highlights:
        return highlights
    focus_start, focus_peak_end, _settle_end = grounded_focus_windows(step, primary_event)
    end = max(focus_peak_end, focus_start + 0.8)
    return [highlights[0].model_copy(update={"start": round(focus_start, 2), "end": round(end, 2)})]


def segmented_grounded_zooms(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> list[EditPlanZoom]:
    focus_start, focus_peak_end, settle_end = grounded_focus_windows(step, primary_event)
    lead_end = max(min(focus_start, step.end), min(step.start + 0.34, focus_start))
    zooms: list[EditPlanZoom] = []
    if lead_end - step.start >= 0.35:
        zooms.append(build_zoom_segment(step.start, lead_end, 1.04, "grounded lead-in", 0.74, event_focus_box, focus_region, 0.35, 0.3, 0.08))
    zooms.append(build_zoom_segment(focus_start, focus_peak_end, 1.24, "grounded action focus", 0.9, event_focus_box, focus_region, 1.0, 0.72, 0.12))
    if settle_end - focus_peak_end >= 0.35:
        zooms.append(build_zoom_segment(focus_peak_end, settle_end, 1.12, "grounded settle hold", 0.82, event_focus_box, focus_region, 0.7, 0.58, 0.14))
    return zooms


def build_zoom_segment(
    start: float,
    end: float,
    scale: float,
    reason: str,
    confidence: float,
    focus_box: FocusBox,
    focus_region: str,
    offset_multiplier: float,
    hold_ratio: float,
    smoothing: float,
) -> EditPlanZoom:
    return EditPlanZoom(
        start=round(start, 2),
        end=round(end, 2),
        scale=scale,
        focus_region=focus_region,
        reason=reason,
        confidence=confidence,
        focus_box=focus_box,
        x_offset=offset_for_box(focus_box, focus_region, axis="x") * offset_multiplier,
        y_offset=offset_for_box(focus_box, focus_region, axis="y") * offset_multiplier,
        hold_ratio=hold_ratio,
        smoothing=smoothing,
    )


def segmented_grounded_highlight(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start = max(step.start, event_time - 0.14)
    focus_peak_end = min(step.end, focus_start + 1.35)
    if focus_peak_end - focus_start < 0.8:
        focus_peak_end = min(step.end, focus_start + 0.8)
    label = step.specific_target_label or step.highlight_label or step.focus_label or step.title
    return EditPlanHighlight(
        start=round(focus_start, 2),
        end=round(focus_peak_end, 2),
        label=label,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.92,
        focus_box=event_focus_box,
        ui_label=label,
    )


def grounded_focus_windows(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> tuple[float, float, float]:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start, focus_peak_end, settle_end = action_result_window(step.start, step.end, event_time, step.narration)
    if focus_peak_end - focus_start < 0.7:
        focus_peak_end = min(step.end, focus_start + 0.7)
    return focus_start, focus_peak_end, settle_end


def hydrate_grounded_zoom(zoom: EditPlanZoom, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    resolved_focus_box = zoom.focus_box or event_focus_box
    return zoom.model_copy(update={
        "focus_box": resolved_focus_box,
        "focus_region": focus_region if zoom.focus_region == "center" else zoom.focus_region,
        "confidence": max(zoom.confidence, 0.82),
        "x_offset": offset_for_box(resolved_focus_box, focus_region, axis="x"),
        "y_offset": offset_for_box(resolved_focus_box, focus_region, axis="y"),
    })


def hydrate_grounded_highlight(
    highlight: EditPlanHighlight,
    step: GuideStepRecord,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    label = step.specific_target_label or step.highlight_label or step.focus_label or highlight.label
    return highlight.model_copy(update={
        "focus_box": highlight.focus_box or event_focus_box,
        "anchor_region": focus_region if highlight.anchor_region == "center" else highlight.anchor_region,
        "confidence": max(highlight.confidence, 0.84),
        "ui_label": label,
        "label": label,
    })


def seed_grounded_zoom(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    return EditPlanZoom(
        start=step.start,
        end=step.end,
        scale=1.08,
        focus_region=focus_region,
        reason="grounded session focus",
        confidence=0.86,
        focus_box=event_focus_box,
        x_offset=offset_for_box(event_focus_box, focus_region, axis="x"),
        y_offset=offset_for_box(event_focus_box, focus_region, axis="y"),
        hold_ratio=0.82,
        smoothing=0.18,
    )


def seed_grounded_highlight(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanHighlight:
    label = step.specific_target_label or step.highlight_label or step.focus_label or step.title
    return EditPlanHighlight(
        start=step.start,
        end=step.end,
        label=label,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.88,
        focus_box=event_focus_box,
        ui_label=label,
    )
