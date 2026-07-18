from __future__ import annotations

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
from app.services.refinement_loop import refine_edit_plan
from app.services.voiceover import build_voiceover


def apply_phase_four_defaults(
    user_id: str,
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
) -> tuple[EditPlanRecord, QualityReportRecord, BenchmarkReportRecord, VoiceoverRecord, TemplateConfigRecord, ManualOverrideRecord]:
    template_config = project.template_config or TemplateConfigRecord()
    manual_overrides = project.manual_overrides or ManualOverrideRecord()
    refined_edit_plan, quality_report = refined_plan_and_report(project, edit_plan, manual_overrides)
    benchmark_report = build_benchmark_report(project, refined_edit_plan, quality_report)
    default_voiceover_mode: VoiceoverMode = "original" if get_settings().fast_pipeline_enabled else "voiceover"
    voiceover = voiceover_for_project(user_id, project, default_voiceover_mode)
    return refined_edit_plan, quality_report, benchmark_report, voiceover, template_config, manual_overrides


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
    refined_edit_plan, quality_report = refined_plan_and_report(updated_project, edit_plan, manual_overrides)
    benchmark_report = build_benchmark_report(updated_project, refined_edit_plan, quality_report)
    voiceover = voiceover_for_project(user_id, updated_project, voiceover_mode)
    return refined_edit_plan, quality_report, benchmark_report, voiceover


def voiceover_for_project(user_id: str, project: ProjectRecord, voiceover_mode: VoiceoverMode) -> VoiceoverRecord:
    if project.launch_script is None:
        return VoiceoverRecord(mode=voiceover_mode, status="disabled")
    return build_voiceover(user_id, project.id, project.launch_script, voiceover_mode)


def refined_plan_and_report(
    project: ProjectRecord,
    edit_plan: EditPlanRecord,
    manual_overrides: ManualOverrideRecord,
) -> tuple[EditPlanRecord, QualityReportRecord]:
    overridden_plan = apply_manual_overrides(edit_plan, manual_overrides)
    return refine_edit_plan(project, overridden_plan)
