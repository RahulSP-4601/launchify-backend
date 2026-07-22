from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.models.projects import EditPlanRecord, EditPlanScene, VoiceoverRecord
from app.services.voiceover_pacing import estimated_duration

MIN_DYNAMIC_SCENE_RATIO = 0.6
MIN_HIGHLIGHT_SCENE_RATIO = 0.5
MIN_ACTION_DYNAMIC_RATIO = 0.75
MIN_ACTION_HIGHLIGHT_RATIO = 0.75
MAX_VOICE_WORDS = 12
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
            edit_plan,
            dynamic_scene_ratio,
            highlight_scene_ratio,
            voiced_scene_ratio,
            avg_voice_words,
            pacing_score,
            continuity_score,
            voiceover,
        ),
    )


def delivery_issues(
    edit_plan: EditPlanRecord,
    dynamic_scene_ratio: float,
    highlight_scene_ratio: float,
    voiced_scene_ratio: float,
    avg_voice_words: float,
    pacing_score: float,
    continuity_score: float,
    voiceover: VoiceoverRecord,
) -> tuple[str, ...]:
    issues: list[str] = []
    if dynamic_scene_ratio < MIN_DYNAMIC_SCENE_RATIO:
        issues.append("limited_motion_coverage")
    if highlight_scene_ratio < MIN_HIGHLIGHT_SCENE_RATIO:
        issues.append("limited_highlight_coverage")
    if action_scene_ratio(edit_plan, lambda scene: bool(scene.zooms)) < MIN_ACTION_DYNAMIC_RATIO:
        issues.append("limited_action_motion")
    if action_scene_ratio(edit_plan, lambda scene: bool(scene.highlights)) < MIN_ACTION_HIGHLIGHT_RATIO:
        issues.append("limited_action_highlights")
    if voiceover_requires_generated_audio(voiceover) and voiceover.status != "ready":
        issues.append("voiceover_not_ready")
    elif voiced_scene_ratio < 0.85:
        issues.append("partial_voiceover_coverage")
    if voiceover_requires_generated_audio(voiceover) and any(
        line_overruns_scene(
            clip.text,
            clip.duration_seconds,
            is_first=index == 0 and is_launch_intro(edit_plan.scenes[0]),
        )
        for index, clip in enumerate(voiceover.clips)
        if clip.text.strip() and edit_plan.scenes
    ):
        issues.append("voiceover_lines_too_long")
    if pacing_score < MIN_PACING_SCORE:
        issues.append("uneven_scene_pacing")
    if continuity_score < MIN_CONTINUITY_SCORE:
        issues.append("weak_editorial_continuity")
    return tuple(issues)


def voice_word_limit(edit_plan: EditPlanRecord) -> float:
    if edit_plan.scenes and is_launch_intro(edit_plan.scenes[0]):
        return MAX_VOICE_WORDS + 2
    return float(MAX_VOICE_WORDS)


def voiceover_requires_generated_audio(voiceover: VoiceoverRecord) -> bool:
    return voiceover.mode in {"voiceover", "mixed"}


def line_overruns_scene(text: str, duration_seconds: float, *, is_first: bool) -> bool:
    if not text.strip():
        return False
    if estimated_duration(text) <= duration_seconds + 0.2:
        return False
    return len(text.split()) > scene_word_limit(duration_seconds, is_first=is_first)


def scene_word_limit(duration_seconds: float, *, is_first: bool) -> int:
    baseline = MAX_VOICE_WORDS + (2 if is_first else 0)
    return max(baseline, int(round(max(duration_seconds, 1.0) * 2.65)))


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
    first_is_launch_intro = bool(edit_plan.scenes) and is_launch_intro(edit_plan.scenes[0])
    voice_scores = [
        voice_continuity_score(clip.text, is_first=index == 0 and first_is_launch_intro)
        for index, clip in enumerate(voiceover.clips)
        if clip.text.strip()
    ]
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


def action_scene_ratio(edit_plan: EditPlanRecord, predicate: Callable[[EditPlanScene], bool]) -> float:
    action_scenes = [scene for scene in edit_plan.scenes if scene.scene_role == "action"]
    if not action_scenes:
        return 1.0
    matched = sum(1 for scene in action_scenes if predicate(scene))
    return round(matched / len(action_scenes), 2)


def voice_continuity_score(text: str, *, is_first: bool = False) -> float:
    words = text.split()
    if len(words) < 4:
        return 0.58
    limit = MAX_VOICE_WORDS + 2 if is_first else MAX_VOICE_WORDS
    if len(words) > limit:
        return 0.65
    if any(marker in text.lower() for marker in ("then then", "click click", "select select")):
        return 0.55
    return 0.92


def is_launch_intro(scene: EditPlanScene) -> bool:
    lowered = scene.spoken_line.lower()
    return scene.action_class == "auth_action" and scene.scene_role == "action" and (
        lowered.startswith("we are launching ") or lowered.startswith("we're launching ") or lowered.startswith("this is ")
    )
