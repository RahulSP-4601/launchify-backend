from __future__ import annotations

from typing import DefaultDict

from app.models.project_editor import EditorClipRecord, EditorSceneRecord, EditorTrackRecord, ProjectEditorState
from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, ProjectRecord


def build_render_timeline(
    project: ProjectRecord,
    editor_state: ProjectEditorState | None,
) -> dict[str, object]:
    if editor_state and editor_state.scenes:
        return build_editor_timeline(project, editor_state)
    return build_legacy_timeline(project.edit_plan)


def build_editor_timeline(
    project: ProjectRecord,
    editor_state: ProjectEditorState,
) -> dict[str, object]:
    if editor_state.sequence and editor_state.sequence.tracks:
        return build_sequence_timeline(project, editor_state)
    source_spans = derive_source_spans(editor_state)
    edit_plan = project.edit_plan
    global_zooms = collect_zooms(edit_plan)
    global_highlights = collect_highlights(edit_plan)
    scenes = [
        build_timeline_scene(scene, source_spans[scene.id], scene_captions(editor_state, scene.id), global_zooms, global_highlights)
        for scene in editor_state.scenes
    ]
    return {
        "scenes": scenes,
        "total_duration_seconds": round(editor_state.scenes[-1].end if editor_state.scenes else 0.0, 2),
    }


def build_sequence_timeline(
    project: ProjectRecord,
    editor_state: ProjectEditorState,
) -> dict[str, object]:
    sequence = editor_state.sequence
    if sequence is None:
        return build_editor_timeline(project, editor_state.model_copy(update={"sequence": None}))
    edit_plan = project.edit_plan
    global_zooms = collect_zooms(edit_plan)
    global_highlights = collect_highlights(edit_plan)
    caption_clips: dict[str, list[EditorClipRecord]] = {
        clip.scene_id: []
        for track in sequence.tracks
        if track.kind == "caption"
        for clip in track.clips
        if clip.scene_id is not None
    }
    for track in sequence.tracks:
        if track.kind != "caption":
            continue
        for clip in track.clips:
            if clip.scene_id is None:
                continue
            caption_clips.setdefault(clip.scene_id, []).append(clip)
    primary_video_track = select_primary_video_track(editor_state)
    scenes = [
        clip_timeline_scene(clip, caption_clips.get(clip.scene_id or "", []), global_zooms, global_highlights)
        for clip in (primary_video_track.clips if primary_video_track else [])
    ]
    return {
        "scenes": scenes,
        "tracks": [
            {
                "clips": [timeline_track_clip(clip) for clip in track.clips],
                "id": track.id,
                "kind": track.kind,
                "locked": track.locked,
                "muted": track.muted,
                "name": track.name,
            }
            for track in sequence.tracks
        ],
        "total_duration_seconds": round(sequence.duration_seconds, 2),
    }


def select_primary_video_track(editor_state: ProjectEditorState) -> EditorTrackRecord | None:
    sequence = editor_state.sequence
    if sequence is None:
        return None
    canonical_track = next(
        (track for track in sequence.tracks if track.id == "track-video-1" and track.kind == "video" and not track.muted),
        None,
    )
    if canonical_track is not None:
        return canonical_track
    return next((track for track in sequence.tracks if track.kind == "video" and not track.muted), None)


def build_legacy_timeline(edit_plan: EditPlanRecord | None) -> dict[str, object]:
    scenes = [legacy_timeline_scene(scene) for scene in (edit_plan.scenes if edit_plan else [])]
    return {
        "scenes": scenes,
        "tracks": [],
        "total_duration_seconds": round(edit_plan.total_duration_seconds if edit_plan else 0.0, 2),
    }


