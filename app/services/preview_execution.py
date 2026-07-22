from __future__ import annotations

from pathlib import Path

from app.models.projects import EditPlanRecord, EditPlanScene, ProjectRecord, RenderSpecRecord, VoiceoverRecord
from app.services.preview_execution_semantics import repair_execution_edit_plan
from app.services.preview_manifest import manifest_edit_plan
from app.services.voiceover import downloadable_voiceover_audio, downloaded_voiceover_clips, scheduled_voiceover_track
from app.services.voiceover_timeline import reconcile_edit_plan_to_voiceover


def build_preview_execution_project(
    project: ProjectRecord,
    quality: str,
) -> ProjectRecord:
    if project.edit_plan is None or quality != "preview":
        return project
    semantic_seed_plan = repair_execution_edit_plan(project.edit_plan)
    seed_voiceover = reconciled_execution_voiceover(project.voiceover, semantic_seed_plan.scenes)
    seeded_project = project.model_copy(update={"edit_plan": semantic_seed_plan, "voiceover": seed_voiceover})
    execution_edit_plan = repair_execution_edit_plan(manifest_edit_plan(seeded_project, quality))
    execution_voiceover = reconciled_execution_voiceover(seed_voiceover, execution_edit_plan.scenes)
    if execution_edit_plan == project.edit_plan and execution_voiceover == project.voiceover:
        return project
    return project.model_copy(update={"edit_plan": execution_edit_plan, "voiceover": execution_voiceover})


def downloadable_execution_voiceover_audio(project: ProjectRecord) -> Path | None:
    voiceover = project.voiceover
    if voiceover is None:
        return None
    timed_audio = scheduled_execution_voiceover(voiceover, project.edit_plan.scenes if project.edit_plan is not None else [])
    return timed_audio or downloadable_voiceover_audio(voiceover)


def scheduled_execution_voiceover(
    voiceover: VoiceoverRecord,
    scenes: list[EditPlanScene],
) -> Path | None:
    if voiceover.status != "ready" or not voiceover.clips or not scenes:
        return None
    downloaded = downloaded_voiceover_clips(voiceover)
    clips_by_scene = {clip.scene_number: (clip, audio_file) for clip, audio_file in downloaded}
    timed = []
    cursor = 0.0
    used_scene_numbers: set[int] = set()
    try:
        for scene in scenes:
            scene_duration = round(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8), 2)
            clip_audio = clips_by_scene.get(scene.scene_number)
            if clip_audio is not None and scene.spoken_line.strip() and scene.scene_number not in used_scene_numbers:
                clip, audio_file = clip_audio
                timed.append((clip.model_copy(update={"start": round(cursor, 2)}), audio_file))
                used_scene_numbers.add(scene.scene_number)
            cursor = round(cursor + scene_duration, 2)
        return scheduled_voiceover_track(timed) if timed else None
    finally:
        for clip, audio_file in downloaded:
            if timed and clip.scene_number in used_scene_numbers:
                continue
            audio_file.unlink(missing_ok=True)


def reconciled_execution_voiceover(
    voiceover: VoiceoverRecord | None,
    scenes: list[EditPlanScene],
) -> VoiceoverRecord | None:
    if voiceover is None or not scenes:
        return voiceover
    total_duration = round(sum(max(scene.render_duration_seconds or (scene.end - scene.start), 0.8) for scene in scenes), 2)
    edit_plan_stub = EditPlanRecord(
        overview="preview execution",
        total_duration_seconds=total_duration,
        scenes=scenes,
        render_spec=RenderSpecRecord(
            title_card="preview execution",
            title_options=[],
            cta="",
            total_duration_seconds=total_duration,
        ),
    )
    _reconciled_plan, reconciled_voiceover = reconcile_edit_plan_to_voiceover(edit_plan_stub, voiceover)
    return reconciled_voiceover
