from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, EditPlanZoom, EditPlanHighlight, FocusBox, VisualSceneAnalysisRecord


def tracked_focus_box(
    analysis: VisualSceneAnalysisRecord | None,
    *,
    focus_start: float,
    focus_end: float,
    result_anchor: float | None,
    fallback: FocusBox | None = None,
) -> FocusBox | None:
    if analysis is None or not analysis.frames:
        return tightened_box(fallback or analysis_anchor_box(analysis), fallback)
    action_boxes = candidate_boxes(analysis, focus_start - 0.12, focus_end)
    result_boxes = candidate_boxes(
        analysis,
        (result_anchor or focus_end) - 0.12,
        min(analysis.end, (result_anchor or focus_end) + 0.42),
    )
    if action_boxes and result_boxes:
        return tightened_box(blended_box(average_box(action_boxes[:3]), average_box(result_boxes[:3]), result_weight=0.58), fallback)
    if action_boxes:
        return tightened_box(average_box(action_boxes[:3]), fallback)
    if result_boxes:
        return tightened_box(average_box(result_boxes[:3]), fallback)
    return tightened_box(fallback or analysis_anchor_box(analysis), fallback)


def smooth_focus_handoffs(edit_plan: EditPlanRecord) -> EditPlanRecord:
    scenes = [smooth_scene_focus(index, edit_plan.scenes) for index in range(len(edit_plan.scenes))]
    return edit_plan.model_copy(update={"scenes": scenes})


def smooth_scene_focus(index: int, scenes: list[EditPlanScene]) -> EditPlanScene:
    scene = scenes[index]
    current = scene_focus_box(scene)
    previous = nearest_focus_box(scenes, index, step=-1)
    following = nearest_focus_box(scenes, index, step=1)
    replacement = current
    if replacement is None:
        replacement = inherited_focus_box(previous, following)
    elif previous is not None and box_distance(replacement, previous) < 0.12:
        replacement = blended_box(previous, replacement, result_weight=0.62)
    if replacement is None:
        return scene
    return scene.model_copy(
        update={
            "zooms": updated_zooms(scene.zooms, replacement),
            "highlights": updated_highlights(scene.highlights, replacement),
        }
    )


def analysis_anchor_box(analysis: VisualSceneAnalysisRecord | None) -> FocusBox | None:
    if analysis is None:
        return None
    return analysis.click_target_box or analysis.anchor_box or analysis.primary_focus_box or analysis.cursor_box


def scene_focus_box(scene: EditPlanScene) -> FocusBox | None:
    if scene.highlights and scene.highlights[0].focus_box is not None:
        return scene.highlights[0].focus_box
    if scene.zooms and scene.zooms[0].focus_box is not None:
        return scene.zooms[0].focus_box
    return None


def nearest_focus_box(scenes: list[EditPlanScene], start_index: int, *, step: int) -> FocusBox | None:
    index = start_index + step
    while 0 <= index < len(scenes):
        candidate = scene_focus_box(scenes[index])
        if candidate is not None:
            return candidate
        index += step
    return None


def inherited_focus_box(previous: FocusBox | None, following: FocusBox | None) -> FocusBox | None:
    if previous is not None and following is not None:
        return blended_box(previous, following, result_weight=0.5)
    return previous or following


def candidate_boxes(
    analysis: VisualSceneAnalysisRecord,
    start: float,
    end: float,
) -> list[FocusBox]:
    boxes: list[FocusBox] = []
    for frame in analysis.frames:
        if frame.timestamp < start or frame.timestamp > end:
            continue
        for box in (
            frame.click_target_box,
            tightened_cursor_focus(frame.cursor_box),
            frame.dominant_box,
        ):
            if isinstance(box, FocusBox) and plausible_box(box):
                boxes.append(box)
                break
    return deduped_boxes(boxes)


def updated_zooms(zooms: list[EditPlanZoom], focus_box: FocusBox) -> list[EditPlanZoom]:
    return [zoom.model_copy(update={"focus_box": zoom.focus_box or focus_box}) for zoom in zooms]


def updated_highlights(highlights: list[EditPlanHighlight], focus_box: FocusBox) -> list[EditPlanHighlight]:
    return [highlight.model_copy(update={"focus_box": highlight.focus_box or focus_box}) for highlight in highlights]


def deduped_boxes(boxes: list[FocusBox]) -> list[FocusBox]:
    deduped: list[FocusBox] = []
    for box in boxes:
        if any(box_distance(box, existing) < 0.04 for existing in deduped):
            continue
        deduped.append(box)
    return deduped


def compact_box(box: FocusBox | None) -> bool:
    return box is not None and (box.width * box.height) <= 0.18


def plausible_box(box: FocusBox) -> bool:
    area = box.width * box.height
    return 0.002 <= area <= 0.35


def tightened_box(box: FocusBox | None, fallback: FocusBox | None) -> FocusBox | None:
    if box is None:
        return fallback
    area = box.width * box.height
    if area <= 0.18:
        return box
    if fallback is not None and fallback is not box and (fallback.width * fallback.height) <= 0.18:
        return fallback
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    tightened_width = min(max(box.width * 0.42, 0.1), 0.24)
    tightened_height = min(max(box.height * 0.34, 0.08), 0.2)
    return centered_box(center_x, center_y, tightened_width, tightened_height)


def tightened_cursor_focus(box: FocusBox | None) -> FocusBox | None:
    if box is None or not compact_box(box):
        return None
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    return centered_box(center_x, center_y, 0.12, 0.1)


def centered_box(center_x: float, center_y: float, width: float, height: float) -> FocusBox:
    clamped_width = min(max(width, 0.04), 0.32)
    clamped_height = min(max(height, 0.04), 0.28)
    x = min(max(center_x - clamped_width / 2, 0.0), 1.0 - clamped_width)
    y = min(max(center_y - clamped_height / 2, 0.0), 1.0 - clamped_height)
    return FocusBox(
        x=round(x, 4),
        y=round(y, 4),
        width=round(clamped_width, 4),
        height=round(clamped_height, 4),
    )


def average_box(boxes: list[FocusBox]) -> FocusBox:
    count = max(len(boxes), 1)
    return FocusBox(
        x=round(sum(box.x for box in boxes) / count, 4),
        y=round(sum(box.y for box in boxes) / count, 4),
        width=round(sum(box.width for box in boxes) / count, 4),
        height=round(sum(box.height for box in boxes) / count, 4),
    )


def blended_box(action_box: FocusBox, result_box: FocusBox, *, result_weight: float) -> FocusBox:
    action_weight = max(0.0, 1.0 - result_weight)
    total = action_weight + result_weight or 1.0
    return FocusBox(
        x=round((action_box.x * action_weight + result_box.x * result_weight) / total, 4),
        y=round((action_box.y * action_weight + result_box.y * result_weight) / total, 4),
        width=round((action_box.width * action_weight + result_box.width * result_weight) / total, 4),
        height=round((action_box.height * action_weight + result_box.height * result_weight) / total, 4),
    )


def box_distance(left: FocusBox, right: FocusBox) -> float:
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)
