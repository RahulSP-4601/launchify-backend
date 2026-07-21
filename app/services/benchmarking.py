from __future__ import annotations

from app.models.projects import (
    BenchmarkMetricRecord,
    BenchmarkReportRecord,
    EditPlanRecord,
    ProjectRecord,
    QualityReportRecord,
)
from app.services.editorial_flow import scene_contexts
from app.services.preview_delivery import editorial_continuity_score, editorial_pacing_score
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds


def build_benchmark_report(
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    quality_report: QualityReportRecord,
) -> BenchmarkReportRecord:
    voiceover = project.voiceover or None
    metrics = [
        metric("anchor_coverage", anchor_coverage(edit_plan), "Share of scenes with anchored zoom or highlight."),
        metric("caption_balance", caption_balance(edit_plan), "Share of longer captions balanced into readable lines."),
        metric("motion_confidence", motion_confidence(edit_plan), "Average confidence of approved zoom moves."),
        metric("timing_sync", timing_sync_score(edit_plan), "Share of focus scenes with detected action timing."),
        metric("pacing_balance", editorial_pacing_score(edit_plan), "Whether scene durations stay readable without collapsing meaningful product moments."),
        metric("narrative_continuity", continuity_score(edit_plan), "Whether adjacent scenes preserve a clean action-to-result walkthrough arc."),
        metric("grounding_health", grounding_health(project), "Whether the walkthrough structure is sufficiently grounded for the source duration."),
        metric("review_debt", review_debt_score(project), "How much unresolved manual review debt is still present."),
        metric("quality_gate", quality_gate_score(quality_report), "Quality-report readiness after review and refinement."),
    ]
    if voiceover is not None:
        metrics.append(metric("voiceover_flow", editorial_continuity_score(edit_plan, voiceover), "Whether voiceover lines remain concise and continuous across the full walkthrough."))
    overall = round(sum(item.score for item in metrics) / max(len(metrics), 1) * 100)
    return BenchmarkReportRecord(
        overall_score=overall,
        verdict=verdict(overall),
        metrics=metrics,
    )


def metric(name: str, score: float, detail: str) -> BenchmarkMetricRecord:
    return BenchmarkMetricRecord(name=name, score=round(score, 2), detail=detail)


def anchor_coverage(edit_plan: EditPlanRecord) -> float:
    anchored = 0
    for scene in edit_plan.scenes:
        if any(zoom.focus_box is not None for zoom in scene.zooms) or any(highlight.focus_box is not None for highlight in scene.highlights):
            anchored += 1
    return anchored / max(len(edit_plan.scenes), 1)


def caption_balance(edit_plan: EditPlanRecord) -> float:
    captions = [caption for scene in edit_plan.scenes for caption in scene.captions]
    if not captions:
        return 0.0
    balanced = sum(1 for caption in captions if "\n" in caption.text or len(caption.text) <= 36)
    return balanced / len(captions)


def motion_confidence(edit_plan: EditPlanRecord) -> float:
    zooms = [zoom for scene in edit_plan.scenes for zoom in scene.zooms]
    if not zooms:
        return 0.0
    return sum(zoom.confidence for zoom in zooms) / len(zooms)


def timing_sync_score(edit_plan: EditPlanRecord) -> float:
    focus_scenes = [scene for scene in edit_plan.scenes if scene.camera_mode == "focus"]
    if not focus_scenes:
        return 1.0
    timed = sum(1 for scene in focus_scenes if scene.action_timestamp is not None)
    return timed / len(focus_scenes)


def continuity_score(edit_plan: EditPlanRecord) -> float:
    if len(edit_plan.scenes) <= 1:
        return 1.0
    contexts = scene_contexts(edit_plan.scenes)
    scores: list[float] = []
    ordered = sorted(edit_plan.scenes, key=lambda scene: (scene.start, scene.scene_number))
    for scene in ordered:
        context = contexts.get(scene.scene_number)
        if context is None or context.next_scene is None:
            continue
        score = 0.74
        if scene.end <= context.next_scene.start + 0.4:
            score += 0.1
        if scene.scene_role != context.next_scene.scene_role:
            score += 0.08
        if scene.action_class != context.next_scene.action_class:
            score += 0.08
        scores.append(min(score, 1.0))
    return sum(scores) / max(len(scores), 1)


def review_debt_score(project: ProjectRecord) -> float:
    if project.manual_overrides is None or not project.manual_overrides.scenes:
        return 1.0
    noted_scenes = sum(1 for scene in project.manual_overrides.scenes if scene.notes.strip())
    total_scenes = len(project.manual_overrides.scenes)
    return max(0.0, 1 - noted_scenes / max(total_scenes, 1))


def grounding_health(project: ProjectRecord) -> float:
    duration_seconds = recording_duration_seconds(project.recording_session, project.transcript)
    return 0.0 if guide_is_under_grounded(project.guide, duration_seconds) else 1.0


def quality_gate_score(quality_report: QualityReportRecord) -> float:
    readiness_bonus = 0.1 if quality_report.ready_for_export else 0.0
    return min(1.0, quality_report.score / 100 + readiness_bonus)


def verdict(overall_score: int) -> str:
    if overall_score >= 88:
        return "Clueso-class candidate"
    if overall_score >= 76:
        return "Strong but still tuneable"
    return "Needs more tuning"
