from __future__ import annotations

from app.models.projects import (
    EditPlanCaption,
    EditPlanHighlight,
    EditPlanRecord,
    EditPlanScene,
    EditPlanZoom,
    ProjectRecord,
    QualityIssueRecord,
    QualityReportRecord,
)
from app.services.quality_assessor import build_quality_report

REFINEMENT_ROUNDS = 2


def refine_edit_plan(project: ProjectRecord, edit_plan: EditPlanRecord) -> tuple[EditPlanRecord, QualityReportRecord]:
    refined_plan = edit_plan
    report = build_quality_report(project, refined_plan)
    for _ in range(REFINEMENT_ROUNDS):
        if report.ready_for_export:
            break
        refined_plan = refined_plan.model_copy(
            update={
                "scenes": [
                    refine_scene(scene, issues_for_scene(report, scene.scene_number))
                    for scene in refined_plan.scenes
                ]
            }
        )
        report = build_quality_report(project, refined_plan)
    return refined_plan, report


def issues_for_scene(report: QualityReportRecord, scene_number: int) -> list[QualityIssueRecord]:
    return [issue for issue in report.issues if issue.scene_number == scene_number]


def refine_scene(
    scene: EditPlanScene,
    issues: list[QualityIssueRecord],
) -> EditPlanScene:
    updated = scene.model_copy(
        update={
            "captions": [refine_caption(caption) for caption in scene.captions],
            "zooms": refined_zooms(scene, issues),
            "highlights": refined_highlights(scene, issues),
            "transition_duration_seconds": tightened_transition(scene, issues),
        }
    )
    if should_stabilize_scene(issues):
        return updated.model_copy(
            update={
                "camera_mode": "static",
                "zooms": [],
                "highlights": anchored_highlights(updated.highlights),
                "action_timestamp": None,
            }
        )
    return updated


def approved_zooms(zooms: list[EditPlanZoom]) -> list[EditPlanZoom]:
    return [
        zoom
        for zoom in zooms
        if zoom.confidence >= 0.5 and (zoom.focus_box is not None or zoom.focus_region != "center")
    ]


def refined_zooms(scene: EditPlanScene, issues: list[QualityIssueRecord]) -> list[EditPlanZoom]:
    zooms = approved_zooms(scene.zooms)
    if has_issue(issues, "scene-wide-zoom"):
        return [shortened_zoom(scene, zoom) for zoom in zooms]
    return zooms


def approved_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [
        highlight
        for highlight in highlights
        if highlight.confidence >= 0.58
        and (highlight.focus_box is not None or highlight.anchor_region != "center")
    ]


def refined_highlights(scene: EditPlanScene, issues: list[QualityIssueRecord]) -> list[EditPlanHighlight]:
    highlights = approved_highlights(scene.highlights)
    if has_issue(issues, "long-highlight") or has_issue(issues, "scene-wide-highlight"):
        return [shortened_highlight(scene, highlight) for highlight in highlights]
    return highlights


def tightened_transition(scene: EditPlanScene, issues: list[QualityIssueRecord]) -> float:
    if has_issue(issues, "slow-transition"):
        return min(scene.transition_duration_seconds, 0.34)
    return scene.transition_duration_seconds


def should_stabilize_scene(issues: list[QualityIssueRecord]) -> bool:
    return any(
        has_issue(issues, code)
        for code in ("missing-focus-motion", "weak-visual-decision")
    )


def anchored_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [highlight for highlight in highlights if highlight.focus_box is not None]


def shortened_zoom(scene: EditPlanScene, zoom: EditPlanZoom) -> EditPlanZoom:
    scene_duration = max(scene.end - scene.start, 0.8)
    duration = min(max(scene_duration * 0.42, 0.8), 1.8)
    start = scene.action_timestamp - 0.28 if scene.action_timestamp is not None else scene.start + 0.12
    bounded_start = max(scene.start, min(start, scene.end - duration))
    return zoom.model_copy(update={"start": round(bounded_start, 2), "end": round(min(scene.end, bounded_start + duration), 2)})


def shortened_highlight(scene: EditPlanScene, highlight: EditPlanHighlight) -> EditPlanHighlight:
    start = scene.action_timestamp - 0.08 if scene.action_timestamp is not None else highlight.start
    bounded_start = max(scene.start, min(start, scene.end - 0.8))
    return highlight.model_copy(update={"start": round(bounded_start, 2), "end": round(min(scene.end, bounded_start + 1.2), 2)})


def has_issue(issues: list[QualityIssueRecord], code: str) -> bool:
    return any(issue.code == code for issue in issues)


def refine_caption(caption: EditPlanCaption) -> EditPlanCaption:
    text = caption.text.replace("  ", " ").strip()
    return caption.model_copy(update={"text": cropped_text(text)})


def cropped_text(text: str) -> str:
    if len(text) <= 84:
        return text
    truncated = text[:84].rsplit(" ", 1)[0].strip()
    return truncated or text[:84].strip()
