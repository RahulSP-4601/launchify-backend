from __future__ import annotations

from app.models.project_editor import (
    EditorCaptionRecord,
    EditorClipRecord,
    EditorSceneRecord,
    EditorTrackRecord,
    ProjectEditorState,
    ProjectEditorSequence,
)
from app.models.projects import EditPlanScene, LaunchScriptScene, ProjectRecord, TranscriptSegment


def build_project_editor_state(project: ProjectRecord) -> ProjectEditorState:
    scenes = build_editor_scenes(project)
    captions = build_editor_captions(project, scenes)
    return ProjectEditorState(
        aspect_ratio="16:9",
        captions=captions,
        edit_mode="overwrite",
        selected_scene_id=scenes[0].id if scenes else "",
        selected_track_id="track-video-1",
        sequence=build_editor_sequence(project.id, scenes, captions, build_audio_clips(project, "track-audio-1")),
        scenes=scenes,
        show_captions=True,
    )


def restore_ai_scene(
    current_state: ProjectEditorState,
    baseline_state: ProjectEditorState,
    scene_id: str,
) -> ProjectEditorState:
    current_scene = next((scene for scene in current_state.scenes if scene.id == scene_id), None)
    baseline_scene = next((scene for scene in baseline_state.scenes if scene.id == scene_id), None)
    if current_scene is None or baseline_scene is None:
        raise ValueError("The selected scene is not available in the AI draft.")
    restored_scene = baseline_scene.model_copy(
        update={
            "end": current_scene.end,
            "scene_number": current_scene.scene_number,
            "start": current_scene.start,
        }
    )
    restored_captions = remap_scene_captions(current_scene, baseline_scene, baseline_state.captions)
    kept_captions = [caption for caption in current_state.captions if caption.scene_id != scene_id]
    next_captions = sorted([*kept_captions, *restored_captions], key=lambda caption: (caption.start, caption.end))
    next_scenes = [
        restored_scene if scene.id == scene_id else scene
        for scene in current_state.scenes
    ]
    regenerated_sequence = build_editor_sequence(
        sequence_project_id(current_state),
        next_scenes,
        next_captions,
        existing_audio_clips(current_state),
    )
    return current_state.model_copy(
        update={
            "captions": next_captions,
            "edit_mode": current_state.edit_mode,
            "selected_scene_id": scene_id,
            "selected_track_id": current_state.selected_track_id,
            "sequence": merge_regenerated_sequence(current_state.sequence, regenerated_sequence),
            "scenes": next_scenes,
        }
    )


def build_editor_scenes(project: ProjectRecord) -> list[EditorSceneRecord]:
    return (
        build_scenes_from_edit_plan(project.edit_plan.scenes if project.edit_plan else [])
        or build_scenes_from_launch_script(project.launch_script.scenes if project.launch_script else [])
        or build_scenes_from_transcript(project.transcript)
        or [build_fallback_scene(project)]
    )


def build_editor_captions(
    project: ProjectRecord,
    scenes: list[EditorSceneRecord],
) -> list[EditorCaptionRecord]:
    return (
        build_captions_from_edit_plan(project.edit_plan.scenes if project.edit_plan else [])
        or build_captions_from_transcript(project.transcript, scenes)
        or build_captions_from_scenes(scenes)
    )


def build_scenes_from_edit_plan(scenes: list[EditPlanScene]) -> list[EditorSceneRecord]:
    return [
        EditorSceneRecord(
            end=normalize_end(scene.start, scene.end, scene.render_duration_seconds),
            id=f"scene-{scene.scene_number}",
            on_screen_text=scene.on_screen_text,
            scene_number=scene.scene_number,
            source="edit_plan",
            spoken_line=scene.spoken_line,
            start=scene.start,
            title=scene.title or scene.purpose or f"Scene {scene.scene_number}",
        )
        for scene in scenes
    ]


def build_scenes_from_launch_script(scenes: list[LaunchScriptScene]) -> list[EditorSceneRecord]:
    built: list[EditorSceneRecord] = []
    cursor = 0.0
    for scene in scenes:
        duration = max(scene.estimated_duration_seconds or 4.0, 2.5)
        built.append(
            EditorSceneRecord(
                end=round(cursor + duration, 2),
                id=f"scene-{scene.scene_number}",
                on_screen_text=scene.on_screen_text,
                scene_number=scene.scene_number,
                source="launch_script",
                spoken_line=scene.spoken_line,
                start=round(cursor, 2),
                title=scene.purpose or f"Scene {scene.scene_number}",
            )
        )
        cursor += duration
    return built


def build_scenes_from_transcript(transcript: list[TranscriptSegment]) -> list[EditorSceneRecord]:
    return [
        EditorSceneRecord(
            end=max(segment.end, segment.start + 1.5),
            id=f"scene-{index + 1}",
            on_screen_text=segment.text,
            scene_number=index + 1,
            source="transcript",
            spoken_line=segment.text,
            start=segment.start,
            title=f"Scene {index + 1}",
        )
        for index, segment in enumerate(transcript[:8])
    ]


