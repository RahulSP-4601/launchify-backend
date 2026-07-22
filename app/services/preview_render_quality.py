from __future__ import annotations

from pathlib import Path

from app.models.projects import RenderDiagnosticsRecord
from app.services.preview_execution_semantics import semantic_consistency_score
from app.services.preview_render_report import ProxyPreviewRenderReport, RenderedClipSegment
from app.services.render_runtime_helpers import output_duration_seconds

PROFILE_SEVERITY = {
    "balanced": 0.0,
    "no_spotlight": 0.25,
    "no_motion": 0.45,
    "simple_crop": 0.7,
    "static_frame": 1.0,
}


def build_render_diagnostics(report: ProxyPreviewRenderReport, output_path: Path) -> RenderDiagnosticsRecord:
    manifest = report.manifest
    rendered = report.rendered_clips
    passthrough = is_passthrough_report(report)
    intended_motion = sum(1 for clip in manifest.clips if clip.animated_crop or clip.scene.zooms)
    intended_highlight = sum(1 for clip in manifest.clips if clip.spotlight or clip.scene.highlights)
    kept_motion = sum(1 for clip in rendered if clip.clip.animated_crop or clip.clip.scene.zooms)
    kept_highlight = sum(1 for clip in rendered if clip.clip.spotlight or clip.clip.scene.highlights)
    degraded = sum(1 for clip in rendered if clip.profile_name != "balanced")
    freeze_ratio = ratio(sum(1 for clip in rendered if clip.clip.freeze_frame), len(rendered))
    actual_duration = output_duration_seconds(output_path, fallback=manifest.total_duration_seconds)
    audio_truncation = round(max(manifest.total_duration_seconds - actual_duration, 0.0), 2)
    semantic_score = semantic_consistency_score([clip.clip.scene for clip in rendered] or [clip.scene for clip in manifest.clips])
    sync_score = voiceover_visual_sync_score(manifest.total_duration_seconds, actual_duration, report)
    motion_ratio = preserved_ratio(kept_motion, intended_motion, passthrough)
    highlight_ratio = preserved_ratio(kept_highlight, intended_highlight, passthrough)
    fallback = fallback_severity(rendered, passthrough)
    score = rendered_preview_score(
        motion_ratio=motion_ratio,
        highlight_ratio=highlight_ratio,
        semantic_score=semantic_score,
        sync_score=sync_score,
        fallback_score=fallback,
        audio_truncation=audio_truncation,
    )
    issues = preview_validation_issues(report, actual_duration, semantic_score, sync_score, fallback)
    return RenderDiagnosticsRecord(
        selected_profile=report.selected_profile,
        motion_preserved_ratio=motion_ratio,
        highlight_preserved_ratio=highlight_ratio,
        freeze_frame_ratio=freeze_ratio,
        audio_truncation_seconds=audio_truncation,
        semantic_consistency_score=semantic_score,
        voiceover_visual_sync_score=sync_score,
        fallback_severity=fallback,
        rendered_preview_score=score,
        total_clips=len(rendered),
        degraded_clips=degraded,
        validation_passed=not issues,
        issues=issues,
    )


def preview_validation_issues(
    report: ProxyPreviewRenderReport,
    actual_duration: float,
    semantic_score: float,
    sync_score: float,
    fallback: float,
) -> list[str]:
    issues: list[str] = []
    if abs(report.manifest.total_duration_seconds - actual_duration) > 1.2:
        issues.append("duration_mismatch")
    if semantic_score < 0.62:
        issues.append("semantic_consistency_low")
    if sync_score < 0.62:
        issues.append("voiceover_visual_sync_low")
    if report.selected_profile == "static_frame":
        issues.append("static_frame_publish")
    if is_passthrough_report(report):
        issues.append("passthrough_preview")
    if fallback > 0.72:
        issues.append("fallback_severity_high")
    if report.rendered_clips:
        motion_ratio = ratio(sum(1 for clip in report.rendered_clips if clip.clip.animated_crop or clip.clip.scene.zooms), sum(1 for clip in report.manifest.clips if clip.animated_crop or clip.scene.zooms) or 1)
        highlight_ratio = ratio(sum(1 for clip in report.rendered_clips if clip.clip.spotlight or clip.clip.scene.highlights), sum(1 for clip in report.manifest.clips if clip.spotlight or clip.scene.highlights) or 1)
        if motion_ratio < 0.55:
            issues.append("motion_preservation_low")
        if highlight_ratio < 0.5:
            issues.append("highlight_preservation_low")
    return issues


def should_reject_preview_publish(diagnostics: RenderDiagnosticsRecord) -> bool:
    fatal_issues = {"static_frame_publish"}
    if any(issue in fatal_issues for issue in diagnostics.issues):
        return True
    if diagnostics.voiceover_visual_sync_score < 0.4:
        return True
    if diagnostics.semantic_consistency_score < 0.4:
        return True
    return False
def voiceover_visual_sync_score(
    expected_duration: float,
    actual_duration: float,
    report: ProxyPreviewRenderReport,
) -> float:
    duration_gap = abs(expected_duration - actual_duration)
    scene_fit = ratio(sum(1 for clip in report.manifest.clips if clip.has_voiceover_fit), len(report.manifest.clips) or 1)
    gap_penalty = min(duration_gap / 3.0, 1.0)
    return round(max(0.0, min(scene_fit * 0.75 + (1.0 - gap_penalty) * 0.25, 1.0)), 3)


def fallback_severity(rendered_clips: list[RenderedClipSegment], passthrough: bool) -> float:
    if passthrough:
        return 0.9
    if not rendered_clips:
        return 0.0
    return round(sum(PROFILE_SEVERITY.get(clip.profile_name, 1.0) for clip in rendered_clips) / len(rendered_clips), 3)


def preserved_ratio(value: int, total: int, passthrough: bool) -> float:
    if passthrough:
        return 0.0 if total > 0 else 1.0
    return ratio(value, total) if total else 1.0


def is_passthrough_report(report: ProxyPreviewRenderReport) -> bool:
    return report.selected_profile == "passthrough" or not report.rendered_clips


def rendered_preview_score(
    *,
    motion_ratio: float,
    highlight_ratio: float,
    semantic_score: float,
    sync_score: float,
    fallback_score: float,
    audio_truncation: float,
) -> float:
    score = (
        motion_ratio * 0.22
        + highlight_ratio * 0.18
        + semantic_score * 0.22
        + sync_score * 0.22
        + (1.0 - fallback_score) * 0.12
        + max(0.0, 1.0 - min(audio_truncation / 1.5, 1.0)) * 0.04
    )
    return round(max(0.0, min(score, 1.0)), 3)


def ratio(value: int, total: int) -> float:
    return round(value / max(total, 1), 3)
