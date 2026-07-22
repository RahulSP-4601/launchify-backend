from __future__ import annotations

import json
from datetime import UTC, datetime

from app.models.project_editor import ProjectEditorState, ProjectEditorStateResponse
from app.services.database import connection_scope


class ProjectEditorStore:
    def get_editor_state(self, user_id: str, project_id: str) -> ProjectEditorStateResponse | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select project_id, editor_state, updated_at
                    from project_editor_states
                    where project_id = %s and user_id = %s
                    """,
                    (project_id, user_id),
                )
                row = cursor.fetchone()
        return editor_state_from_row(row) if row else None

    def save_editor_state(
        self,
        user_id: str,
        project_id: str,
        editor_state: ProjectEditorState,
    ) -> ProjectEditorStateResponse:
        now = datetime.now(UTC)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    insert into project_editor_states (
                        project_id, user_id, editor_state, created_at, updated_at
                    )
                    values (%s, %s, %s::jsonb, %s, %s)
                    on conflict (project_id) do update
                    set editor_state = excluded.editor_state,
                        user_id = excluded.user_id,
                        updated_at = excluded.updated_at
                    returning project_id, editor_state, updated_at
                    """,
                    (
                        project_id,
                        user_id,
                        json.dumps(editor_state.model_dump(mode="json")),
                        now,
                        now,
                    ),
                )
                row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Editor state could not be saved.")
        return editor_state_from_row(row)

    def clear_editor_state(self, user_id: str, project_id: str) -> None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    delete from project_editor_states
                    where project_id = %s and user_id = %s
                    """,
                    (project_id, user_id),
                )


def editor_state_from_row(row: tuple[object, ...]) -> ProjectEditorStateResponse:
    return ProjectEditorStateResponse(
        project_id=str(row[0]),
        editor_state=ProjectEditorState.model_validate(row[1]),
        updated_at=row[2] if isinstance(row[2], datetime) else datetime.now(UTC),
    )


project_editor_store = ProjectEditorStore()
