from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.models.projects import (
    AssetRecord,
    BenchmarkReportRecord,
    CreateProjectRequest,
    EditPlanRecord,
    GuideRecord,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    ProjectStatus,
    QualityReportRecord,
    RecordingSessionRecord,
    RenderedVideoRecord,
    TemplateConfigRecord,
    TranscriptSegment,
    VoiceoverRecord,
)
from app.services.database import connection_scope
from app.services.project_store_errors import StaleProjectAssetError
from app.services.project_store_helpers import (
    create_processing_job_params,
    create_project_params,
    has_active_job,
    insert_processing_job_sql,
    project_from_row,
    reset_project_for_asset_params,
    reset_project_for_asset_sql,
)
from app.services.project_store_runtime import execute_project_update, save_render_outputs_payload

PARTIAL_RENDER_OUTPUT_COLUMNS = frozenset({"preview_video"})
class ProjectStore:
    def list_projects(self, user_id: str) -> list[ProjectRecord]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, status, asset, recording_session, transcript, guide, launch_script, edit_plan,
                           template_config, manual_overrides, quality_report, benchmark_report, voiceover, preview_video,
                           error_message, created_at, updated_at
                    from projects
                    where user_id = %s
                    order by updated_at desc
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [project_from_row(row) for row in rows]

    def create_project(self, user_id: str, payload: CreateProjectRequest) -> ProjectRecord:
        now = datetime.now(UTC)
        project = ProjectRecord(
            id=str(uuid4()),
            project_name=payload.project_name,
            status="draft",
            template_config=TemplateConfigRecord(),
            manual_overrides=ManualOverrideRecord(),
            voiceover=VoiceoverRecord(),
            created_at=now,
            updated_at=now,
        )
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into projects (
                        id, user_id, project_name, status, asset, recording_session, transcript, guide, launch_script, edit_plan,
                        template_config, manual_overrides, quality_report, benchmark_report, voiceover, preview_video,
                        error_message, created_at, updated_at
                    )
                    values (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
                    """,
                    create_project_params(project, user_id),
                )
        return project
    def get_project(self, user_id: str, project_id: str) -> ProjectRecord | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, status, asset, recording_session, transcript, guide, launch_script, edit_plan,
                           template_config, manual_overrides, quality_report, benchmark_report, voiceover, preview_video,
                           error_message, created_at, updated_at
                    from projects
                    where id = %s and user_id = %s
                    """,
                    (project_id, user_id),
                )
                row = cursor.fetchone()
        return project_from_row(row) if row else None
    def update_status(self, user_id: str, project_id: str, status: ProjectStatus, error_message: str = "") -> None:
        self._execute_update(
            """
            update projects
            set status = %s, error_message = %s, updated_at = %s
            where id = %s and user_id = %s
            """,
            (status, error_message, datetime.now(UTC), project_id, user_id),
        )

    def update_status_for_asset(
        self,
        user_id: str,
        project_id: str,
        asset_path: str,
        status: ProjectStatus,
        error_message: str = "",
    ) -> None:
        self._execute_update(
            """
            update projects
            set status = %s, error_message = %s, updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (status, error_message, datetime.now(UTC), project_id, user_id, asset_path),
            stale_error_message="Project asset was replaced by a newer upload.",
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
        self.attach_session_asset_and_queue_job(user_id, project_id, asset, None)
    def attach_session_asset_and_queue_job(
        self,
        user_id: str,
        project_id: str,
        asset: AssetRecord,
        recording_session: RecordingSessionRecord | None,
    ) -> None:
        now = datetime.now(UTC)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                if has_active_job(cursor, project_id, asset.storage_path):
                    return
                cursor.execute(
                    reset_project_for_asset_sql(),
                    reset_project_for_asset_params(asset, recording_session, now, project_id, user_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Project not found.")
                cursor.execute(insert_processing_job_sql(), create_processing_job_params(user_id, project_id, asset, now))
    def save_recording_session(
        self,
        user_id: str,
        project_id: str,
        recording_session: RecordingSessionRecord,
        asset_path: str | None = None,
    ) -> None:
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set recording_session = %s::jsonb, updated_at = %s
                where id = %s and user_id = %s
                """,
                (
                    json.dumps(recording_session.model_dump(mode="json")),
                    datetime.now(UTC),
                    project_id,
                    user_id,
                ),
            )
            return
        self._execute_update(
            """
            update projects
            set recording_session = %s::jsonb, updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (
                json.dumps(recording_session.model_dump(mode="json")),
                datetime.now(UTC),
                project_id,
                user_id,
                asset_path,
            ),
            stale_error_message="Project asset was replaced before inferred session data could be saved.",
        )
    def save_transcript(
        self,
        user_id: str,
        project_id: str,
        transcript: list[TranscriptSegment],
        status: ProjectStatus,
        error_message: str = "",
        asset_path: str | None = None,
    ) -> None:
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set transcript = %s::jsonb, status = %s, error_message = %s, updated_at = %s
                where id = %s and user_id = %s
                """,
                (
                    json.dumps([segment.model_dump(mode="json") for segment in transcript]),
                    status,
                    error_message,
                    datetime.now(UTC),
                    project_id,
                    user_id,
                ),
            )
            return
        self._execute_update(
            """
            update projects
            set transcript = %s::jsonb, status = %s, error_message = %s, updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (
                json.dumps([segment.model_dump(mode="json") for segment in transcript]),
                status,
                error_message,
                datetime.now(UTC),
                project_id,
                user_id,
                asset_path,
            ),
            stale_error_message="Project asset was replaced before transcript could be saved.",
        )
    def save_guide(
        self,
        user_id: str,
        project_id: str,
        guide: GuideRecord,
        status: ProjectStatus = "planning",
        asset_path: str | None = None,
    ) -> None:
        query = """
            update projects
            set guide = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s
        """
        params: tuple[object, ...] = (
            json.dumps(guide.model_dump(mode="json")),
            status,
            datetime.now(UTC),
            project_id,
            user_id,
        )
        if asset_path is None:
            self._execute_update(query, params)
            return
        self._execute_update(
            """
            update projects
            set guide = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (
                json.dumps(guide.model_dump(mode="json")),
                status,
                datetime.now(UTC),
                project_id,
                user_id,
                asset_path,
            ),
            stale_error_message="Project asset was replaced before guide grounding could be saved.",
        )
    def save_launch_script(
        self,
        user_id: str,
        project_id: str,
        launch_script: LaunchScriptRecord,
        asset_path: str | None = None,
    ) -> None:
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set launch_script = %s::jsonb, status = %s, error_message = '', updated_at = %s
                where id = %s and user_id = %s
                """,
                (
                    json.dumps(launch_script.model_dump(mode="json")),
                    "planning",
                    datetime.now(UTC),
                    project_id,
                    user_id,
                ),
            )
            return
        self._execute_update(
            """
            update projects
            set launch_script = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (
                json.dumps(launch_script.model_dump(mode="json")),
                "planning",
                datetime.now(UTC),
                project_id,
                user_id,
                asset_path,
            ),
            stale_error_message="Project asset was replaced before the launch script could be saved.",
        )
    def save_edit_plan(
        self,
        user_id: str,
        project_id: str,
        edit_plan: EditPlanRecord,
        asset_path: str | None = None,
    ) -> None:
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set edit_plan = %s::jsonb, status = %s, error_message = '', updated_at = %s
                where id = %s and user_id = %s
                """,
                (
                    json.dumps(edit_plan.model_dump(mode="json")),
                    "rendering",
                    datetime.now(UTC),
                    project_id,
                    user_id,
                ),
            )
            return
        self._execute_update(
            """
            update projects
            set edit_plan = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (
                json.dumps(edit_plan.model_dump(mode="json")),
                "rendering",
                datetime.now(UTC),
                project_id,
                user_id,
                asset_path,
            ),
            stale_error_message="Project asset was replaced before the edit plan could be saved.",
        )
    def save_refined_edit_plan(
        self,
        user_id: str,
        project_id: str,
        edit_plan: EditPlanRecord,
        asset_path: str | None = None,
    ) -> None:
        payload = (
            json.dumps(edit_plan.model_dump(mode="json")),
            datetime.now(UTC),
            project_id,
            user_id,
        )
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set edit_plan = %s::jsonb, updated_at = %s
                where id = %s and user_id = %s
                """,
                payload,
            )
            return
        self._execute_update(
            """
            update projects
            set edit_plan = %s::jsonb, updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (*payload, asset_path),
            stale_error_message="Project asset was replaced before the reviewed edit plan could be saved.",
        )

    def save_render_outputs(
        self,
        user_id: str,
        project_id: str,
        preview_video: RenderedVideoRecord,
        asset_path: str | None = None,
    ) -> None:
        sql, params, stale_error = save_render_outputs_payload(
            (
                json.dumps(preview_video.model_dump(mode="json")),
                "ready",
                datetime.now(UTC),
                project_id,
                user_id,
            ),
            asset_path,
        )
        self._execute_update(sql, params, stale_error_message=stale_error)

    def save_partial_render_output(
        self,
        column_name: str,
        output_label: str,
        user_id: str,
        project_id: str,
        video: RenderedVideoRecord,
        asset_path: str | None,
    ) -> None:
        if column_name not in PARTIAL_RENDER_OUTPUT_COLUMNS:
            raise ValueError(f"Unsupported render output column: {column_name}")
        payload = (
            json.dumps(video.model_dump(mode="json")),
            "rendering",
            datetime.now(UTC),
            project_id,
            user_id,
        )
        if asset_path is None:
            self._execute_update(
                f"""
                update projects
                set {column_name} = %s::jsonb, status = %s, error_message = '', updated_at = %s
                where id = %s and user_id = %s
                """,
                payload,
            )
            return
        self._execute_update(
            f"""
            update projects
            set {column_name} = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (*payload, asset_path),
            stale_error_message=f"Project asset was replaced before the {output_label} could be saved.",
        )

    def save_phase_four_state(
        self,
        user_id: str,
        project_id: str,
        quality_report: QualityReportRecord,
        benchmark_report: BenchmarkReportRecord,
        voiceover: VoiceoverRecord,
        template_config: TemplateConfigRecord,
        manual_overrides: ManualOverrideRecord,
        asset_path: str | None = None,
    ) -> None:
        payload = (
            json.dumps(template_config.model_dump(mode="json")),
            json.dumps(manual_overrides.model_dump(mode="json")),
            json.dumps(quality_report.model_dump(mode="json")),
            json.dumps(benchmark_report.model_dump(mode="json")),
            json.dumps(voiceover.model_dump(mode="json")),
            datetime.now(UTC),
            project_id,
            user_id,
        )
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set template_config = %s::jsonb, manual_overrides = %s::jsonb, quality_report = %s::jsonb,
                    benchmark_report = %s::jsonb, voiceover = %s::jsonb, updated_at = %s
                where id = %s and user_id = %s
                """,
                payload,
            )
            return
        self._execute_update(
            """
            update projects
            set template_config = %s::jsonb, manual_overrides = %s::jsonb, quality_report = %s::jsonb,
                benchmark_report = %s::jsonb, voiceover = %s::jsonb, updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (*payload, asset_path),
            stale_error_message="Project asset was replaced before Phase 4 data could be saved.",
        )

    def _execute_update(
        self,
        sql: str,
        params: tuple[object, ...],
        stale_error_message: str | None = None,
    ) -> None:
        execute_project_update(sql, params, stale_error_message)

project_store = ProjectStore()
