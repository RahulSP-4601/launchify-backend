from __future__ import annotations

from app.models.projects import EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, FocusBox, VisualSceneAnalysisRecord
from app.services.canonical_event_scene_builder import source_scene_number
from app.services.cursor_intelligence import CursorJourney, classify_cursor_journey, cursor_approach_timestamp
from app.services.inferred_recording_support import box_area
from app.services.walkthrough_windows import action_result_window

PRE_ZOOM_LEAD = 0.28
HIGHLIGHT_DURATION = 1.2


def sync_edit_plan_timing(
    edit_plan: EditPlanRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None,
) -> EditPlanRecord:
    analyses_by_scene = {analysis.scene_number: analysis for analysis in visual_analyses or []}
    scenes = [
        synced_scene(
            scene,
            analyses_by_scene.get(scene.scene_number) or analyses_by_scene.get(source_scene_number(scene.scene_number)),
            index,
            len(edit_plan.scenes),
        )
        for index, scene in enumerate(edit_plan.scenes)
    ]
    return edit_plan.model_copy(update={"scenes": scenes})


def synced_scene(
    scene: EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    scene_index: int,
    scene_count: int,
) -> EditPlanScene:
    action_time = action_timestamp(scene, analysis)
    result_time = result_timestamp(scene, analysis, action_time)
    journey = cursor_journey(scene, analysis, action_time, result_time)
    approach_time = approach_time_from_journey(scene, journey, analysis, action_time)
    focus_start, focus_end, settle_end = action_result_window(
        scene.start,
        max(scene.end, result_time or scene.end),
        focus_lead_timestamp(journey, approach_time, action_time),
        scene.spoken_line,
        scene_role=scene.scene_role,
        action_class=scene.action_class,
    )
    return scene.model_copy(
        update={
            "action_timestamp": action_time,
            "result_anchor_timestamp": result_time or scene.result_anchor_timestamp,
            "establish_end_timestamp": establish_end(scene, journey, approach_time, focus_start),
            "focus_start_timestamp": focus_start_timestamp(journey, approach_time, focus_start),
            "focus_end_timestamp": focus_end,
            "settle_end_timestamp": settle_timestamp_from_journey(journey, settle_end),
            "readable_hold_seconds": readable_hold(scene, result_time, settle_end),
            "zooms": synced_zooms(scene, journey, action_time, result_time),
            "highlights": synced_highlights(scene, journey, action_time, result_time),
            "transition_style": transition_style(scene, scene_index, scene_count),
            "transition_duration_seconds": transition_duration(scene),
        }
    )


def action_timestamp(scene: EditPlanScene, analysis: VisualSceneAnalysisRecord | None) -> float | None:
    if analysis is None or not analysis.frames:
        return None
    scored_frames = sorted(analysis.frames, key=lambda frame: action_frame_score(frame), reverse=True)
    if not scored_frames:
        return None
    best_time = max(scene.start, min(scene.end, scored_frames[0].timestamp))
    return round(best_time, 2)


def action_frame_score(frame: object) -> float:
    click_target_box = getattr(frame, "click_target_box", None)
    compact_focus = max(0.0, 0.14 - box_area(click_target_box)) if click_target_box is not None else 0.0
    return (
        getattr(frame, "click_confidence", 0.0) * 0.44
        + getattr(frame, "importance_score", 0.0) * 0.24
        + getattr(frame, "diff_score", 0.0) * 0.16
        + compact_focus
    )


def result_timestamp(
    scene: EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
) -> float | None:
    if analysis is None or not analysis.frames or action_time is None:
        return scene.result_anchor_timestamp
    candidates = [frame for frame in analysis.frames if frame.timestamp >= action_time + 0.24]
    if not candidates:
        return scene.result_anchor_timestamp
    ranked = sorted(candidates, key=result_frame_score, reverse=True)
    best = ranked[0]
    if result_frame_score(best) < 0.18:
        return scene.result_anchor_timestamp
    return round(min(max(best.timestamp, action_time), analysis.end), 2)


def result_frame_score(frame: object) -> float:
    click_target_box = getattr(frame, "click_target_box", None)
    dominant_box = getattr(frame, "dominant_box", None)
    compact_focus = max(0.0, 0.12 - box_area(click_target_box)) if click_target_box is not None else 0.0
    state_shift = 0.12 if dominant_box is not None and click_target_box is None else 0.0
    return (
        getattr(frame, "diff_score", 0.0) * 0.36
        + getattr(frame, "importance_score", 0.0) * 0.28
        + getattr(frame, "ocr_confidence", 0.0) * 0.16
        + compact_focus
        + state_shift
    )


