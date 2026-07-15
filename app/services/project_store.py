from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.models.projects import AssetRecord, CreateProjectRequest, ProjectRecord, ProjectStatus, TranscriptSegment
from app.services.database import connection_scope


class ProjectStore:
    def list_projects(self, user_id: str) -> list[ProjectRecord]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, product_name, product_description, target_audience,
                           video_goal, status, asset, transcript, error_message, created_at, updated_at
                    from projects
                    where user_id = %s
                    order by updated_at desc
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [self._row_to_project(user_id, row) for row in rows]

    def create_project(self, user_id: str, payload: CreateProjectRequest) -> ProjectRecord:
        now = datetime.now(UTC)
        project = ProjectRecord(
            id=str(uuid4()),
            project_name=payload.project_name,
            product_name=payload.product_name,
            product_description=payload.product_description,
            target_audience=payload.target_audience,
            video_goal=payload.video_goal,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into projects (
                        id, user_id, project_name, product_name, product_description, target_audience,
                        video_goal, status, asset, transcript, error_message, created_at, updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
                    """,
                    (
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
                        project.error_message,
                        project.created_at,
                        project.updated_at,
                    ),
                )
        return project

    def get_project(self, user_id: str, project_id: str) -> ProjectRecord | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, product_name, product_description, target_audience,
                           video_goal, status, asset, transcript, error_message, created_at, updated_at
                    from projects
                    where id = %s and user_id = %s
                    """,
                    (project_id, user_id),
                )
                row = cursor.fetchone()
        return self._row_to_project(user_id, row) if row else None

    def update_status(self, user_id: str, project_id: str, status: ProjectStatus, error_message: str = "") -> None:
        self._execute_update(
            """
            update projects
            set status = %s, error_message = %s, updated_at = %s
            where id = %s and user_id = %s
            """,
            (status, error_message, datetime.now(UTC), project_id, user_id),
        )

    def attach_asset(self, user_id: str, project_id: str, asset: AssetRecord) -> None:
        self._execute_update(
            """
            update projects
            set asset = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s
            """,
            (json.dumps(asset.model_dump(mode="json")), "uploading", datetime.now(UTC), project_id, user_id),
        )

    def attach_asset_and_queue_job(self, user_id: str, project_id: str, asset: AssetRecord) -> None:
        now = datetime.now(UTC)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update projects
                    set asset = %s::jsonb, status = %s, error_message = '', updated_at = %s
                    where id = %s and user_id = %s
                    """,
                    (
                        json.dumps(asset.model_dump(mode="json")),
                        "queued",
                        now,
                        project_id,
                        user_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Project not found.")
                cursor.execute(
                    """
                    insert into processing_jobs (
                        id, user_id, project_id, asset_path, content_type, status,
                        attempts, error_message, created_at, updated_at, claimed_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
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
                    ),
                )

    def save_transcript(self, user_id: str, project_id: str, transcript: list[TranscriptSegment]) -> None:
        self._execute_update(
            """
            update projects
            set transcript = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s
            """,
            (
                json.dumps([segment.model_dump(mode="json") for segment in transcript]),
                "ready",
                datetime.now(UTC),
                project_id,
                user_id,
            ),
        )

    def _execute_update(self, sql: str, params: tuple[object, ...]) -> None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                if cursor.rowcount != 1:
                    raise RuntimeError("Project not found.")

    def _row_to_project(self, user_id: str, row: tuple[object, ...]) -> ProjectRecord:
        asset = AssetRecord.model_validate(row[7]) if row[7] is not None else None
        transcript = [TranscriptSegment.model_validate(item) for item in self._as_list(row[8])]
        return ProjectRecord(
            id=str(row[0]),
            project_name=str(row[1]),
            product_name=str(row[2]),
            product_description=str(row[3]),
            target_audience=str(row[4]),
            video_goal=str(row[5]),
            status=cast(Any, row[6]),
            asset=asset,
            transcript=transcript,
            error_message=str(row[9]),
            created_at=cast(datetime, row[10]),
            updated_at=cast(datetime, row[11]),
        )

    def _as_list(self, value: object) -> list[object]:
        if isinstance(value, list):
            return value
        return []


project_store = ProjectStore()