def derive_source_spans(editor_state: ProjectEditorState) -> dict[str, tuple[float, float]]:
    source_cursor = 0.0
    spans: dict[str, tuple[float, float]] = {}
    for scene in editor_state.scenes:
        duration = max(scene.end - scene.start, 0.0)
        if scene.source == "inserted":
            spans[scene.id] = (round(source_cursor, 2), round(source_cursor, 2))
            continue
        source_start = round(source_cursor, 2)
        source_end = round(source_cursor + duration, 2)
        spans[scene.id] = (source_start, source_end)
        source_cursor = source_end
    return spans


def scene_captions(editor_state: ProjectEditorState, scene_id: str) -> list[dict[str, object]]:
    return [
        {
            "emphasis_words": [],
            "end": round(caption.end - scene.start, 2),
            "start": round(caption.start - scene.start, 2),
            "text": caption.text,
            "variant": "body",
        }
        for scene in editor_state.scenes
        if scene.id == scene_id
        for caption in editor_state.captions
        if caption.scene_id == scene_id
    ]


def build_timeline_scene(
    scene: EditorSceneRecord,
    source_span: tuple[float, float],
    captions: list[dict[str, object]],
    global_zooms: list[EditPlanZoom],
    global_highlights: list[EditPlanHighlight],
) -> dict[str, object]:
    source_start, source_end = source_span
    return {
        "camera_mode": "static" if scene.source == "inserted" else "focus",
        "captions": captions,
        "editor_end": scene.end,
        "editor_start": scene.start,
        "highlights": localize_highlights(global_highlights, source_start, source_end),
        "is_inserted": scene.source == "inserted",
        "on_screen_text": scene.on_screen_text,
        "purpose": scene.title,
        "render_duration_seconds": round(scene.end - scene.start, 2),
        "scene_number": scene.scene_number,
        "scene_role": "explanation" if scene.source == "inserted" else "action",
        "source": scene.source,
        "source_end": source_end,
        "source_excerpt": scene.spoken_line,
        "source_start": source_start,
        "spoken_line": scene.spoken_line,
        "title": scene.title,
        "transition_duration_seconds": 0.28,
        "transition_style": "fade",
        "zooms": localize_zooms(global_zooms, source_start, source_end),
    }


def clip_timeline_scene(
    clip: EditorClipRecord,
    captions: list[EditorClipRecord],
    global_zooms: list[EditPlanZoom],
    global_highlights: list[EditPlanHighlight],
) -> dict[str, object]:
    source_start = clip.source_start or 0.0
    source_end = clip.source_end or source_start
    return {
        "camera_mode": "static" if clip.kind == "inserted_card" else "focus",
        "captions": [
            {
                "emphasis_words": [],
                "end": round(caption.timeline_end - clip.timeline_start, 2),
                "start": round(caption.timeline_start - clip.timeline_start, 2),
                "text": caption.text,
                "variant": "body",
            }
            for caption in captions
        ],
        "editor_end": clip.timeline_end,
        "editor_start": clip.timeline_start,
        "highlights": localize_highlights(global_highlights, source_start, source_end),
        "is_inserted": clip.kind == "inserted_card",
        "on_screen_text": clip.text,
        "purpose": clip.title,
        "render_duration_seconds": round(clip.timeline_end - clip.timeline_start, 2),
        "scene_number": inferred_scene_number(clip),
        "scene_role": "explanation" if clip.kind == "inserted_card" else "action",
        "source": "inserted" if clip.kind == "inserted_card" else "edit_plan",
        "source_end": source_end,
        "source_excerpt": clip.text,
        "source_start": source_start,
        "spoken_line": clip.text,
        "title": clip.title,
        "transition_duration_seconds": 0.28,
        "transition_style": "fade",
        "zooms": localize_zooms(global_zooms, source_start, source_end),
    }


def timeline_track_clip(clip: EditorClipRecord) -> dict[str, object]:
    return {
        "id": clip.id,
        "kind": clip.kind,
        "locked": clip.locked,
        "muted": clip.muted,
        "scene_id": clip.scene_id,
        "source_end": clip.source_end,
        "source_start": clip.source_start,
        "text": clip.text,
        "timeline_end": clip.timeline_end,
        "timeline_start": clip.timeline_start,
        "title": clip.title,
        "track_id": clip.track_id,
    }


