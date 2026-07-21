from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene, RenderSpecRecord, VoiceoverClipRecord, VoiceoverCueRecord, VoiceoverRecord

VOICEOVER_SCENE_TAIL_SECONDS = 0.18
MIN_SCENE_DURATION_SECONDS = 0.8


def reconcile_edit_plan_to_voiceover(
    edit_plan: EditPlanRecord,
    voiceover: VoiceoverRecord,
) -> tuple[EditPlanRecord, VoiceoverRecord]:
    if voiceover.status != "ready" or not voiceover.clips or not edit_plan.scenes:
        return edit_plan, voiceover
    clips_by_scene = {clip.scene_number: clip for clip in voiceover.clips if clip.audio_storage_path}
    reconciled_scenes: list[EditPlanScene] = []
    updated_clips: list[VoiceoverClipRecord] = []
    cursor = 0.0
    for scene in sorted(edit_plan.scenes, key=lambda item: item.scene_number):
        render_duration = target_render_duration(scene, clips_by_scene.get(scene.scene_number))
        reconciled_scenes.append(scene.model_copy(update={"render_duration_seconds": render_duration}))
        clip = clips_by_scene.get(scene.scene_number)
        if clip is not None:
            updated_clips.append(clip.model_copy(update={"start": round(cursor, 2), "end": round(cursor + clip.duration_seconds, 2)}))
        cursor = round(cursor + render_duration, 2)
    total_duration = round(sum(scene_duration_seconds(scene) for scene in reconciled_scenes), 2)
    return (
        edit_plan.model_copy(
            update={
                "scenes": reconciled_scenes,
                "total_duration_seconds": total_duration,
                "render_spec": updated_render_spec(edit_plan.render_spec, total_duration),
            }
        ),
        rebuild_voiceover_timeline(voiceover, updated_clips),
    )


def target_render_duration(scene: EditPlanScene, clip: VoiceoverClipRecord | None) -> float:
    base_duration = scene_duration_seconds(scene)
    editorial_floor = max(base_duration, scene.readable_hold_seconds + 0.9 if scene.readable_hold_seconds > 0 else base_duration)
    if clip is None:
        return round(editorial_floor, 2)
    return round(max(editorial_floor, clip.duration_seconds + VOICEOVER_SCENE_TAIL_SECONDS, MIN_SCENE_DURATION_SECONDS), 2)


def scene_duration_seconds(scene: EditPlanScene) -> float:
    return round(max(scene.render_duration_seconds or (scene.end - scene.start), MIN_SCENE_DURATION_SECONDS), 2)


def rebuild_voiceover_timeline(
    voiceover: VoiceoverRecord,
    updated_clips: list[VoiceoverClipRecord],
) -> VoiceoverRecord:
    clips_by_scene = {clip.scene_number: clip for clip in updated_clips}
    clips = [clips_by_scene.get(clip.scene_number, clip) for clip in voiceover.clips]
    cues = cue_track(clips)
    duration_seconds = round(max((clip.start + clip.duration_seconds for clip in clips), default=0.0), 2)
    return voiceover.model_copy(update={"clips": clips, "cues": cues, "duration_seconds": duration_seconds})


def cue_track(clips: list[VoiceoverClipRecord]) -> list[VoiceoverCueRecord]:
    cues: list[VoiceoverCueRecord] = []
    cursor = 0.0
    for clip in clips:
        duration = max(clip.duration_seconds, 0.4)
        cues.append(
            VoiceoverCueRecord(
                scene_number=clip.scene_number,
                start=round(cursor, 2),
                end=round(cursor + duration, 2),
                text=clip.text,
                duration_seconds=round(duration, 2),
            )
        )
        cursor = round(cursor + duration, 2)
    return cues


def updated_render_spec(render_spec: RenderSpecRecord, total_duration: float) -> RenderSpecRecord:
    return render_spec.model_copy(update={"total_duration_seconds": total_duration})
