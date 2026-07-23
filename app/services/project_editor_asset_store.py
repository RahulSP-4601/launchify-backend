from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import uuid4

from app.models.project_editor import EditorMediaAssetKind, EditorMediaAssetRecord, EditorMediaAssetSource
from app.models.projects import AssetRecord, ProjectRecord
from app.services.database import connection_scope


class ProjectEditorAssetStore:
    def list_project_assets(self, user_id: str, project: ProjectRecord) -> list[EditorMediaAssetRecord]:
        stored_assets = self._list_stored_assets(user_id, project.id)
        return [
            *synthetic_project_assets(project),
            *stored_assets,
        ]

    def list_workspace_assets(self, user_id: str, project_id: str) -> list[EditorMediaAssetRecord]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, asset, voiceover, created_at, updated_at
                    from projects
                    where user_id = %s and id <> %s
                    order by updated_at desc
                    """,
                    (user_id, project_id),
                )
                project_rows = cursor.fetchall()
                cursor.execute(
                    """
                    select id, project_id, kind, source, title, storage_path, content_type, size_bytes,
                           duration_seconds, source_project_id, created_at, updated_at
                    from project_editor_media_assets
                    where user_id = %s and project_id <> %s
                    order by updated_at desc, created_at desc
                    """,
                    (user_id, project_id),
                )
                asset_rows = cursor.fetchall()
        return [
            *workspace_assets_from_rows(project_rows),
            *[editor_media_asset_from_row(row) for row in asset_rows],
        ]

    def create_uploaded_asset(
        self,
        user_id: str,
        project_id: str,
        asset: AssetRecord,
        media_kind: EditorMediaAssetKind,
        duration_seconds: float | None,
    ) -> EditorMediaAssetRecord:
        return self._insert_asset(
            user_id,
            project_id,
            asset=asset,
            duration_seconds=duration_seconds,
            media_kind=media_kind,
            source="uploaded",
            source_project_id=None,
            title=asset.filename,
        )

    def import_project_asset(
        self,
        user_id: str,
        project_id: str,
        source_project: ProjectRecord,
        asset_id: str | None,
        variant: str,
        duration_seconds: float | None,
    ) -> EditorMediaAssetRecord:
        asset, media_kind, title = imported_asset_parts(
            user_id,
            source_project,
            asset_id,
            variant,
        )
        return self._insert_asset(
            user_id,
            project_id,
            asset=asset,
            duration_seconds=duration_seconds,
            media_kind=media_kind,
            source="imported",
            source_project_id=source_project.id,
            title=title,
        )

    def _list_stored_assets(self, user_id: str, project_id: str) -> list[EditorMediaAssetRecord]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_id, kind, source, title, storage_path, content_type, size_bytes,
                           duration_seconds, source_project_id, created_at, updated_at
                    from project_editor_media_assets
                    where user_id = %s and project_id = %s
                    order by updated_at desc, created_at desc
                    """,
                    (user_id, project_id),
                )
                rows = cursor.fetchall()
        return [editor_media_asset_from_row(row) for row in rows]

    def get_asset(self, user_id: str, asset_id: str) -> EditorMediaAssetRecord | None:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_id, kind, source, title, storage_path, content_type, size_bytes,
                           duration_seconds, source_project_id, created_at, updated_at
                    from project_editor_media_assets
                    where user_id = %s and id = %s
                    limit 1
                    """,
                    (user_id, asset_id),
                )
                row = cursor.fetchone()
        return editor_media_asset_from_row(row) if row else None

    def _insert_asset(
        self,
        user_id: str,
        project_id: str,
        *,
        asset: AssetRecord,
        duration_seconds: float | None,
        media_kind: EditorMediaAssetKind,
        source: EditorMediaAssetSource,
        source_project_id: str | None,
        title: str,
    ) -> EditorMediaAssetRecord:
        record = build_editor_media_asset_record(
            asset,
            duration_seconds,
            media_kind,
            project_id,
            source,
            source_project_id,
            title,
        )
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                insert_editor_media_asset(cursor, record, user_id)
        return record


def build_editor_media_asset_record(
    asset: AssetRecord,
    duration_seconds: float | None,
    media_kind: EditorMediaAssetKind,
    project_id: str,
    source: EditorMediaAssetSource,
    source_project_id: str | None,
    title: str,
) -> EditorMediaAssetRecord:
    now = datetime.now(UTC)
    return EditorMediaAssetRecord(
        id=f"editor-asset-{uuid4()}",
        project_id=project_id,
        kind=media_kind,
        source=source,
        title=title,
        storage_path=asset.storage_path,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        duration_seconds=duration_seconds,
        source_project_id=source_project_id,
        created_at=now,
        updated_at=now,
    )


def insert_editor_media_asset(cursor: Any, record: EditorMediaAssetRecord, user_id: str) -> None:
    cursor.execute(
        """
        insert into project_editor_media_assets (
            id, project_id, user_id, kind, source, title, storage_path, content_type,
            size_bytes, duration_seconds, source_project_id, created_at, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            record.id,
            record.project_id,
            user_id,
            record.kind,
            record.source,
            record.title,
            record.storage_path,
            record.content_type,
            record.size_bytes,
            record.duration_seconds,
            record.source_project_id,
            record.created_at,
            record.updated_at,
        ),
    )


