from __future__ import annotations

from app.models.project_editor import EditorClipRecord, ProjectEditorSequence, ProjectEditorState
from app.models.projects import ProjectRecord

TIMING_EPSILON = 0.05
MIN_SCENE_SECONDS = 0.5
MIN_CAPTION_SECONDS = 0.2


def validate_project_editor_state(project: ProjectRecord, state: ProjectEditorState) -> None:
    scene_ids = validate_scenes(project, state)
    validate_selected_scene(scene_ids, state.selected_scene_id)
    validate_selected_clip(state.sequence, state.selected_clip_id)
    validate_captions(state, scene_ids)
    validate_comments(state, scene_ids)
    validate_source_coverage(project_duration_limit(project), state)
    validate_sequence(state)


def validate_scenes(project: ProjectRecord, state: ProjectEditorState) -> set[str]:
    if not state.scenes:
        raise ValueError("At least one scene is required in the editor.")
    scene_ids: set[str] = set()
    previous_end = 0.0
    for scene in sorted(state.scenes, key=lambda item: item.start):
        if scene.id in scene_ids:
            raise ValueError("Each editor scene must have a unique id.")
        validate_scene_timing(scene.start, scene.end, previous_end)
        scene_ids.add(scene.id)
        previous_end = scene.end
    return scene_ids


def validate_selected_scene(scene_ids: set[str], selected_scene_id: str) -> None:
    if selected_scene_id and selected_scene_id not in scene_ids:
        raise ValueError("The selected editor scene no longer exists.")


def validate_selected_clip(sequence: ProjectEditorSequence | None, selected_clip_id: str | None) -> None:
    if not selected_clip_id or sequence is None:
        return
    if not any(clip.id == selected_clip_id for track in sequence.tracks for clip in track.clips):
        raise ValueError("The selected editor clip no longer exists.")


def validate_captions(state: ProjectEditorState, scene_ids: set[str]) -> None:
    scenes_by_id = {scene.id: scene for scene in state.scenes}
    timeline_duration = state.scenes[-1].end
    for caption in state.captions:
        if caption.end - caption.start < MIN_CAPTION_SECONDS:
            raise ValueError("Each caption must be at least 0.2 seconds long.")
        if caption.start < 0 or caption.end > timeline_duration + TIMING_EPSILON:
            raise ValueError("Caption timings must stay within the editor timeline duration.")
        if caption.scene_id is None:
            continue
        if caption.scene_id not in scene_ids:
            raise ValueError("Each caption must point to a valid scene.")
        scene = scenes_by_id[caption.scene_id]
        if caption.start < scene.start - TIMING_EPSILON or caption.end > scene.end + TIMING_EPSILON:
            raise ValueError("Caption timings must stay inside their scene boundaries.")


def validate_comments(state: ProjectEditorState, scene_ids: set[str]) -> None:
    timeline_duration = state.sequence.duration_seconds if state.sequence else state.scenes[-1].end
    for comment in state.comments:
        if comment.scene_id is not None and comment.scene_id not in scene_ids:
            raise ValueError("Each comment must point to a valid scene.")
        if comment.time < 0 or comment.time > timeline_duration + TIMING_EPSILON:
            raise ValueError("Comment timestamps must stay within the editor timeline duration.")


def validate_scene_timing(start: float, end: float, previous_end: float) -> None:
    if end - start < MIN_SCENE_SECONDS:
        raise ValueError("Each scene must be at least 0.5 seconds long.")
    if start < 0:
        raise ValueError("Scene timings must stay on the positive timeline.")
    if start < previous_end - TIMING_EPSILON:
        raise ValueError("Scenes cannot overlap on the editor timeline.")
    if start > previous_end + TIMING_EPSILON:
        raise ValueError("Editor scenes must stay contiguous on the timeline.")


def validate_source_coverage(duration_limit: float, state: ProjectEditorState) -> None:
    source_seconds = sum(
        max(scene.end - scene.start, 0.0)
        for scene in state.scenes
        if scene.source not in {"inserted", "imported"}
    )
    if source_seconds > duration_limit + TIMING_EPSILON:
        raise ValueError("Edited source clips exceed the available source media duration.")


def validate_sequence(state: ProjectEditorState) -> None:
    if state.sequence is None:
        return
    validate_track_ids(state.sequence)
    validate_selected_track(state.sequence, state.selected_track_id)
    validate_clip_timings(state.sequence)
    validate_sequence_alignment(state)


def validate_track_ids(sequence: ProjectEditorSequence) -> None:
    track_ids: set[str] = set()
    for track in sequence.tracks:
        if track.id in track_ids:
            raise ValueError("Each editor track must have a unique id.")
        track_ids.add(track.id)


def validate_selected_track(sequence: ProjectEditorSequence, selected_track_id: str) -> None:
    if not selected_track_id:
        return
    if not any(track.id == selected_track_id for track in sequence.tracks):
        raise ValueError("The selected editor track no longer exists.")


