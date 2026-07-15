from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib import error, request

from app.core.config import get_settings
from app.models.projects import (
    EditPlanRecord,
    EditPlanScene,
    IssueSeverity,
    ProjectRecord,
    QualityIssueRecord,
    QualityReportRecord,
)
from app.services.caption_designer import balanced_break
from app.services.quality_assessor import build_quality_report
from app.services.script_writer import describe_transport_error, openai_headers
from app.services.video_frames import ExtractedFrame, extract_scene_frames
from app.services.vision_analyzer import data_url

INTRO_OFFSET_SECONDS = 1.8


@dataclass(frozen=True)
class ReviewIssue:
    scene_number: int
    code: str
    severity: str
    message: str
    action: str


def refine_from_preview(
    project: ProjectRecord,
    preview_video_path: Path,
) -> tuple[EditPlanRecord, QualityReportRecord]:
    edit_plan = require_edit_plan(project)
    issues = review_issues(project, preview_video_path)
    if not issues:
        return edit_plan, build_quality_report(project, edit_plan)
    refined_plan = apply_review_actions(edit_plan, issues)
    quality_report = report_with_review(project, refined_plan, issues)
    return refined_plan, quality_report


def require_edit_plan(project: ProjectRecord) -> EditPlanRecord:
    if project.edit_plan is None:
        raise RuntimeError("Edit plan is required before preview review.")
    return project.edit_plan


def review_issues(project: ProjectRecord, preview_video_path: Path) -> list[ReviewIssue]:
    if not review_available():
        return []
    extracted = extracted_scene_frames(project, preview_video_path)
    return [
        issue
        for scene_number, frames in extracted.items()
        for issue in scene_review(project, scene_number, frames)
    ]


def review_available() -> bool:
    settings = get_settings()
    return bool(settings.openai_api_key)


def extracted_scene_frames(
    project: ProjectRecord,
    preview_video_path: Path,
) -> dict[int, list[ExtractedFrame]]:
    edit_plan = require_edit_plan(project)
    scene_numbers = [scene.scene_number for scene in edit_plan.scenes]
    scene_ranges = [(scene.start + INTRO_OFFSET_SECONDS, scene.end + INTRO_OFFSET_SECONDS) for scene in edit_plan.scenes]
    output_dir = preview_video_path.parent / "review-frames"
    output_dir.mkdir(exist_ok=True)
    return extract_scene_frames(preview_video_path, scene_numbers, scene_ranges, output_dir)


def scene_review(
    project: ProjectRecord,
    scene_number: int,
    frames: list[ExtractedFrame],
) -> list[ReviewIssue]:
    scene = next(scene for scene in require_edit_plan(project).scenes if scene.scene_number == scene_number)
    payload = request_review(scene, frames)
    issues = payload.get("issues", [])
    if not isinstance(issues, list):
        return []
    return [review_issue(scene_number, item) for item in issues if isinstance(item, dict)]


def request_review(
    scene: EditPlanScene,
    frames: list[ExtractedFrame],
) -> dict[str, object]:
    request_payload = {
        "model": get_settings().openai_vision_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": review_system_prompt()},
            {"role": "user", "content": [review_text(scene), *review_images(frames)]},
        ],
    }
    api_request = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers=openai_headers(get_settings().openai_api_key),
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI render review failed: {detail}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI render review failed: {describe_transport_error(exc)}") from exc
    return parse_review_payload(payload)


def review_system_prompt() -> str:
    return (
        "You review AI-generated product video frames for premium quality. "
        "Return JSON with key issues containing an array of objects. "
        "Each object must contain code, severity, message, action. "
        "Allowed actions: keep, soften_zoom, remove_zoom, remove_highlight, tighten_caption. "
        "Only report real visible quality issues such as over-zooming, poor framing, subtitle crowding, "
        "or distracting highlight placement."
    )


def review_text(scene: EditPlanScene) -> dict[str, object]:
    return {
        "type": "text",
        "text": (
            f"Scene number: {scene.scene_number}\n"
            f"Purpose: {scene.purpose}\n"
            f"Spoken line: {scene.spoken_line}\n"
            f"On-screen text: {scene.on_screen_text}\n"
            "Review whether the framing feels premium, whether captions feel crowded, and whether highlights are distracting."
        ),
    }


def review_images(frames: list[ExtractedFrame]) -> list[dict[str, object]]:
    return [{"type": "image_url", "image_url": {"url": data_url(frame.image_path)}} for frame in frames]


def parse_review_payload(payload: dict[str, object]) -> dict[str, object]:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        return {"issues": []}
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(content, str) or not content.strip():
        return {"issues": []}
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else {"issues": []}


def review_issue(scene_number: int, payload: dict[str, object]) -> ReviewIssue:
    return ReviewIssue(
        scene_number=scene_number,
        code=str(payload.get("code", "review-note")),
        severity=str(payload.get("severity", "low")),
        message=str(payload.get("message", "Render review flagged a scene-quality issue.")),
        action=str(payload.get("action", "keep")),
    )


def apply_review_actions(edit_plan: EditPlanRecord, issues: list[ReviewIssue]) -> EditPlanRecord:
    issues_by_scene = {issue.scene_number: [item for item in issues if item.scene_number == issue.scene_number] for issue in issues}
    scenes = [apply_scene_actions(scene, issues_by_scene.get(scene.scene_number, [])) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def apply_scene_actions(
    scene: EditPlanScene,
    issues: list[ReviewIssue],
) -> EditPlanScene:
    updated_scene = scene
    for issue in issues:
        if issue.action == "soften_zoom":
            updated_scene = updated_scene.model_copy(
                update={"zooms": [zoom.model_copy(update={"scale": max(1.02, round(zoom.scale - 0.08, 2)), "smoothing": min(0.95, zoom.smoothing + 0.1)}) for zoom in updated_scene.zooms]}
            )
        elif issue.action == "remove_zoom":
            updated_scene = updated_scene.model_copy(update={"zooms": [], "camera_mode": "static"})
        elif issue.action == "remove_highlight":
            updated_scene = updated_scene.model_copy(update={"highlights": []})
        elif issue.action == "tighten_caption":
            updated_scene = updated_scene.model_copy(update={"captions": [caption.model_copy(update={"text": balanced_break(caption.text[:64].strip())}) for caption in updated_scene.captions]})
    return updated_scene


def report_with_review(
    project: ProjectRecord,
    refined_plan: EditPlanRecord,
    issues: list[ReviewIssue],
) -> QualityReportRecord:
    report = build_quality_report(project, refined_plan)
    review_notes = [quality_issue(issue) for issue in issues if issue.action != "keep"]
    return report.model_copy(update={"issues": [*report.issues, *review_notes], "ready_for_export": report.ready_for_export and not blocking_review_issue(review_notes)})


def quality_issue(issue: ReviewIssue) -> QualityIssueRecord:
    return QualityIssueRecord(
        code=issue.code,
        severity=issue_severity(issue.severity),
        scene_number=issue.scene_number,
        message=issue.message,
        suggestion=f"Render-review action applied: {issue.action}.",
    )


def blocking_review_issue(issues: list[QualityIssueRecord]) -> bool:
    return any(issue.severity == "high" for issue in issues)


def issue_severity(value: str) -> IssueSeverity:
    if value == "high":
        return "high"
    if value == "medium":
        return "medium"
    return "low"
