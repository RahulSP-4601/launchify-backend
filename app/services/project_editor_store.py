from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from app.models.project_editor import (
    ProjectEditorConflictError,
    ProjectEditorRevisionRecord,
    ProjectEditorRevisionSummary,
    ProjectEditorState,
    ProjectEditorStateResponse,
)
from app.services.database import connection_scope


class ProjectEditorStore:
    def get_editor_state(self, user_id: str, project_id: str) -> ProjectEditorStateResponse | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select project_id, editor_state, updated_at,
                           (
                               select id
                               from project_editor_revisions revisions
                               where revisions.project_id = project_editor_states.project_id
                                 and revisions.user_id = project_editor_states.user_id
                               order by created_at desc, id desc
                               limit 1
                           ) as head_revision_id
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
        base_revision_id: int | None = None,
    ) -> ProjectEditorStateResponse:
        now = datetime.now(UTC)
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                parent_revision_id = latest_revision_id(cursor, project_id, user_id)
                ensure_revision_base(parent_revision_id, base_revision_id)
                payload = json.dumps(editor_state.model_dump(mode="json"))
                row = upsert_editor_state(cursor, project_id, user_id, payload, now)
                revision_row = insert_editor_revision(cursor, project_id, user_id, parent_revision_id, payload, now)
        if row is None:
            raise RuntimeError("Editor state could not be saved.")
        head_revision_id = parse_optional_int(revision_row[0] if revision_row else None) or parent_revision_id
        return editor_state_from_row((*row, head_revision_id))

    def list_editor_revisions(
        self,
        user_id: str,
        project_id: str,
        limit: int = 30,
    ) -> list[ProjectEditorRevisionSummary]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_id, editor_state, created_at, parent_revision_id
                    from project_editor_revisions
                    where project_id = %s and user_id = %s
                    order by created_at desc, id desc
                    limit %s
                    """,
                    (project_id, user_id, max(1, min(limit, 100))),
                )
                rows = cursor.fetchall()
        return [revision_summary_from_row(row) for row in rows]

    def restore_editor_revision(
        self,
        user_id: str,
        project_id: str,
        revision_id: int,
    ) -> ProjectEditorRevisionRecord:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_id, editor_state, created_at, parent_revision_id
                    from project_editor_revisions
                    where id = %s and project_id = %s and user_id = %s
                    """,
                    (revision_id, project_id, user_id),
                )
                row = cursor.fetchone()
                latest_revision = latest_revision_id(cursor, project_id, user_id)
        if row is None:
            raise ValueError("The requested editor revision was not found.")
        summary = revision_summary_from_row(row)
        editor_state = ProjectEditorState.model_validate(row[2])
        saved = self.save_editor_state(user_id, project_id, editor_state, latest_revision)
        return ProjectEditorRevisionRecord(
            project_id=project_id,
            revision=summary,
            editor_state=saved.editor_state,
            head_revision_id=saved.head_revision_id,
            updated_at=saved.updated_at,
        )

    def clear_editor_state(self, user_id: str, project_id: str) -> None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    delete from project_editor_revisions
                    where project_id = %s and user_id = %s
                    """,
                    (project_id, user_id),
                )
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
        head_revision_id=parse_optional_int(row[3] if len(row) > 3 else None),
    )


def revision_summary_from_row(row: tuple[object, ...]) -> ProjectEditorRevisionSummary:
    editor_state = ProjectEditorState.model_validate(row[2])
    revision_id = row[0] if isinstance(row[0], int) else int(str(row[0]))
    return ProjectEditorRevisionSummary(
        created_at=row[3] if isinstance(row[3], datetime) else datetime.now(UTC),
        id=revision_id,
        parent_revision_id=parse_optional_int(row[4] if len(row) > 4 else None),
        project_id=str(row[1]),
        scene_count=len(editor_state.scenes),
        sequence_version=editor_state.sequence.version if editor_state.sequence else 1,
    )


def ensure_revision_base(latest_id: int | None, base_revision_id: int | None) -> None:
    if latest_id is None and base_revision_id is None:
        return
    if latest_id == base_revision_id:
        return
    raise ProjectEditorConflictError("A newer editor revision exists. Refresh or restore the latest revision before saving.")


def upsert_editor_state(
    cursor: Any,
    project_id: str,
    user_id: str,
    payload: str,
    now: datetime,
) -> tuple[object, ...] | None:
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
        (project_id, user_id, payload, now, now),
    )
    return cast(tuple[object, ...] | None, cursor.fetchone())


def insert_editor_revision(
    cursor: Any,
    project_id: str,
    user_id: str,
    parent_revision_id: int | None,
    payload: str,
    now: datetime,
) -> tuple[object, ...] | None:
    cursor.execute(
        """
        insert into project_editor_revisions (
            project_id, user_id, parent_revision_id, editor_state, created_at
        )
        values (%s, %s, %s, %s::jsonb, %s)
        returning id
        """,
        (project_id, user_id, parent_revision_id, payload, now),
    )
    return cast(tuple[object, ...] | None, cursor.fetchone())


def latest_revision_id(cursor: Any, project_id: str, user_id: str) -> int | None:
    cursor.execute(
        """
        select id
        from project_editor_revisions
        where project_id = %s and user_id = %s
        order by created_at desc, id desc
        limit 1
        """,
        (project_id, user_id),
    )
    row = cursor.fetchone()
    return parse_optional_int(row[0] if row else None)


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    return int(str(value))


project_editor_store = ProjectEditorStore()
