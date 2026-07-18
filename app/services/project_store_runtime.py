from __future__ import annotations

from typing import Any

from app.services.database import connection_scope
from app.services.project_store_errors import StaleProjectAssetError


def execute_project_update(
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


def save_render_outputs_payload(
    payload: tuple[object, ...],
    asset_path: str | None,
) -> tuple[str, tuple[Any, ...], str | None]:
    if asset_path is None:
        return (
            """
            update projects
            set preview_video = %s::jsonb, final_video = %s::jsonb, status = %s, error_message = '', updated_at = %s
            where id = %s and user_id = %s
            """,
            payload,
            None,
        )
    return (
        """
        update projects
        set preview_video = %s::jsonb, final_video = %s::jsonb, status = %s, error_message = '', updated_at = %s
        where id = %s and user_id = %s and asset->>'storage_path' = %s
        """,
        (*payload, asset_path),
        "Project asset was replaced before rendered outputs could be saved.",
    )
