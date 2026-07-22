from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import ProjectRecord
from app.services.preview_render_intelligence import build_preview_render_intelligence
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
    scene_type: str
    target_coverage_seconds: float
    has_voiceover_fit: bool
    requires_split: bool
    would_freeze_action: bool


def render_scene_diagnostics(project: ProjectRecord, clips: list[RenderClip]) -> list[SceneRenderDiagnostics]:
    voice_clips = project.voiceover.clips if project.voiceover is not None else []
    voice_map = {clip.scene_number: clip for clip in voice_clips if clip.text.strip()}
    intelligence = build_preview_render_intelligence(project, clips, voice_map)
    diagnostics: list[SceneRenderDiagnostics] = []
    for clip in intelligence.clips:
        scene_plan = intelligence.scene_plans.get(clip.scene.scene_number)
        diagnostics.append(
            SceneRenderDiagnostics(
                scene_number=clip.scene.scene_number,
                stage=clip.stage,
                zoom_count=len(clip.scene.zooms),
                highlight_count=len(clip.scene.highlights),
                caption_count=len(clip.scene.captions),
                animated_crop=bool(clip.scene.zooms),
                spotlight=bool(clip.scene.highlights),
                voiceover_line=getattr(voice_map.get(clip.scene.scene_number), "text", "").strip(),
                clip_start=round(clip.start, 2),
                clip_end=round(clip.end, 2),
                scene_type=scene_plan.scene_type if scene_plan is not None else "generic",
                target_coverage_seconds=scene_plan.target_coverage_seconds if scene_plan is not None else round(clip.end - clip.start, 2),
                has_voiceover_fit=scene_plan.has_voiceover_fit if scene_plan is not None else True,
                requires_split=scene_plan.requires_split if scene_plan is not None else False,
                would_freeze_action=scene_plan.would_freeze_action if scene_plan is not None else False,
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
            "scene_type": item.scene_type,
            "target_coverage_seconds": item.target_coverage_seconds,
            "has_voiceover_fit": item.has_voiceover_fit,
            "requires_split": item.requires_split,
            "would_freeze_action": item.would_freeze_action,
            "voiceover_ready": project.voiceover is not None and project.voiceover.status == "ready",
        }
        for item in render_scene_diagnostics(project, clips)
    ]
