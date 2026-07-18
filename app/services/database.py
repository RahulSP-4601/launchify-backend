from __future__ import annotations

from contextlib import contextmanager
from queue import LifoQueue
from threading import Lock
from typing import Any, Generator

from app.core.config import get_settings

_POOL_LOCK = Lock()
_CONNECTION_POOL: LifoQueue[Any] | None = None


def _pool() -> LifoQueue[Any]:
    global _CONNECTION_POOL
    if _CONNECTION_POOL is None:
        with _POOL_LOCK:
            if _CONNECTION_POOL is None:
                _CONNECTION_POOL = LifoQueue(maxsize=max(get_settings().database_pool_size, 1))
    return _CONNECTION_POOL


def get_connection() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required. Install backend dependencies again.") from exc

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for project and job persistence.")
    return psycopg.connect(
        settings.database_url,
        connect_timeout=settings.database_connect_timeout_seconds,
        prepare_threshold=None,
    )


def acquire_connection() -> Any:
    pool = _pool()
    try:
        connection = pool.get_nowait()
    except Exception:
        return get_connection()
    if not connection_is_usable(connection):
        close_connection(connection)
        return get_connection()
    return connection


def release_connection(connection: Any) -> None:
    if getattr(connection, "closed", False):
        return
    pool = _pool()
    try:
        pool.put_nowait(connection)
    except Exception:
        close_connection(connection)


def connection_is_usable(connection: Any) -> bool:
    if getattr(connection, "closed", False):
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute("select 1")
            cursor.fetchone()
    except Exception:
        return False
    return True


def close_connection(connection: Any) -> None:
    try:
        connection.close()
    except Exception:
        return


@contextmanager
def connection_scope() -> Generator[Any, None, None]:
    connection = acquire_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        try:
            connection.rollback()
        except Exception:
            connection.close()
        raise
    finally:
        release_connection(connection)


def ensure_schema() -> None:
    with connection_scope() as connection:
        with connection.cursor() as cursor:
            ensure_projects_schema(cursor)
            ensure_jobs_schema(cursor)


def ensure_projects_schema(cursor: Any) -> None:
    cursor.execute(
        """
        create table if not exists projects (
            id text primary key,
            user_id text not null,
            project_name text not null,
            product_name text not null,
            product_description text not null default '',
            target_audience text not null default '',
            video_goal text not null,
            status text not null,
            asset jsonb,
            recording_session jsonb,
            transcript jsonb not null default '[]'::jsonb,
            guide jsonb,
            launch_script jsonb,
            edit_plan jsonb,
            template_config jsonb,
            manual_overrides jsonb,
            quality_report jsonb,
            benchmark_report jsonb,
            voiceover jsonb,
            preview_video jsonb,
            error_message text not null default '',
            created_at timestamptz not null,
            updated_at timestamptz not null
        )
        """,
    )
    cursor.execute("alter table projects add column if not exists recording_session jsonb")
    cursor.execute("alter table projects add column if not exists guide jsonb")
    cursor.execute("alter table projects add column if not exists launch_script jsonb")
    cursor.execute("alter table projects add column if not exists edit_plan jsonb")
    cursor.execute("alter table projects add column if not exists template_config jsonb")
    cursor.execute("alter table projects add column if not exists manual_overrides jsonb")
    cursor.execute("alter table projects add column if not exists quality_report jsonb")
    cursor.execute("alter table projects add column if not exists benchmark_report jsonb")
    cursor.execute("alter table projects add column if not exists voiceover jsonb")
    cursor.execute("alter table projects add column if not exists preview_video jsonb")
    backfill_legacy_final_video(cursor)
    cursor.execute("alter table projects drop column if exists final_video")
    cursor.execute(
        """
        create index if not exists idx_projects_user_updated
        on projects (user_id, updated_at desc)
        """,
    )


def backfill_legacy_final_video(cursor: Any) -> None:
    cursor.execute(
        """
        do $$
        begin
            if exists (
                select 1
                from information_schema.columns
                where table_name = 'projects' and column_name = 'final_video'
            ) then
                update projects
                set preview_video = coalesce(preview_video, final_video)
                where final_video is not null;
            end if;
        end $$;
        """,
    )


def ensure_jobs_schema(cursor: Any) -> None:
    cursor.execute(
        """
        create table if not exists processing_jobs (
            id text primary key,
            user_id text not null,
            project_id text not null references projects(id) on delete cascade,
            asset_path text not null,
            content_type text not null,
            status text not null,
            attempts integer not null default 0,
            error_message text not null default '',
            created_at timestamptz not null,
            updated_at timestamptz not null,
            claimed_at timestamptz
        )
        """,
    )
    cursor.execute(
        """
        create index if not exists idx_processing_jobs_status_created
        on processing_jobs (status, created_at asc)
        """,
    )
