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
                    select id, project_name, product_name, video_goal, status, created_at, updated_at,
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
                product_name=str(row[2]),
                video_goal=str(row[3]),
                status=cast(Any, row[4]),
                created_at=cast(datetime, row[5]),
                updated_at=cast(datetime, row[6]),
                has_transcript=bool(row[7]),
                has_guide=bool(row[8]),
                has_launch_script=bool(row[9]),
                has_edit_plan=bool(row[10]),
                has_quality_report=bool(row[11]),
                has_benchmark_report=bool(row[12]),
                has_voiceover=bool(row[13]),
                has_preview_video=bool(row[14]),
            )
            for row in rows
        ]


project_summary_store = ProjectSummaryStore()
