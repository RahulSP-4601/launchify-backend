from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from app.models.projects import (
    AssetRecord,
    BenchmarkReportRecord,
    CreateProjectRequest,
    EditPlanRecord,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    ProjectStatus,
    QualityReportRecord,
    RenderedVideoRecord,
    TemplateConfigRecord,
    TranscriptSegment,
    VoiceoverRecord,
)
from app.services.database import connection_scope
from app.services.project_store_helpers import (
    create_processing_job_params,
    create_project_params,
    has_active_job,
    insert_processing_job_sql,
    reset_project_for_asset_params,
    reset_project_for_asset_sql,
)


class StaleProjectAssetError(RuntimeError):
    pass


PARTIAL_RENDER_OUTPUT_COLUMNS = frozenset({"preview_video", "final_video"})


class ProjectStore:
    def list_projects(self, user_id: str) -> list[ProjectRecord]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, product_name, product_description, target_audience,
                           video_goal, status, asset, transcript, launch_script, edit_plan,
                           template_config, manual_overrides, quality_report, benchmark_report, voiceover,
                           preview_video, final_video, error_message, created_at, updated_at
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
                        id, user_id, project_name, product_name, product_description, target_audience,
                        video_goal, status, asset, transcript, launch_script, edit_plan,
                        template_config, manual_overrides, quality_report, benchmark_report, voiceover,
                        preview_video, final_video, error_message, created_at, updated_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
                    """,
                    create_project_params(project, user_id),
                )
        return project

    def get_project(self, user_id: str, project_id: str) -> ProjectRecord | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, product_name, product_description, target_audience,
                           video_goal, status, asset, transcript, launch_script, edit_plan,
                           template_config, manual_overrides, quality_report, benchmark_report, voiceover,
                           preview_video, final_video, error_message, created_at, updated_at
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
        now = datetime.now(UTC)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                if has_active_job(cursor, project_id, asset.storage_path):
                    return
                cursor.execute(reset_project_for_asset_sql(), reset_project_for_asset_params(asset, now, project_id, user_id))
                if cursor.rowcount != 1:
                    raise RuntimeError("Project not found.")
                cursor.execute(insert_processing_job_sql(), create_processing_job_params(user_id, project_id, asset, now))

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
        preview_video: RenderedVideoRecord | None,
        final_video: RenderedVideoRecord,
        asset_path: str | None = None,
    ) -> None:
        self._save_render_outputs_payload(
            user_id,
            project_id,
            (
                json.dumps(preview_video.model_dump(mode="json")) if preview_video is not None else None,
                json.dumps(final_video.model_dump(mode="json")),
                "ready",
                datetime.now(UTC),
                project_id,
                user_id,
            ),
            asset_path,
        )

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

    def _save_render_outputs_payload(
        self,
        user_id: str,
        project_id: str,
        payload: tuple[object, ...],
        asset_path: str | None,
    ) -> None:
        if asset_path is None:
            self._execute_update(
                """
                update projects
                set preview_video = %s::jsonb, final_video = %s::jsonb, status = %s, error_message = '', updated_at = %s
                where id = %s and user_id = %s
                """,
                payload,
            )
            return
        self._execute_update(
            """
            update projects
            set preview_video = %s::jsonb, final_video = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s and asset->>'storage_path' = %s
            """,
            (*payload, asset_path),
            stale_error_message="Project asset was replaced before rendered outputs could be saved.",
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
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                if cursor.rowcount != 1:
                    if stale_error_message is not None:
                        raise StaleProjectAssetError(stale_error_message)
                    raise RuntimeError("Project not found.")

    def _row_to_project(self, user_id: str, row: tuple[object, ...]) -> ProjectRecord:
        asset = AssetRecord.model_validate(row[7]) if row[7] is not None else None
        transcript = [TranscriptSegment.model_validate(item) for item in self._as_list(row[8])]
        launch_script = LaunchScriptRecord.model_validate(row[9]) if row[9] is not None else None
        edit_plan = EditPlanRecord.model_validate(row[10]) if row[10] is not None else None
        template_config = TemplateConfigRecord.model_validate(row[11]) if row[11] is not None else None
        manual_overrides = ManualOverrideRecord.model_validate(row[12]) if row[12] is not None else None
        quality_report = QualityReportRecord.model_validate(row[13]) if row[13] is not None else None
        benchmark_report = BenchmarkReportRecord.model_validate(row[14]) if row[14] is not None else None
        voiceover = VoiceoverRecord.model_validate(row[15]) if row[15] is not None else None
        preview_video = RenderedVideoRecord.model_validate(row[16]) if row[16] is not None else None
        final_video = RenderedVideoRecord.model_validate(row[17]) if row[17] is not None else None
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
            launch_script=launch_script,
            edit_plan=edit_plan,
            template_config=template_config,
            manual_overrides=manual_overrides,
            quality_report=quality_report,
            benchmark_report=benchmark_report,
            voiceover=voiceover,
            preview_video=preview_video,
            final_video=final_video,
            error_message=str(row[18]),
            created_at=cast(datetime, row[19]),
            updated_at=cast(datetime, row[20]),
        )

    def _as_list(self, value: object) -> list[object]:
        return value if isinstance(value, list) else []


project_store = ProjectStore()
