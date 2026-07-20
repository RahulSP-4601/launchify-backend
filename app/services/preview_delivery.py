from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanRecord, VoiceoverRecord

MIN_DYNAMIC_SCENE_RATIO = 0.6
MIN_HIGHLIGHT_SCENE_RATIO = 0.5
MAX_VOICE_WORDS = 10


@dataclass(frozen=True)
class DeliveryDiagnostics:
    dynamic_scene_ratio: float
    highlight_scene_ratio: float
    voiced_scene_ratio: float
    avg_voice_words: float
    issues: tuple[str, ...]


def preview_delivery_diagnostics(
    edit_plan: EditPlanRecord,
    voiceover: VoiceoverRecord,
) -> DeliveryDiagnostics:
    total_scenes = max(len(edit_plan.scenes), 1)
    dynamic_scenes = sum(1 for scene in edit_plan.scenes if scene.zooms)
    highlight_scenes = sum(1 for scene in edit_plan.scenes if scene.highlights)
    voiced_lines = [clip.text for clip in voiceover.clips if clip.text.strip()]
    voiced_scene_ratio = round(min(len(voiced_lines), total_scenes) / total_scenes, 2)
    avg_voice_words = round(sum(len(text.split()) for text in voiced_lines) / max(len(voiced_lines), 1), 2)
    dynamic_scene_ratio = round(dynamic_scenes / total_scenes, 2)
    highlight_scene_ratio = round(highlight_scenes / total_scenes, 2)
    return DeliveryDiagnostics(
        dynamic_scene_ratio=dynamic_scene_ratio,
        highlight_scene_ratio=highlight_scene_ratio,
        voiced_scene_ratio=voiced_scene_ratio,
        avg_voice_words=avg_voice_words,
        issues=delivery_issues(dynamic_scene_ratio, highlight_scene_ratio, voiced_scene_ratio, avg_voice_words, voiceover.status),
    )


def delivery_issues(
    dynamic_scene_ratio: float,
    highlight_scene_ratio: float,
    voiced_scene_ratio: float,
    avg_voice_words: float,
    voiceover_status: str,
) -> tuple[str, ...]:
    issues: list[str] = []
    if dynamic_scene_ratio < MIN_DYNAMIC_SCENE_RATIO:
        issues.append("limited_motion_coverage")
    if highlight_scene_ratio < MIN_HIGHLIGHT_SCENE_RATIO:
        issues.append("limited_highlight_coverage")
    if voiceover_status != "ready":
        issues.append("voiceover_not_ready")
    elif voiced_scene_ratio < 0.85:
        issues.append("partial_voiceover_coverage")
    if avg_voice_words > MAX_VOICE_WORDS:
        issues.append("voiceover_lines_too_long")
    return tuple(issues)