def readable_hold(scene: EditPlanScene, result_time: float | None, settle_end: float) -> float:
    anchor = result_time or scene.result_anchor_timestamp or scene.action_timestamp or scene.start
    return round(max(scene.readable_hold_seconds, settle_end - anchor, 0.0), 2)


def approach_timestamp(
    scene: EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
) -> float | None:
    target_box = scene_focus_box(scene, analysis)
    return cursor_approach_timestamp(analysis, target_box, action_time)


def cursor_journey(
    scene: EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
    result_time: float | None,
) -> CursorJourney | None:
    target_box = scene_focus_box(scene, analysis)
    return classify_cursor_journey(analysis, target_box, action_time, result_time)


def approach_time_from_journey(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
) -> float | None:
    if journey is not None and journey.approach_timestamp is not None:
        return journey.approach_timestamp
    return approach_timestamp(scene, analysis, action_time)


def focus_lead_timestamp(
    journey: CursorJourney | None,
    approach_time: float | None,
    action_time: float | None,
) -> float | None:
    if journey is not None and journey.commit_timestamp is not None:
        return journey.commit_timestamp
    return approach_time or action_time


def establish_end(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    approach_time: float | None,
    fallback: float,
) -> float:
    if journey is not None and journey.navigation_timestamp is not None:
        return max(scene.start, journey.navigation_timestamp)
    return approach_time or fallback


def focus_start_timestamp(
    journey: CursorJourney | None,
    approach_time: float | None,
    fallback: float,
) -> float:
    if journey is not None and journey.approach_timestamp is not None:
        return journey.approach_timestamp
    return approach_time or fallback


def settle_timestamp_from_journey(journey: CursorJourney | None, fallback: float) -> float:
    if journey is not None and journey.settle_timestamp is not None:
        return journey.settle_timestamp
    return fallback


def synced_zooms(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    action_time: float | None,
    result_time: float | None,
) -> list[EditPlanZoom]:
    if action_time is None or not scene.zooms:
        return scene.zooms
    if len(scene.zooms) == 1:
        return single_zoom(scene, scene.zooms[0], journey, action_time, result_time)
    return sequence_zooms(scene, journey, action_time, result_time)


def synced_highlights(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    action_time: float | None,
    result_time: float | None,
) -> list[EditPlanHighlight]:
    if action_time is None or not scene.highlights:
        return scene.highlights
    return refined_highlights(scene, journey, action_time, result_time)


def scene_focus_box(scene: EditPlanScene, analysis: VisualSceneAnalysisRecord | None) -> FocusBox | None:
    if scene.highlights and scene.highlights[0].focus_box is not None:
        return scene.highlights[0].focus_box
    if scene.zooms and scene.zooms[0].focus_box is not None:
        return scene.zooms[0].focus_box
    if analysis is None:
        return None
    return analysis.click_target_box or analysis.anchor_box or analysis.primary_focus_box or analysis.cursor_box


def transition_style(scene: EditPlanScene, scene_index: int, scene_count: int) -> str:
    if scene_index == 0:
        return "slide-up"
    if scene_index == scene_count - 1:
        return "fade"
    if scene.camera_mode == "focus":
        return "focus-push"
    return "fade"


def transition_duration(scene: EditPlanScene) -> float:
    if scene.camera_mode == "focus":
        return 0.4
    return 0.28


def single_zoom(
    scene: EditPlanScene,
    zoom: EditPlanZoom,
    journey: CursorJourney | None,
    action_time: float,
    result_time: float | None,
) -> list[EditPlanZoom]:
    start = max(scene.start, (journey.approach_timestamp if journey is not None else None) or action_time - PRE_ZOOM_LEAD)
    end = min(scene.end, max(result_time or action_time + 0.7, start + 0.72))
    return [zoom.model_copy(update={"start": round(start, 2), "end": round(end, 2)})]


def sequence_zooms(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    action_time: float,
    result_time: float | None,
) -> list[EditPlanZoom]:
    anchors = zoom_phase_anchors(scene, journey, action_time, result_time)
    synced: list[EditPlanZoom] = []
    for index, zoom in enumerate(scene.zooms):
        start, end = zoom_bounds_for_index(scene, anchors, index, len(scene.zooms))
        synced.append(zoom.model_copy(update={"start": start, "end": end}))
    return synced


