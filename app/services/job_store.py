from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from app.core.config import get_settings
from app.models.projects import ProcessingJobRecord
from app.services.database import connection_scope


class JobStore:
    def create_job(
        self,
        user_id: str,
        project_id: str,
        asset_path: str,
        content_type: str,
    ) -> ProcessingJobRecord:
        now = datetime.now(UTC)
        job = ProcessingJobRecord(
            id=str(uuid4()),
            user_id=user_id,
            project_id=project_id,
            asset_path=asset_path,
            content_type=content_type,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into processing_jobs (
                        id, user_id, project_id, asset_path, content_type, status,
                        attempts, error_message, created_at, updated_at, claimed_at
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        job.id,
                        job.user_id,
                        job.project_id,
                        job.asset_path,
                        job.content_type,
                        job.status,
                        job.attempts,
                        job.error_message,
                        job.created_at,
                        job.updated_at,
                        None,
                    ),
                )
        return job

    def claim_next_job(self) -> ProcessingJobRecord | None:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=get_settings().effective_job_stale_claim_window_seconds)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    with next_job as (
                        select id
                        from processing_jobs
                        where status = 'pending'
                           or (status = 'processing' and claimed_at is not null and claimed_at < %s)
                        order by created_at asc
                        limit 1
                        for update skip locked
                    )
                    update processing_jobs jobs
                    set status = 'processing',
                        attempts = jobs.attempts + 1,
                        updated_at = %s,
                        claimed_at = %s
                    from next_job
                    where jobs.id = next_job.id
                    returning jobs.id, jobs.user_id, jobs.project_id, jobs.asset_path, jobs.content_type,
                              jobs.status, jobs.created_at, jobs.updated_at, jobs.attempts, jobs.error_message
                    """,
                    (stale_before, now, now),
                )
                row = cursor.fetchone()
        return self._row_to_job(row) if row else None

    def get_job(self, job_id: str) -> ProcessingJobRecord | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, user_id, project_id, asset_path, content_type,
                           status, created_at, updated_at, attempts, error_message
                    from processing_jobs
                    where id = %s
                    """,
                    (job_id,),
                )
                row = cursor.fetchone()
        return self._row_to_job(row) if row else None

    def mark_completed(self, job_id: str) -> None:
        self._update_job_status(job_id, "completed", "")

    def mark_failed(self, job_id: str, error_message: str) -> None:
        self._update_job_status(job_id, "failed", error_message)

    def heartbeat(self, job_id: str) -> None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update processing_jobs
                    set updated_at = %s, claimed_at = %s
                    where id = %s and status = 'processing'
                    """,
                    (datetime.now(UTC), datetime.now(UTC), job_id),
                )

    def _update_job_status(self, job_id: str, status: str, error_message: str) -> None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    update processing_jobs
                    set status = %s, error_message = %s, updated_at = %s
                    where id = %s
                    """,
                    (status, error_message, datetime.now(UTC), job_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("Processing job not found.")

    def _row_to_job(self, row: tuple[object, ...]) -> ProcessingJobRecord:
        return ProcessingJobRecord(
            id=str(row[0]),
            user_id=str(row[1]),
            project_id=str(row[2]),
            asset_path=str(row[3]),
            content_type=str(row[4]),
            status=cast(Any, row[5]),
            created_at=cast(datetime, row[6]),
            updated_at=cast(datetime, row[7]),
            attempts=int(cast(Any, row[8])),
            error_message=str(row[9]),
        )


job_store = JobStore()