def build_fallback_scene(project: ProjectRecord) -> EditorSceneRecord:
    return EditorSceneRecord(
        end=project_duration_seconds(project),
        id="scene-1",
        on_screen_text="Add on-screen guidance for your first scene.",
        scene_number=1,
        source="fallback",
        spoken_line="Start shaping the first AI draft here.",
        start=0.0,
        title=project.project_name,
    )


def build_captions_from_edit_plan(scenes: list[EditPlanScene]) -> list[EditorCaptionRecord]:
    return [
        EditorCaptionRecord(
            end=max(caption.end, caption.start + 0.8),
            id=f"caption-{scene.scene_number}-{index + 1}",
            scene_id=f"scene-{scene.scene_number}",
            start=caption.start,
            text=caption.text,
        )
        for scene in scenes
        for index, caption in enumerate(scene.captions)
    ]


def build_captions_from_transcript(
    transcript: list[TranscriptSegment],
    scenes: list[EditorSceneRecord],
) -> list[EditorCaptionRecord]:
    return [
        EditorCaptionRecord(
            end=max(segment.end, segment.start + 0.8),
            id=f"caption-transcript-{index + 1}",
            scene_id=scene_id_for_time(scenes, segment.start),
            start=segment.start,
            text=segment.text,
        )
        for index, segment in enumerate(transcript)
    ]


def build_captions_from_scenes(scenes: list[EditorSceneRecord]) -> list[EditorCaptionRecord]:
    return [
        EditorCaptionRecord(
            end=scene.end,
            id=f"caption-{scene.scene_number}",
            scene_id=scene.id,
            start=scene.start,
            text=scene.spoken_line,
        )
        for scene in scenes
    ]


def remap_scene_captions(
    current_scene: EditorSceneRecord,
    baseline_scene: EditorSceneRecord,
    baseline_captions: list[EditorCaptionRecord],
) -> list[EditorCaptionRecord]:
    scene_captions = [caption for caption in baseline_captions if caption.scene_id == baseline_scene.id]
    if not scene_captions:
        return [
            EditorCaptionRecord(
                end=current_scene.end,
                id=f"{current_scene.id}-caption-1",
                scene_id=current_scene.id,
                start=current_scene.start,
                text=baseline_scene.spoken_line,
            )
        ]
    source_span = max(baseline_scene.end - baseline_scene.start, 0.5)
    target_span = max(current_scene.end - current_scene.start, 0.5)
    scale = target_span / source_span
    return [
        EditorCaptionRecord(
            end=clamp_caption_edge(current_scene.start, current_scene.end, current_scene.start + ((caption.end - baseline_scene.start) * scale)),
            id=caption.id,
            scene_id=current_scene.id,
            start=clamp_caption_edge(current_scene.start, current_scene.end, current_scene.start + ((caption.start - baseline_scene.start) * scale)),
            text=caption.text,
        )
        for caption in scene_captions
    ]


def scene_id_for_time(scenes: list[EditorSceneRecord], time: float) -> str | None:
    scene = next((item for item in scenes if time >= item.start and time <= item.end), None)
    return scene.id if scene else (scenes[0].id if scenes else None)


def normalize_end(start: float, end: float, render_duration: float | None) -> float:
    if end > start:
        return end
    if render_duration and render_duration > 0:
        return start + render_duration
    return start + 3


def project_duration_seconds(project: ProjectRecord) -> float:
    launch_script_duration = sum(max(scene.estimated_duration_seconds or 0.0, 0.0) for scene in (project.launch_script.scenes if project.launch_script else []))
    guide_duration = project.guide.steps[-1].end if project.guide and project.guide.steps else 0.0
    return (
        (project.preview_video.duration_seconds if project.preview_video else 0.0)
        or (project.edit_plan.total_duration_seconds if project.edit_plan else 0.0)
        or launch_script_duration
        or guide_duration
        or 12.0
    )


def clamp_caption_edge(scene_start: float, scene_end: float, value: float) -> float:
    return round(min(max(value, scene_start), scene_end), 2)


def build_editor_sequence(
    project_id: str,
    scenes: list[EditorSceneRecord],
    captions: list[EditorCaptionRecord],
    audio_clips: list[EditorClipRecord] | None = None,
) -> ProjectEditorSequence:
    video_track_id = "track-video-1"
    caption_track_id = "track-caption-1"
    tracks = [
        EditorTrackRecord(clips=build_video_clips(scenes, video_track_id), id=video_track_id, kind="video", name="Video"),
        EditorTrackRecord(clips=build_caption_clips(captions, caption_track_id), id=caption_track_id, kind="caption", name="Captions"),
    ]
    if audio_clips:
        tracks.append(EditorTrackRecord(clips=audio_clips, id="track-audio-1", kind="audio", name="Voiceover"))
    return ProjectEditorSequence(
        duration_seconds=scenes[-1].end if scenes else 0.0,
        id=f"sequence-{project_id}",
        playhead_seconds=0.0,
        tracks=tracks,
        version=1,
    )


