from __future__ import annotations

import logging

from app.core.config import get_settings
from app.models.projects import (
    BenchmarkReportRecord,
    EditPlanRecord,
    ManualOverrideRecord,
    ProjectRecord,
    QualityReportRecord,
    TemplateConfigRecord,
    VoiceoverMode,
    VoiceoverRecord,
)
from app.services.benchmarking import build_benchmark_report
from app.services.override_manager import apply_manual_overrides
from app.services.preview_delivery import preview_delivery_diagnostics
from app.services.quality_assessor import build_quality_report
from app.services.refinement_loop import refine_edit_plan
from app.services.voiceover import build_voiceover, refresh_voiceover_asset
from app.services.voiceover_script_polish import polish_voiceover_script
from app.services.voiceover_timeline import reconcile_edit_plan_to_voiceover
from app.services.walkthrough_guardrails import recording_duration_seconds

logger = logging.getLogger(__name__)


def apply_phase_four_defaults(
    user_id: str,
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
) -> tuple[EditPlanRecord, QualityReportRecord, BenchmarkReportRecord, VoiceoverRecord, TemplateConfigRecord, ManualOverrideRecord]:
    template_config = project.template_config or TemplateConfigRecord()
    manual_overrides = project.manual_overrides or ManualOverrideRecord()
    refined_edit_plan, quality_report = polished_plan_and_report(project, edit_plan, manual_overrides)
    default_voiceover_mode: VoiceoverMode = "voiceover"
    voiceover = voiceover_for_project(user_id, project, refined_edit_plan, default_voiceover_mode)
    reconciled_edit_plan, reconciled_voiceover = reconcile_edit_plan_to_voiceover(refined_edit_plan, voiceover)
    reconciled_voiceover = finalized_voiceover(user_id, project.id, reconciled_voiceover)
    benchmark_report = build_benchmark_report(project, reconciled_edit_plan, quality_report)
    diagnostics = preview_delivery_diagnostics(reconciled_edit_plan, reconciled_voiceover)
    logger.info(
        "Phase 4 summary for project %s: voiceover_status=%s, voiceover_audio=%s, zoom_scenes=%s, highlight_scenes=%s, quality_score=%s, benchmark_score=%s, dynamic_scene_ratio=%.2f, highlight_scene_ratio=%.2f, voiced_scene_ratio=%.2f, avg_voice_words=%.2f, delivery_issues=%s.",
        project.id,
        reconciled_voiceover.status,
        bool(reconciled_voiceover.audio_storage_path or any(clip.audio_storage_path for clip in reconciled_voiceover.clips)),
        sum(1 for scene in reconciled_edit_plan.scenes if scene.zooms),
        sum(1 for scene in reconciled_edit_plan.scenes if scene.highlights),
        quality_report.score,
        benchmark_report.overall_score,
        diagnostics.dynamic_scene_ratio,
        diagnostics.highlight_scene_ratio,
        diagnostics.voiced_scene_ratio,
        diagnostics.avg_voice_words,
        list(diagnostics.issues),
    )
    return reconciled_edit_plan, quality_report, benchmark_report, reconciled_voiceover, template_config, manual_overrides


def apply_phase_four_update(
    user_id: str,
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    template_config: TemplateConfigRecord,
    manual_overrides: ManualOverrideRecord,
    voiceover_mode: VoiceoverMode,
) -> tuple[EditPlanRecord, QualityReportRecord, BenchmarkReportRecord, VoiceoverRecord]:
    updated_project = project.model_copy(
        update={
            "template_config": template_config,
            "manual_overrides": manual_overrides,
        }
    )
    refined_edit_plan, quality_report = polished_plan_and_report(updated_project, edit_plan, manual_overrides)
    voiceover = voiceover_for_project(user_id, updated_project, refined_edit_plan, voiceover_mode)
    reconciled_edit_plan, reconciled_voiceover = reconcile_edit_plan_to_voiceover(refined_edit_plan, voiceover)
    reconciled_voiceover = finalized_voiceover(user_id, updated_project.id, reconciled_voiceover)
    benchmark_report = build_benchmark_report(updated_project, reconciled_edit_plan, quality_report)
    return reconciled_edit_plan, quality_report, benchmark_report, reconciled_voiceover


def voiceover_for_project(
    user_id: str,
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    voiceover_mode: VoiceoverMode,
) -> VoiceoverRecord:
    if project.guide is None and project.launch_script is None:
        return VoiceoverRecord(mode=voiceover_mode, status="disabled")
    return build_voiceover(
        user_id,
        project.id,
        voiceover_mode,
        source_duration_seconds=recording_duration_seconds(project.recording_session, project.transcript),
        guide=project.guide,
        launch_script=project.launch_script,
        edit_plan=edit_plan,
        recording_session=project.recording_session,
        transcript=project.transcript,
    )


def finalized_voiceover(user_id: str, project_id: str, voiceover: VoiceoverRecord) -> VoiceoverRecord:
    if not any(clip.audio_storage_path for clip in voiceover.clips):
        return voiceover
    try:
        return refresh_voiceover_asset(user_id, project_id, voiceover)
    except Exception:
        logger.exception("Voiceover refresh failed for project %s; continuing with clip-based audio.", project_id)
        return voiceover


def refined_plan_and_report(
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    manual_overrides: ManualOverrideRecord,
) -> tuple[EditPlanRecord, QualityReportRecord]:
    overridden_plan = apply_manual_overrides(edit_plan, manual_overrides)
    return refine_edit_plan(project, overridden_plan)


def polished_plan_and_report(
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    manual_overrides: ManualOverrideRecord,
) -> tuple[EditPlanRecord, QualityReportRecord]:
    refined_plan, quality_report = refined_plan_and_report(project, edit_plan, manual_overrides)
    polished_plan = polish_voiceover_script(project, refined_plan)
    if polished_plan == refined_plan:
        return polished_plan, quality_report
    return polished_plan, build_quality_report(project, polished_plan)