def zoom_phase_anchors(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    action_time: float,
    result_time: float | None,
) -> list[float]:
    establish = max(scene.start, (journey.navigation_timestamp if journey is not None else None) or action_time - 0.52)
    approach = max(establish + 0.26, (journey.approach_timestamp if journey is not None else None) or action_time - 0.24)
    commit = max(approach + 0.24, (journey.commit_timestamp if journey is not None else None) or action_time)
    settle = min(scene.end, max(result_time or action_time + 0.9, commit + 0.42))
    return [round(scene.start, 2), round(establish, 2), round(approach, 2), round(commit, 2), round(settle, 2), round(scene.end, 2)]


def zoom_bounds_for_index(
    scene: EditPlanScene,
    anchors: list[float],
    index: int,
    zoom_count: int,
) -> tuple[float, float]:
    windows = zoom_windows(anchors, zoom_count)
    start, end = windows[min(index, len(windows) - 1)]
    return safe_window(scene, start, end)


def refined_highlights(
    scene: EditPlanScene,
    journey: CursorJourney | None,
    action_time: float,
    result_time: float | None,
) -> list[EditPlanHighlight]:
    start, end, _settle_end = action_result_window(
        scene.start,
        scene.end,
        action_time,
        scene.spoken_line,
        scene_role=scene.scene_role,
        action_class=scene.action_class,
    )
    highlight_windows = distributed_highlight_windows(scene, start, end, action_time, result_time)
    synced: list[EditPlanHighlight] = []
    for index, highlight in enumerate(scene.highlights):
        window_start, window_end = highlight_windows[min(index, len(highlight_windows) - 1)]
        prelude = prelude_highlight(scene, highlight, journey, window_start, action_time)
        primary = primary_highlight(scene, highlight, journey, window_start, window_end, action_time, result_time)
        synced.extend(item for item in (prelude, primary) if item is not None)
    return synced


def prelude_highlight(
    scene: EditPlanScene,
    highlight: EditPlanHighlight,
    journey: CursorJourney | None,
    start: float,
    action_time: float,
) -> EditPlanHighlight | None:
    approach = journey.approach_timestamp if journey is not None else None
    if approach is None or action_time - approach < 0.36 or scene.scene_role != "action":
        return None
    return highlight.model_copy(
        update={
            "start": round(max(scene.start, approach), 2),
            "end": round(min(scene.end, max(start, action_time - 0.08)), 2),
            "style": "ambient" if highlight.style != "ambient" else highlight.style,
        }
    )


def primary_highlight(
    scene: EditPlanScene,
    highlight: EditPlanHighlight,
    journey: CursorJourney | None,
    start: float,
    end: float,
    action_time: float,
    result_time: float | None,
) -> EditPlanHighlight:
    begin = max(scene.start, (journey.commit_timestamp if journey is not None else None) or max(start, action_time - 0.06))
    finish = min(scene.end, max(result_time or end, begin + HIGHLIGHT_DURATION * 0.78))
    return highlight.model_copy(update={"start": round(begin, 2), "end": round(finish, 2)})


def safe_window(scene: EditPlanScene, start: float, end: float) -> tuple[float, float]:
    bounded_start = max(scene.start, start)
    bounded_end = min(scene.end, max(end, bounded_start + 0.44))
    return round(bounded_start, 2), round(bounded_end, 2)


def zoom_windows(anchors: list[float], zoom_count: int) -> list[tuple[float, float]]:
    if zoom_count <= 1:
        return [(anchors[1], anchors[4])]
    if zoom_count == 2:
        return [(anchors[1], anchors[3]), (anchors[3], anchors[4])]
    if zoom_count == 3:
        return [(anchors[1], anchors[2]), (anchors[2], anchors[3]), (anchors[3], anchors[4])]
    return [
        (anchors[0], anchors[1]),
        (anchors[1], anchors[2]),
        (anchors[2], anchors[3]),
        (anchors[3], anchors[4]),
    ]


def distributed_highlight_windows(
    scene: EditPlanScene,
    start: float,
    end: float,
    action_time: float,
    result_time: float | None,
) -> list[tuple[float, float]]:
    if len(scene.highlights) <= 1:
        return [(start, end)]
    if len(scene.highlights) == 2:
        midpoint = min(scene.end, max(action_time + 0.06, start + 0.42))
        return [(start, midpoint), (midpoint, max(result_time or end, midpoint + 0.42))]
    action_anchor = min(scene.end, max(action_time - 0.02, start + 0.28))
    result_anchor = min(scene.end, max(result_time or end, action_anchor + 0.38))
    return [(start, action_anchor), (action_anchor, result_anchor), (result_anchor, scene.end)]