def validate_clip_timings(sequence: ProjectEditorSequence) -> None:
    for track in sequence.tracks:
        previous_end = 0.0
        for clip in sorted(track.clips, key=lambda item: item.timeline_start):
            validate_track_clip_kind(track.kind, clip.kind)
            minimum_duration = min_duration_for_clip(clip)
            if clip.timeline_end - clip.timeline_start < minimum_duration:
                raise ValueError(f"Each {clip.kind.replace('_', ' ')} clip must be at least {minimum_duration:.1f} seconds long.")
            if clip.timeline_start < previous_end - TIMING_EPSILON:
                raise ValueError("Clips cannot overlap within the same track.")
            validate_clip_source_bounds(clip)
            validate_clip_asset_metadata(clip)
            previous_end = clip.timeline_end


def validate_clip_source_bounds(clip: EditorClipRecord) -> None:
    if clip.kind in {"inserted_card", "caption", "voiceover", "media_audio", "text_overlay", "shape_overlay", "effect_overlay"}:
        if clip.source_start is not None or clip.source_end is not None:
            raise ValueError("Non-source clips cannot point to source media bounds.")
        return
    if clip.kind == "media_video" and clip.source_start is None and clip.source_end is None:
        return
    if clip.source_start is None or clip.source_end is None:
        raise ValueError("Source-backed clips must declare source bounds.")
    if clip.source_end < clip.source_start:
        raise ValueError("Clip source bounds are invalid.")


def validate_track_clip_kind(track_kind: str, clip_kind: str) -> None:
    allowed = {
        "video": {"source_video", "inserted_card", "media_video"},
        "audio": {"voiceover", "media_audio"},
        "caption": {"caption"},
        "overlay": {"inserted_card", "text_overlay", "shape_overlay", "effect_overlay"},
    }
    if clip_kind not in allowed.get(track_kind, set()):
        raise ValueError(f"{track_kind.title()} tracks cannot contain {clip_kind.replace('_', ' ')} clips.")


def validate_clip_asset_metadata(clip: EditorClipRecord) -> None:
    if clip.kind in {"media_audio", "media_video"} and not clip.asset_path:
        raise ValueError("Uploaded or imported media clips must include an asset path.")
    if clip.kind in {"text_overlay", "shape_overlay", "effect_overlay"} and not (clip.text or clip.title):
        raise ValueError("Overlay clips must include title or text content.")


def validate_sequence_alignment(state: ProjectEditorState) -> None:
    if state.sequence is None:
        return
    scene_clips = clip_map(state.sequence, "video")
    for scene in state.scenes:
        clip = scene_clips.get(scene.id)
        if clip is None:
            raise ValueError("Each editor scene must have a matching video clip.")
        if abs(clip.timeline_start - scene.start) > TIMING_EPSILON or abs(clip.timeline_end - scene.end) > TIMING_EPSILON:
            raise ValueError("Scene timings must match the sequence video track.")
    caption_clips = clip_map(state.sequence, "caption")
    for caption in state.captions:
        clip = caption_clips.get(caption.id)
        if clip is None:
            raise ValueError("Each caption must have a matching caption clip.")
        if abs(clip.timeline_start - caption.start) > TIMING_EPSILON or abs(clip.timeline_end - caption.end) > TIMING_EPSILON:
            raise ValueError("Caption timings must match the sequence caption track.")
    validate_audio_clips(state.sequence)


def clip_map(sequence: ProjectEditorSequence, kind: str) -> dict[str, EditorClipRecord]:
    result: dict[str, EditorClipRecord] = {}
    for track in sequence.tracks:
        if track.kind != kind:
            continue
        for clip in track.clips:
            scene_key = clip.scene_id if kind == "video" else clip.id.replace("caption-clip-", "", 1)
            result[scene_key or clip.id] = clip
    return result


def validate_audio_clips(sequence: ProjectEditorSequence) -> None:
    for track in sequence.tracks:
        if track.kind != "audio":
            continue
        for clip in track.clips:
            if clip.kind not in {"voiceover", "media_audio"}:
                raise ValueError("Audio tracks can only contain voiceover or uploaded audio clips.")
            if clip.kind == "voiceover" and clip.scene_id is None:
                raise ValueError("Voiceover clips must reference a scene.")


def min_duration_for_clip(clip: EditorClipRecord) -> float:
    return MIN_CAPTION_SECONDS if clip.kind == "caption" else MIN_SCENE_SECONDS


def project_duration_limit(project: ProjectRecord) -> float:
    candidates = [
        project.preview_video.duration_seconds if project.preview_video else 0.0,
        project.edit_plan.total_duration_seconds if project.edit_plan else 0.0,
        project.voiceover.duration_seconds if project.voiceover else 0.0,
        project.transcript[-1].end if project.transcript else 0.0,
        project.guide.steps[-1].end if project.guide and project.guide.steps else 0.0,
    ]
    return max([candidate for candidate in candidates if candidate > 0], default=12.0)
