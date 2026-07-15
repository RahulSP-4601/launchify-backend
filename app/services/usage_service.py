from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, cast

from app.services.database import connection_scope, get_connection


def total_rendered_seconds(user_id: str) -> float:
    with connection_scope() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select coalesce(sum(coalesce((final_video->>'duration_seconds')::double precision, 0)), 0)
                from projects
                where user_id = %s and final_video is not null
                """,
                (user_id,),
            )
            row = cursor.fetchone()
    if row is None or row[0] is None:
        return 0.0
    return float(cast(Any, row[0]))


def projected_rendered_seconds(user_id: str, project_id: str, additional_seconds: float) -> float:
    with connection_scope() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select coalesce(sum(coalesce((final_video->>'duration_seconds')::double precision, 0)), 0)
                from projects
                where user_id = %s and id <> %s and final_video is not null
                """,
                (user_id, project_id),
            )
            row = cursor.fetchone()
    used_seconds = 0.0 if row is None or row[0] is None else float(cast(Any, row[0]))
    return used_seconds + additional_seconds


@contextmanager
def usage_lock(user_id: str) -> Generator[None, None, None]:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("select pg_advisory_lock(hashtext(%s))", (user_id,))
        connection.commit()
        yield
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute("select pg_advisory_unlock(hashtext(%s))", (user_id,))
            connection.commit()
        finally:
            connection.close()
