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
            "zooms": approved_zooms(scene.zooms),
            "highlights": approved_highlights(scene.highlights),
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
    return [zoom for zoom in zooms if zoom.confidence >= 0.5 and zoom.focus_box is not None]


def approved_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [highlight for highlight in highlights if highlight.confidence >= 0.58 and highlight.focus_box is not None]


def tightened_transition(scene: EditPlanScene, issues: list[QualityIssueRecord]) -> float:
    if has_issue(issues, "slow-transition"):
        return min(scene.transition_duration_seconds, 0.34)
    return scene.transition_duration_seconds


def should_stabilize_scene(issues: list[QualityIssueRecord]) -> bool:
    return any(
        has_issue(issues, code)
        for code in ("missing-focus-motion", "weak-visual-decision", "missing-action-timing")
    )


def anchored_highlights(highlights: list[EditPlanHighlight]) -> list[EditPlanHighlight]:
    return [highlight for highlight in highlights if highlight.focus_box is not None]


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