def synthetic_project_assets(project: ProjectRecord) -> list[EditorMediaAssetRecord]:
    now = project.updated_at
    assets: list[EditorMediaAssetRecord] = []
    if project.asset is not None:
        assets.append(
            EditorMediaAssetRecord(
                id=f"source-{project.id}",
                project_id=project.id,
                kind="video",
                source="project_source",
                title=project.asset.filename,
                storage_path=project.asset.storage_path,
                content_type=project.asset.content_type,
                size_bytes=project.asset.size_bytes,
                duration_seconds=project.preview_video.duration_seconds if project.preview_video else None,
                source_project_id=project.id,
                created_at=project.created_at,
                updated_at=now,
            ),
        )
    if project.voiceover and project.voiceover.audio_storage_path:
        assets.append(
            EditorMediaAssetRecord(
                id=f"voiceover-{project.id}",
                project_id=project.id,
                kind="audio",
                source="project_voiceover",
                title=f"{project.project_name} voiceover",
                storage_path=project.voiceover.audio_storage_path,
                content_type="audio/mpeg",
                size_bytes=0,
                duration_seconds=project.voiceover.duration_seconds or None,
                source_project_id=project.id,
                created_at=project.created_at,
                updated_at=now,
            ),
        )
    return assets


def workspace_assets_from_rows(rows: list[tuple[object, ...]]) -> list[EditorMediaAssetRecord]:
    return [
        asset
        for row in rows
        for asset in assets_from_workspace_row(row)
    ]


def assets_from_workspace_row(row: tuple[object, ...]) -> list[EditorMediaAssetRecord]:
    project_id = str(row[0])
    project_name = str(row[1])
    asset = AssetRecord.model_validate(row[2]) if row[2] else None
    voiceover = row[3]
    created_at = cast(datetime, row[4])
    updated_at = cast(datetime, row[5])
    assets: list[EditorMediaAssetRecord] = []
    if asset is not None:
        assets.append(
            EditorMediaAssetRecord(
                id=f"workspace-source-{project_id}",
                project_id=project_id,
                kind="video",
                source="imported",
                title=f"{project_name} source video",
                storage_path=asset.storage_path,
                content_type=asset.content_type,
                size_bytes=asset.size_bytes,
                duration_seconds=None,
                source_project_id=project_id,
                created_at=created_at,
                updated_at=updated_at,
            ),
        )
    audio_path = voiceover.get("audio_storage_path") if isinstance(voiceover, dict) else None
    duration = voiceover.get("duration_seconds") if isinstance(voiceover, dict) else None
    if audio_path:
        assets.append(
            EditorMediaAssetRecord(
                id=f"workspace-voiceover-{project_id}",
                project_id=project_id,
                kind="audio",
                source="imported",
                title=f"{project_name} voiceover",
                storage_path=str(audio_path),
                content_type="audio/mpeg",
                size_bytes=0,
                duration_seconds=float(duration) if duration is not None else None,
                source_project_id=project_id,
                created_at=created_at,
                updated_at=updated_at,
            ),
        )
    return assets


def imported_asset_parts(
    user_id: str,
    source_project: ProjectRecord,
    asset_id: str | None,
    variant: str,
) -> tuple[AssetRecord, EditorMediaAssetKind, str]:
    if variant == "asset" and asset_id:
        return imported_uploaded_asset(user_id, source_project.id, asset_id)
    if variant == "source" and source_project.asset is not None:
        return source_project.asset, "video", f"{source_project.project_name} source video"
    if variant == "voiceover" and source_project.voiceover and source_project.voiceover.audio_storage_path:
        asset = AssetRecord(
            filename=f"{source_project.project_name} voiceover.mp3",
            content_type="audio/mpeg",
            size_bytes=0,
            storage_path=source_project.voiceover.audio_storage_path,
        )
        return asset, "audio", f"{source_project.project_name} voiceover"
    raise ValueError("The requested project asset is not available for import.")


def imported_uploaded_asset(
    user_id: str,
    source_project_id: str,
    asset_id: str,
) -> tuple[AssetRecord, EditorMediaAssetKind, str]:
    with connection_scope() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select title, storage_path, content_type, size_bytes, kind
                from project_editor_media_assets
                where user_id = %s and project_id = %s and id = %s
                limit 1
                """,
                (user_id, source_project_id, asset_id),
            )
            row = cursor.fetchone()
    if not row:
        raise ValueError("The requested project asset is not available for import.")
    return (
        AssetRecord(
            filename=str(row[0]),
            storage_path=str(row[1]),
            content_type=str(row[2]),
            size_bytes=int(row[3]),
        ),
        cast(EditorMediaAssetKind, row[4]),
        str(row[0]),
    )


def editor_media_asset_from_row(row: tuple[object, ...]) -> EditorMediaAssetRecord:
    return EditorMediaAssetRecord(
        id=str(row[0]),
        project_id=str(row[1]),
        kind=cast(EditorMediaAssetKind, row[2]),
        source=cast(EditorMediaAssetSource, row[3]),
        title=str(row[4]),
        storage_path=str(row[5]),
        content_type=str(row[6]),
        size_bytes=int(cast(int, row[7])),
        duration_seconds=float(cast(float, row[8])) if row[8] is not None else None,
        source_project_id=str(row[9]) if row[9] is not None else None,
        created_at=cast(datetime, row[10]),
        updated_at=cast(datetime, row[11]),
    )


project_editor_asset_store = ProjectEditorAssetStore()
