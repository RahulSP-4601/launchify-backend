from __future__ import annotations

from app.models.projects import EditPlanRecord, IssueSeverity, ProjectRecord, QualityIssueRecord, QualityReportRecord


def build_quality_report(project: ProjectRecord, edit_plan: EditPlanRecord) -> QualityReportRecord:
    issues = [
        *caption_issues(edit_plan),
        *motion_issues(edit_plan),
        *highlight_issues(edit_plan),
        *timing_issues(edit_plan),
        *visual_intelligence_issues(edit_plan),
        *transcript_issues(project, edit_plan),
        *manual_review_issues(project),
    ]
    score = max(0, 100 - sum(issue_penalty(issue.severity) for issue in issues))
    return QualityReportRecord(
        score=score,
        summary=quality_summary(score, issues),
        issues=issues,
        ready_for_export=score >= 82 and not has_blocking_issue(issues),
    )


def caption_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    for scene in edit_plan.scenes:
        if not scene.captions:
            issues.append(issue("missing-captions", "high", scene.scene_number, "Scene has no captions.", "Regenerate or add a caption line for this scene."))
            continue
        if any(len(caption.text) > 90 for caption in scene.captions):
            issues.append(issue("long-captions", "medium", scene.scene_number, "Caption line is too long for clean reading.", "Split the line into shorter caption chunks."))
        if any("\n" not in caption.text and len(caption.text) > 40 for caption in scene.captions):
            issues.append(issue("flat-captions", "low", scene.scene_number, "Long caption line is not visually balanced.", "Use a balanced two-line subtitle layout."))
    return issues


def motion_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    for scene in edit_plan.scenes:
        duration = max(scene.end - scene.start, 0.0)
        if duration >= 4.0 and not scene.zooms and scene.camera_mode == "focus":
            issues.append(issue("missing-focus-motion", "medium", scene.scene_number, "Scene is marked as focus but has no zoom move.", "Add a zoom or switch the camera mode to static."))
        if duration >= 3.0 and any(zoom.end - zoom.start >= duration * 0.85 for zoom in scene.zooms):
            issues.append(issue("scene-wide-zoom", "medium", scene.scene_number, "Zoom spans nearly the full scene, so the motion can feel static.", "Split the zoom into shorter action-led focus moves."))
        if any(zoom.confidence < 0.45 for zoom in scene.zooms):
            issues.append(issue("low-confidence-zoom", "medium", scene.scene_number, "Zoom confidence is weak for this scene.", "Review the focus area or disable the zoom."))
        if any(zoom.focus_box is None and zoom.focus_region == "center" for zoom in scene.zooms):
            issues.append(issue("unanchored-zoom", "medium", scene.scene_number, "Zoom is not anchored to a detected UI box.", "Anchor the zoom to a real UI region or keep the frame static."))
        elif any(zoom.focus_box is None for zoom in scene.zooms):
            issues.append(issue("region-anchored-zoom", "low", scene.scene_number, "Zoom is guided by a transcript-inferred screen region instead of a detected UI box.", "Confirm the framing manually if the scene needs more precise focus."))
    return issues


def highlight_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    for scene in edit_plan.scenes:
        duration = max(scene.end - scene.start, 0.0)
        if any(highlight.end - highlight.start > 1.8 for highlight in scene.highlights):
            issues.append(issue("long-highlight", "medium", scene.scene_number, "Highlight lingers too long and stops feeling tied to a real interaction.", "Shorten the highlight to the action moment."))
        if duration >= 3.0 and any(highlight.end - highlight.start >= duration * 0.85 for highlight in scene.highlights):
            issues.append(issue("scene-wide-highlight", "medium", scene.scene_number, "Highlight spans nearly the full scene, which weakens its meaning.", "Anchor the highlight to a short action window instead of the whole scene."))
        if scene.highlights and all(highlight.focus_box is None and highlight.anchor_region == "center" for highlight in scene.highlights):
            issues.append(issue("unanchored-highlight", "low", scene.scene_number, "Highlight is not anchored to a detected UI target.", "Confirm the highlight manually or improve visual detection."))
    return issues


def visual_intelligence_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    for scene in edit_plan.scenes:
        if scene.camera_mode == "focus" and not scene.highlights and not scene.zooms:
            issues.append(issue("weak-visual-decision", "medium", scene.scene_number, "Focus scene has no strong visual action left after refinement.", "Keep the scene static or improve cursor/click detection."))
    return issues


def timing_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    for scene in edit_plan.scenes:
        if scene.action_timestamp is None and scene.camera_mode == "focus":
            issues.append(issue("missing-action-timing", "low", scene.scene_number, "Focus scene has no detected action timestamp.", "Review cursor/click timing or keep this scene static."))
        if scene.transition_duration_seconds > 0.45:
            issues.append(issue("slow-transition", "low", scene.scene_number, "Transition duration is long for a product walkthrough.", "Shorten the transition to keep pacing tight."))
    return issues


def transcript_issues(project: ProjectRecord, edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    if project.transcript or not edit_plan.scenes:
        return []
    return [issue("missing-transcript", "high", None, "No usable transcript was found for the project.", "Upload a clearer recording or add manual script guidance.")]


def manual_review_issues(project: ProjectRecord) -> list[QualityIssueRecord]:
    if project.manual_overrides is None:
        return []
    issues: list[QualityIssueRecord] = []
    for scene in project.manual_overrides.scenes:
        if scene.notes.strip():
            issues.append(
                issue(
                    "manual-review-note",
                    "low",
                    scene.scene_number,
                    "A reviewer left a manual correction note for this scene.",
                    "Resolve the reviewer note or clear it once the scene looks correct.",
                )
            )
    return issues


def issue_penalty(severity: str) -> int:
    return {"high": 22, "medium": 10, "low": 4}[severity]


def quality_summary(score: int, issues: list[QualityIssueRecord]) -> str:
    if not issues:
        return "The edit plan cleared the Phase 4 quality checks and is ready for export."
    if score >= 85:
        return "The output is strong, but a few refinements would improve polish."
    if score >= 70:
        return "The output is usable, but it still needs polish before it feels premium."
    return "The output needs more refinement before it matches a Clueso-class result."


def has_blocking_issue(issues: list[QualityIssueRecord]) -> bool:
    return any(issue.severity == "high" for issue in issues)


def issue(
    code: str,
    severity: IssueSeverity,
    scene_number: int | None,
    message: str,
    suggestion: str,
) -> QualityIssueRecord:
    return QualityIssueRecord(
        code=code,
        severity=severity,
        scene_number=scene_number,
        message=message,
        suggestion=suggestion,
    )
