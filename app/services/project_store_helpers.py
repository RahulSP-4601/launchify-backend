from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from app.models.projects import (
    AssetRecord,
    BenchmarkReportRecord,
    EditPlanRecord,
    GuideRecord,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    QualityReportRecord,
    RecordingSessionRecord,
    RenderedVideoRecord,
    TemplateConfigRecord,
    TranscriptSegment,
    VoiceoverRecord,
)


def create_project_params(project: ProjectRecord, user_id: str) -> tuple[object, ...]:
    template_config = project.template_config or TemplateConfigRecord()
    manual_overrides = project.manual_overrides or ManualOverrideRecord()
    voiceover = project.voiceover or VoiceoverRecord()
    return (
        project.id,
        user_id,
        project.project_name,
        project.product_name,
        project.product_description,
        project.target_audience,
        project.video_goal,
        project.status,
        None,
        None,
        json.dumps([]),
        None,
        None,
        None,
        json.dumps(template_config.model_dump(mode="json")),
        json.dumps(manual_overrides.model_dump(mode="json")),
        None,
        None,
        json.dumps(voiceover.model_dump(mode="json")),
        None,
        None,
        project.error_message,
        project.created_at,
        project.updated_at,
    )


def has_active_job(cursor: Any, project_id: str, asset_path: str) -> bool:
    cursor.execute(
        """
        select 1
        from processing_jobs
        where project_id = %s and asset_path = %s and status in ('pending', 'processing')
        limit 1
        """,
        (project_id, asset_path),
    )
    return cursor.fetchone() is not None


def reset_project_for_asset_sql() -> str:
    return """
        update projects
        set asset = %s::jsonb, recording_session = %s::jsonb, status = %s, transcript = '[]'::jsonb, guide = null, launch_script = null, edit_plan = null,
            manual_overrides = %s::jsonb, quality_report = null, benchmark_report = null, voiceover = %s::jsonb,
            preview_video = null, final_video = null,
            error_message = '', updated_at = %s
        where id = %s and user_id = %s
    """


def reset_project_for_asset_params(
    asset: AssetRecord,
    recording_session: RecordingSessionRecord | None,
    now: datetime,
    project_id: str,
    user_id: str,
) -> tuple[object, ...]:
    return (
        json.dumps(asset.model_dump(mode="json")),
        json.dumps(recording_session.model_dump(mode="json")) if recording_session is not None else None,
        "queued",
        json.dumps(ManualOverrideRecord().model_dump(mode="json")),
        json.dumps(VoiceoverRecord().model_dump(mode="json")),
        now,
        project_id,
        user_id,
    )


def insert_processing_job_sql() -> str:
    return """
        insert into processing_jobs (
            id, user_id, project_id, asset_path, content_type, status,
            attempts, error_message, created_at, updated_at, claimed_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """


def create_processing_job_params(
    user_id: str,
    project_id: str,
    asset: AssetRecord,
    now: datetime,
) -> tuple[object, ...]:
    return (
        str(uuid4()),
        user_id,
        project_id,
        asset.storage_path,
        asset.content_type,
        "pending",
        0,
        "",
        now,
        now,
        None,
    )


def project_from_row(row: tuple[object, ...]) -> ProjectRecord:
    asset = AssetRecord.model_validate(row[7]) if row[7] is not None else None
    recording_session = RecordingSessionRecord.model_validate(row[8]) if row[8] is not None else None
    transcript = [TranscriptSegment.model_validate(item) for item in as_list(row[9])]
    guide = GuideRecord.model_validate(row[10]) if row[10] is not None else None
    launch_script = LaunchScriptRecord.model_validate(row[11]) if row[11] is not None else None
    edit_plan = EditPlanRecord.model_validate(row[12]) if row[12] is not None else None
    template_config = TemplateConfigRecord.model_validate(row[13]) if row[13] is not None else None
    manual_overrides = ManualOverrideRecord.model_validate(row[14]) if row[14] is not None else None
    quality_report = QualityReportRecord.model_validate(row[15]) if row[15] is not None else None
    benchmark_report = BenchmarkReportRecord.model_validate(row[16]) if row[16] is not None else None
    voiceover = VoiceoverRecord.model_validate(row[17]) if row[17] is not None else None
    preview_video = RenderedVideoRecord.model_validate(row[18]) if row[18] is not None else None
    final_video = RenderedVideoRecord.model_validate(row[19]) if row[19] is not None else None
    return ProjectRecord(
        id=str(row[0]),
        project_name=str(row[1]),
        product_name=str(row[2]),
        product_description=str(row[3]),
        target_audience=str(row[4]),
        video_goal=str(row[5]),
        status=cast(Any, row[6]),
        asset=asset,
        recording_session=recording_session,
        transcript=transcript,
        guide=guide,
        launch_script=launch_script,
        edit_plan=edit_plan,
        template_config=template_config,
        manual_overrides=manual_overrides,
        quality_report=quality_report,
        benchmark_report=benchmark_report,
        voiceover=voiceover,
        preview_video=preview_video,
        final_video=final_video,
        error_message=str(row[20]),
        created_at=cast(datetime, row[21]),
        updated_at=cast(datetime, row[22]),
    )


def as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
