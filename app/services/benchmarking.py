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
from app.services.reference_style_metrics import (
    cursor_commitment_score,
    highlight_continuity_score,
    reference_style_score,
    result_readability_score,
    zoom_choreography_score,
)
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds


def build_benchmark_report(
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    quality_report: QualityReportRecord,
) -> BenchmarkReportRecord:
    voiceover = project.voiceover or None
    metrics = [
        metric("plan_quality_score", plan_quality_score(edit_plan), "Overall health of the executable preview plan before rendered-output penalties are applied."),
        metric("anchor_coverage", anchor_coverage(edit_plan), "Share of scenes with anchored zoom or highlight."),
        metric("caption_balance", caption_balance(edit_plan), "Share of longer captions balanced into readable lines."),
        metric("motion_confidence", motion_confidence(edit_plan), "Average confidence of approved zoom moves."),
        metric("timing_sync", timing_sync_score(edit_plan), "Share of focus scenes with detected action timing."),
        metric("pacing_balance", editorial_pacing_score(edit_plan), "Whether scene durations stay readable without collapsing meaningful product moments."),
        metric("narrative_continuity", continuity_score(edit_plan), "Whether adjacent scenes preserve a clean action-to-result walkthrough arc."),
        metric("reference_style", reference_style_score(edit_plan), "How closely the edit plan matches premium reference-style action choreography and readability."),
        metric("cursor_commitment", cursor_commitment_metric(edit_plan), "Whether cursor-led scenes visibly approach before they commit to the action."),
        metric("highlight_finesse", highlight_finesse_metric(edit_plan), "Whether highlights feel persistent and elegant instead of abrupt or decorative."),
        metric("result_readability", result_readability_metric(edit_plan), "Whether important post-action states remain on screen long enough to read cleanly."),
        metric("grounding_health", grounding_health(project), "Whether the walkthrough structure is sufficiently grounded for the source duration."),
        metric("review_debt", review_debt_score(project), "How much unresolved manual review debt is still present."),
        metric("quality_gate", quality_gate_score(quality_report), "Quality-report readiness after review and refinement."),
    ]
    if voiceover is not None:
        metrics.append(metric("voiceover_flow", editorial_continuity_score(edit_plan, voiceover), "Whether voiceover lines remain concise and continuous across the full walkthrough."))
    if project.preview_video is not None and project.preview_video.diagnostics is not None:
        metrics.append(metric("preview_duration_alignment", preview_duration_alignment(project, edit_plan), "How closely the stored preview asset duration matches the executable preview plan duration."))
        metrics.append(metric("rendered_preview_score", project.preview_video.diagnostics.rendered_preview_score, "Whether the rendered preview preserved motion, highlight, timing, and sync in the actual exported asset."))
        metrics.append(metric("semantic_consistency", project.preview_video.diagnostics.semantic_consistency_score, "Whether rendered scenes still preserve coherent target, transition, and destination semantics."))
        metrics.append(metric("voiceover_visual_sync", project.preview_video.diagnostics.voiceover_visual_sync_score, "Whether the spoken timing remains aligned to the actual rendered screen coverage."))
        metrics.append(metric("motion_preserved", project.preview_video.diagnostics.motion_preserved_ratio, "How much intended motion survived the actual preview render."))
        metrics.append(metric("highlight_preserved", project.preview_video.diagnostics.highlight_preserved_ratio, "How much intended highlight behavior survived the actual preview render."))
        metrics.append(metric("fallback_penalty", max(0.0, 1.0 - project.preview_video.diagnostics.fallback_severity), "Penalty for destructive fallback profiles appearing in the published preview."))
    overall = round(sum(item.score for item in metrics) / max(len(metrics), 1) * 100)
    return BenchmarkReportRecord(
        overall_score=overall,
        verdict=verdict(overall, project),
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
        return 1.0
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


def cursor_commitment_metric(edit_plan: EditPlanRecord) -> float:
    action_scenes = [scene for scene in edit_plan.scenes if scene.scene_role == "action"]
    if not action_scenes:
        return 1.0
    return sum(cursor_commitment_score(scene) for scene in action_scenes) / len(action_scenes)


def highlight_finesse_metric(edit_plan: EditPlanRecord) -> float:
    focus_scenes = [scene for scene in edit_plan.scenes if scene.camera_mode == "focus"]
    if not focus_scenes:
        return 1.0
    return sum(highlight_continuity_score(scene) * 0.58 + zoom_choreography_score(scene) * 0.42 for scene in focus_scenes) / len(focus_scenes)


def result_readability_metric(edit_plan: EditPlanRecord) -> float:
    candidate_scenes = [scene for scene in edit_plan.scenes if scene.scene_role != "explanation"]
    if not candidate_scenes:
        return 1.0
    return sum(result_readability_score(scene) for scene in candidate_scenes) / len(candidate_scenes)


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


def plan_quality_score(edit_plan: EditPlanRecord) -> float:
    measures = [
        anchor_coverage(edit_plan),
        caption_balance(edit_plan),
        motion_confidence(edit_plan),
        timing_sync_score(edit_plan),
        continuity_score(edit_plan),
        reference_style_score(edit_plan),
    ]
    return round(sum(measures) / len(measures), 3)


def verdict(overall_score: int, project: ProjectRecord) -> str:
    diagnostics = project.preview_video.diagnostics if project.preview_video is not None else None
    if diagnostics is not None and (diagnostics.motion_preserved_ratio < 0.6 or diagnostics.highlight_preserved_ratio < 0.5):
        return "Needs more tuning"
    if overall_score >= 88:
        return "Clueso-class candidate"
    if overall_score >= 76:
        return "Strong but still tuneable"
    return "Needs more tuning"


def preview_duration_alignment(project: ProjectRecord, edit_plan: EditPlanRecord) -> float:
    if project.preview_video is None:
        return 1.0
    expected = max(edit_plan.total_duration_seconds, 0.1)
    actual = max(project.preview_video.duration_seconds, 0.1)
    gap = abs(expected - actual)
    if gap <= 0.4:
        return 1.0
    if gap <= 1.2:
        return 0.82
    if gap <= 2.4:
        return 0.58
    return 0.28
