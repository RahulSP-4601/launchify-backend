from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanRecord, EditPlanScene, VoiceoverRecord

MIN_DYNAMIC_SCENE_RATIO = 0.6
MIN_HIGHLIGHT_SCENE_RATIO = 0.5
MAX_VOICE_WORDS = 10
MIN_PACING_SCORE = 0.72
MIN_CONTINUITY_SCORE = 0.75


@dataclass(frozen=True)
class DeliveryDiagnostics:
    dynamic_scene_ratio: float
    highlight_scene_ratio: float
    voiced_scene_ratio: float
    avg_voice_words: float
    pacing_score: float
    continuity_score: float
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
    pacing_score = round(editorial_pacing_score(edit_plan), 2)
    continuity_score = round(editorial_continuity_score(edit_plan, voiceover), 2)
    return DeliveryDiagnostics(
        dynamic_scene_ratio=dynamic_scene_ratio,
        highlight_scene_ratio=highlight_scene_ratio,
        voiced_scene_ratio=voiced_scene_ratio,
        avg_voice_words=avg_voice_words,
        pacing_score=pacing_score,
        continuity_score=continuity_score,
        issues=delivery_issues(
            dynamic_scene_ratio,
            highlight_scene_ratio,
            voiced_scene_ratio,
            avg_voice_words,
            pacing_score,
            continuity_score,
            voiceover.status,
        ),
    )


def delivery_issues(
    dynamic_scene_ratio: float,
    highlight_scene_ratio: float,
    voiced_scene_ratio: float,
    avg_voice_words: float,
    pacing_score: float,
    continuity_score: float,
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
    if pacing_score < MIN_PACING_SCORE:
        issues.append("uneven_scene_pacing")
    if continuity_score < MIN_CONTINUITY_SCORE:
        issues.append("weak_editorial_continuity")
    return tuple(issues)


def editorial_pacing_score(edit_plan: EditPlanRecord) -> float:
    if not edit_plan.scenes:
        return 0.0
    scores = [scene_pacing_score(scene) for scene in edit_plan.scenes]
    return sum(scores) / len(scores)


def scene_pacing_score(scene: EditPlanScene) -> float:
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    if duration < 1.8:
        return 0.45
    if duration < 2.8:
        return 0.68
    if duration <= 11.5:
        return 1.0
    if duration <= 13.5:
        return 0.82
    return 0.58


def editorial_continuity_score(edit_plan: EditPlanRecord, voiceover: VoiceoverRecord) -> float:
    if not edit_plan.scenes:
        return 0.0
    scene_scores = [scene_continuity_score(scene) for scene in edit_plan.scenes]
    voice_scores = [voice_continuity_score(text) for text in (clip.text for clip in voiceover.clips) if text.strip()]
    combined = scene_scores + voice_scores
    return sum(combined) / max(len(combined), 1)


def scene_continuity_score(scene: EditPlanScene) -> float:
    highlights = scene.highlights
    zooms = scene.zooms
    role = scene.scene_role
    score = 0.72
    if zooms:
        score += 0.12
    if highlights and role != "result":
        score += 0.08
    if scene.result_anchor_timestamp is not None:
        score += 0.08
    return min(score, 1.0)


def voice_continuity_score(text: str) -> float:
    words = text.split()
    if len(words) < 4:
        return 0.58
    if len(words) > MAX_VOICE_WORDS:
        return 0.65
    if any(marker in text.lower() for marker in ("then then", "click click", "select select")):
        return 0.55
    return 0.92
