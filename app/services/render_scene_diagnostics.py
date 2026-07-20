from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import ProjectRecord
from app.services.render_proxy_clips import RenderClip


@dataclass(frozen=True)
class SceneRenderDiagnostics:
    scene_number: int
    stage: str
    zoom_count: int
    highlight_count: int
    caption_count: int
    animated_crop: bool
    spotlight: bool
    voiceover_line: str
    clip_start: float
    clip_end: float


def render_scene_diagnostics(project: ProjectRecord, clips: list[RenderClip]) -> list[SceneRenderDiagnostics]:
    voice_map = {
        clip.scene_number: clip.text.strip()
        for clip in (project.voiceover.clips if project.voiceover is not None else [])
        if clip.text.strip()
    }
    diagnostics: list[SceneRenderDiagnostics] = []
    for clip in clips:
        diagnostics.append(
            SceneRenderDiagnostics(
                scene_number=clip.scene.scene_number,
                stage=clip.stage,
                zoom_count=len(clip.scene.zooms),
                highlight_count=len(clip.scene.highlights),
                caption_count=len(clip.scene.captions),
                animated_crop=bool(clip.scene.zooms),
                spotlight=bool(clip.scene.highlights),
                voiceover_line=voice_map.get(clip.scene.scene_number, ""),
                clip_start=round(clip.start, 2),
                clip_end=round(clip.end, 2),
            )
        )
    return diagnostics


def diagnostic_payloads(project: ProjectRecord, clips: list[RenderClip]) -> list[dict[str, object]]:
    return [
        {
            "scene_number": item.scene_number,
            "stage": item.stage,
            "zoom_count": item.zoom_count,
            "highlight_count": item.highlight_count,
            "caption_count": item.caption_count,
            "animated_crop": item.animated_crop,
            "spotlight": item.spotlight,
            "voiceover_line": item.voiceover_line,
            "clip_start": item.clip_start,
            "clip_end": item.clip_end,
            "voiceover_ready": project.voiceover is not None and project.voiceover.status == "ready",
        }
        for item in render_scene_diagnostics(project, clips)
    ]