def inferred_scene_number(clip: EditorClipRecord) -> int:
    if clip.scene_id and clip.scene_id.startswith("scene-"):
        try:
            return int(clip.scene_id.split("scene-", 1)[1].split("-", 1)[0])
        except ValueError:
            return 1
    return 1


def legacy_timeline_scene(scene: EditPlanScene) -> dict[str, object]:
    return {
        "camera_mode": scene.camera_mode,
        "captions": [caption.model_dump(mode="json") for caption in localize_captions(scene.captions, scene.start)],
        "editor_end": scene.end,
        "editor_start": scene.start,
        "highlights": [highlight.model_dump(mode="json") for highlight in overlapping_highlights(scene.highlights, scene.start, scene.end)],
        "is_inserted": False,
        "on_screen_text": scene.on_screen_text,
        "purpose": scene.purpose,
        "render_duration_seconds": scene.render_duration_seconds,
        "scene_number": scene.scene_number,
        "scene_role": scene.scene_role,
        "source": "edit_plan",
        "source_end": scene.end,
        "source_excerpt": scene.source_excerpt,
        "source_start": scene.start,
        "spoken_line": scene.spoken_line,
        "title": scene.title,
        "transition_duration_seconds": scene.transition_duration_seconds,
        "transition_style": scene.transition_style,
        "zooms": [zoom.model_dump(mode="json") for zoom in overlapping_zooms(scene.zooms, scene.start, scene.end)],
    }


def collect_zooms(edit_plan: EditPlanRecord | None) -> list[EditPlanZoom]:
    return [zoom for scene in (edit_plan.scenes if edit_plan else []) for zoom in scene.zooms]


def collect_highlights(edit_plan: EditPlanRecord | None) -> list[EditPlanHighlight]:
    return [highlight for scene in (edit_plan.scenes if edit_plan else []) for highlight in scene.highlights]


def localize_captions(captions: list[EditPlanCaption], start: float) -> list[EditPlanCaption]:
    return [caption.model_copy(update={"start": round(caption.start - start, 2), "end": round(caption.end - start, 2)}) for caption in captions]


def localize_zooms(zooms: list[EditPlanZoom], source_start: float, source_end: float) -> list[dict[str, object]]:
    return [zoom.model_dump(mode="json") for zoom in overlapping_zooms(zooms, source_start, source_end)]


def localize_highlights(highlights: list[EditPlanHighlight], source_start: float, source_end: float) -> list[dict[str, object]]:
    return [highlight.model_dump(mode="json") for highlight in overlapping_highlights(highlights, source_start, source_end)]


def overlapping_zooms(zooms: list[EditPlanZoom], source_start: float, source_end: float) -> list[EditPlanZoom]:
    return [localize_zoom(zoom, source_start, source_end) for zoom in zooms if overlaps(zoom.start, zoom.end, source_start, source_end)]


def overlapping_highlights(highlights: list[EditPlanHighlight], source_start: float, source_end: float) -> list[EditPlanHighlight]:
    return [localize_highlight(item, source_start, source_end) for item in highlights if overlaps(item.start, item.end, source_start, source_end)]


def localize_zoom(zoom: EditPlanZoom, source_start: float, source_end: float) -> EditPlanZoom:
    return zoom.model_copy(update=localized_edges(zoom.start, zoom.end, source_start, source_end))


def localize_highlight(highlight: EditPlanHighlight, source_start: float, source_end: float) -> EditPlanHighlight:
    return highlight.model_copy(update=localized_edges(highlight.start, highlight.end, source_start, source_end))


def localized_edges(start: float, end: float, source_start: float, source_end: float) -> dict[str, float]:
    return {
        "end": round(min(end, source_end) - source_start, 2),
        "start": round(max(start, source_start) - source_start, 2),
    }


def overlaps(start: float, end: float, source_start: float, source_end: float) -> bool:
    return end > source_start and start < source_end