def build_video_clips(
    scenes: list[EditorSceneRecord],
    track_id: str,
) -> list[EditorClipRecord]:
    source_cursor = 0.0
    clips: list[EditorClipRecord] = []
    for scene in scenes:
        clip, source_cursor = build_video_clip(scene, source_cursor, track_id)
        clips.append(clip)
    return clips


def build_video_clip(
    scene: EditorSceneRecord,
    source_cursor: float,
    track_id: str,
) -> tuple[EditorClipRecord, float]:
    inserted = scene.source == "inserted" or scene.id.startswith("inserted-scene-")
    duration = max(scene.end - scene.start, 0.5)
    source_start = None if inserted else round(source_cursor, 2)
    source_end = None if inserted else round(source_cursor + duration, 2)
    clip = EditorClipRecord(
        id=f"clip-{scene.id}",
        kind="inserted_card" if inserted else "source_video",
        scene_id=scene.id,
        source_end=source_end,
        source_start=source_start,
        text=scene.on_screen_text,
        timeline_end=scene.end,
        timeline_start=scene.start,
        title=scene.title,
        track_id=track_id,
    )
    return clip, source_end if source_end is not None else source_cursor


def build_caption_clips(
    captions: list[EditorCaptionRecord],
    track_id: str,
) -> list[EditorClipRecord]:
    return [
        EditorClipRecord(
            id=f"caption-clip-{caption.id}",
            kind="caption",
            scene_id=caption.scene_id,
            source_end=None,
            source_start=None,
            text=caption.text,
            timeline_end=caption.end,
            timeline_start=caption.start,
            title=caption.text[:48] or "Caption",
            track_id=track_id,
        )
        for caption in captions
    ]


def build_audio_clips(
    project: ProjectRecord,
    track_id: str,
) -> list[EditorClipRecord]:
    voiceover = project.voiceover
    if voiceover is None or voiceover.status != "ready":
        return []
    return [
        EditorClipRecord(
            id=f"voiceover-clip-{clip.scene_number}-{index + 1}",
            kind="voiceover",
            scene_id=f"scene-{clip.scene_number}",
            source_end=None,
            source_start=None,
            text=clip.text,
            timeline_end=clip.end,
            timeline_start=clip.start,
            title=clip.text[:48] or f"Voiceover {clip.scene_number}",
            track_id=track_id,
        )
        for index, clip in enumerate(voiceover.clips)
        if clip.audio_storage_path
    ]


def existing_audio_clips(current_state: ProjectEditorState) -> list[EditorClipRecord]:
    if current_state.sequence is None:
        return []
    return [
        clip.model_copy()
        for track in current_state.sequence.tracks
        if track.kind == "audio"
        for clip in track.clips
    ]


def merge_regenerated_sequence(
    current_sequence: ProjectEditorSequence | None,
    regenerated_sequence: ProjectEditorSequence,
) -> ProjectEditorSequence:
    if current_sequence is None:
        return regenerated_sequence
    primary_track = primary_video_track(current_sequence)
    primary_video_track_id = primary_track.id if primary_track is not None else None
    merged_tracks = [
        merge_track_preserving_state(current_sequence, regenerated_sequence, "video"),
        merge_track_preserving_state(current_sequence, regenerated_sequence, "caption"),
        *preserved_secondary_video_tracks(current_sequence, primary_video_track_id),
        *preserved_auxiliary_tracks(current_sequence),
    ]
    duration_seconds = max((clip.timeline_end for track in merged_tracks for clip in track.clips), default=0.0)
    return regenerated_sequence.model_copy(
        update={
            "duration_seconds": duration_seconds,
            "id": current_sequence.id,
            "playhead_seconds": min(current_sequence.playhead_seconds, duration_seconds),
            "tracks": merged_tracks,
            "version": current_sequence.version,
        }
    )


def merge_track_preserving_state(
    current_sequence: ProjectEditorSequence,
    regenerated_sequence: ProjectEditorSequence,
    kind: str,
) -> EditorTrackRecord:
    regenerated = next(track for track in regenerated_sequence.tracks if track.kind == kind)
    current = next((track for track in current_sequence.tracks if track.kind == kind), None)
    if current is None:
        return regenerated
    return current.model_copy(
        update={
            "clips": regenerated.clips,
        }
    )


def preserved_auxiliary_tracks(current_sequence: ProjectEditorSequence) -> list[EditorTrackRecord]:
    return [
        track.model_copy(deep=True)
        for track in current_sequence.tracks
        if track.kind not in {"video", "caption"}
    ]


def preserved_secondary_video_tracks(
    current_sequence: ProjectEditorSequence,
    primary_track_id: str | None,
) -> list[EditorTrackRecord]:
    return [
        track.model_copy(deep=True)
        for track in current_sequence.tracks
        if track.kind == "video" and track.id != primary_track_id
    ]


def primary_video_track(sequence: ProjectEditorSequence) -> EditorTrackRecord | None:
    return next((track for track in sequence.tracks if track.kind == "video"), None)


def sequence_project_id(current_state: ProjectEditorState) -> str:
    return current_state.sequence.id.replace("sequence-", "", 1) if current_state.sequence else "project"
