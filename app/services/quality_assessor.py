from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, IssueSeverity, ProjectRecord, QualityIssueRecord, QualityReportRecord
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds, session_is_under_grounded


def build_quality_report(project: ProjectRecord, edit_plan: EditPlanRecord) -> QualityReportRecord:
    issues = [
        *caption_issues(edit_plan),
        *motion_issues(edit_plan),
        *highlight_issues(edit_plan),
        *timing_issues(edit_plan),
        *editorial_issues(edit_plan),
        *visual_intelligence_issues(edit_plan),
        *transcript_issues(project, edit_plan),
        *grounding_issues(project),
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
        if scene.scene_role == "explanation" and scene.zooms:
            issues.append(issue("explanation-zoom", "medium", scene.scene_number, "Explanation scene still has camera motion.", "Keep explanation scenes static unless the focus target is very explicit."))
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
        if scene.scene_role != "action" and scene.highlights:
            issues.append(
                issue(
                    "non-action-highlight",
                    "medium",
                    scene.scene_number,
                    "Result or explanation scene still carries an action highlight.",
                    "Drop the highlight or convert the scene back into a real action moment.",
                )
            )
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
        if scene.scene_role == "action" and scene.camera_mode == "static" and not scene.highlights and not setup_scene(scene):
            issues.append(issue("flat-action-scene", "low", scene.scene_number, "Action scene has no visual emphasis left.", "Recover a highlight or add a short anchored focus move for the action."))
    return issues


def editorial_issues(edit_plan: EditPlanRecord) -> list[QualityIssueRecord]:
    issues: list[QualityIssueRecord] = []
    ordered = sorted(edit_plan.scenes, key=lambda scene: (scene.start, scene.scene_number))
    for index, scene in enumerate(ordered):
        if scene.action_class == "auth_action" and max(scene.end - scene.start, 0.0) < 2.9:
            issues.append(issue("short-auth-beat", "medium", scene.scene_number, "Authentication step is trimmed too tightly for a polished walkthrough.", "Keep more setup or result bridge around the auth action."))
        if scene.action_class == "card_selection" and max(scene.end - scene.start, 0.0) < 4.8:
            issues.append(issue("short-selection-beat", "medium", scene.scene_number, "Course or option selection step is trimmed too tightly.", "Preserve more of the transition into the selected state."))
        if index == 0 and "continue with google" in scene.spoken_line.lower():
            issues.append(issue("collapsed-opening-auth", "high", scene.scene_number, "Opening scene is using continuation-login wording instead of the landing CTA.", "Separate the landing CTA beat from the follow-up auth beat."))
        if index > 0 and normalize(scene.spoken_line) == normalize(ordered[index - 1].spoken_line):
            issues.append(issue("duplicate-scene-intent", "medium", scene.scene_number, "Adjacent scenes repeat the same spoken intent.", "Rewrite the later scene so it describes its own action or outcome."))
        if title_is_overliteral(scene.title):
            issues.append(issue("overliteral-title", "medium", scene.scene_number, "Scene title is still too literal or sentence-like for a polished walkthrough.", "Rewrite the title into a short canonical action or setup label."))
        if setup_scene(scene) and scene.layout_mode not in {"screen-only", "dashboard-wide"}:
            issues.append(issue("weak-setup-layout", "low", scene.scene_number, "Setup scene is using a more action-heavy layout than necessary.", "Prefer a calmer setup/result layout for stable choice screens."))
        if spoken_line_is_flat(scene.spoken_line):
            issues.append(issue("flat-voice-line", "low", scene.scene_number, "Voiceover line is functional but not polished.", "Rewrite the spoken line with a clearer action and outcome."))
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


def grounding_issues(project: ProjectRecord) -> list[QualityIssueRecord]:
    duration_seconds = recording_duration_seconds(project.recording_session, project.transcript)
    weak_guide = guide_is_under_grounded(project.guide, duration_seconds)
    weak_session = session_is_under_grounded(project.recording_session, project.transcript)
    if not weak_guide and not weak_session:
        return []
    return [
        issue(
            "under-grounded-walkthrough",
            "high",
            None,
            "The walkthrough structure is under-grounded for the source duration.",
            "Recover more distinct actions before exporting voiceover, trimming, and focus motion.",
        )
    ]


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


def normalize(text: str) -> str:
    return " ".join(text.lower().split()).strip().rstrip(".")


def title_is_overliteral(title: str) -> bool:
    lowered = normalize(title)
    return len(lowered.split()) > 5 or any(token in lowered for token in ("before you", "after you", "once you", "when you"))


def setup_scene(scene: EditPlanScene) -> bool:
    combined = normalize(f"{scene.title} {scene.on_screen_text} {scene.purpose}")
    return scene.action_class in {"button_click", "focus"} and any(
        token in combined for token in ("level", "settings", "preferences", "plan", "workspace", "role", "template", "setup")
    )


def spoken_line_is_flat(line: str) -> bool:
    lowered = normalize(line)
    if not lowered:
        return True
    return any(
        phrase in lowered
        for phrase in (
            "continue to continue",
            "select the option",
            "move forward",
            "continue into setup",
        )
    )
