from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

from app.core.config import get_settings


def get_connection() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required. Install backend dependencies again.") from exc

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for project and job persistence.")
    return psycopg.connect(settings.database_url)


@contextmanager
def connection_scope() -> Generator[Any, None, None]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
            transcript jsonb not null default '[]'::jsonb,
            error_message text not null default '',
            created_at timestamptz not null,
            updated_at timestamptz not null
        )
        """,
    )
    cursor.execute(
        """
        create index if not exists idx_projects_user_updated
        on projects (user_id, updated_at desc)
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
