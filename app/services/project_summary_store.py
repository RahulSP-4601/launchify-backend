from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from app.models.projects import ProjectSummary
from app.services.database import connection_scope


class ProjectSummaryStore:
    def list_projects(self, user_id: str) -> list[ProjectSummary]:
        with connection_scope() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    select id, project_name, status, created_at, updated_at,
                           transcript <> '[]'::jsonb as has_transcript,
                           guide is not null and jsonb_array_length(coalesce(guide->'steps', '[]'::jsonb)) > 0 as has_guide,
                           launch_script is not null as has_launch_script,
                           edit_plan is not null as has_edit_plan,
                           quality_report is not null as has_quality_report,
                           benchmark_report is not null as has_benchmark_report,
                           coalesce(voiceover->>'script', '') <> '' as has_voiceover,
                           preview_video is not null as has_preview_video
                    from projects
                    where user_id = %s
                    order by updated_at desc
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return [
            ProjectSummary(
                id=str(row[0]),
                project_name=str(row[1]),
                status=cast(Any, row[2]),
                created_at=cast(datetime, row[3]),
                updated_at=cast(datetime, row[4]),
                has_transcript=bool(row[5]),
                has_guide=bool(row[6]),
                has_launch_script=bool(row[7]),
                has_edit_plan=bool(row[8]),
                has_quality_report=bool(row[9]),
                has_benchmark_report=bool(row[10]),
                has_voiceover=bool(row[11]),
                has_preview_video=bool(row[12]),
            )
            for row in rows
        ]


project_summary_store = ProjectSummaryStore()
