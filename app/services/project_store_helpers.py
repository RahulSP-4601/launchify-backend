from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.models.projects import AssetRecord, ManualOverrideRecord, ProjectRecord, TemplateConfigRecord, VoiceoverRecord


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
        json.dumps([]),
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
        set asset = %s::jsonb, status = %s, transcript = '[]'::jsonb, launch_script = null, edit_plan = null,
            manual_overrides = %s::jsonb, quality_report = null, benchmark_report = null, voiceover = %s::jsonb,
            preview_video = null, final_video = null,
            error_message = '', updated_at = %s
        where id = %s and user_id = %s
    """


def reset_project_for_asset_params(
    asset: AssetRecord,
    now: datetime,
    project_id: str,
    user_id: str,
) -> tuple[object, ...]:
    return (
        json.dumps(asset.model_dump(mode="json")),
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
