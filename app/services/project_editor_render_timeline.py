from __future__ import annotations

from typing import DefaultDict, cast

from app.models.project_editor import EditorClipRecord, EditorSceneRecord, EditorTrackRecord, ProjectEditorState
from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, ProjectRecord
from app.services.storage import create_signed_asset_url

TimelineCaption = dict[str, object]
TimelineScene = dict[str, object]


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
    caption_clips = [
        clip
        for track in sequence.tracks
        if track.kind == "caption" and not track.muted
        for clip in track.clips
        if not clip.muted
    ]
    scenes = composite_timeline_scenes(sequence.tracks, caption_clips, global_zooms, global_highlights)
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


def composite_timeline_scenes(
    tracks: list[EditorTrackRecord],
    caption_clips: list[EditorClipRecord],
    global_zooms: list[EditPlanZoom],
    global_highlights: list[EditPlanHighlight],
) -> list[TimelineScene]:
    video_tracks = [track for track in tracks if track.kind == "video" and not track.muted]
    if not video_tracks:
        return []
    boundaries = sorted({
        round(clip.timeline_start, 2)
        for track in video_tracks
        for clip in track.clips
    } | {
        round(clip.timeline_end, 2)
        for track in video_tracks
        for clip in track.clips
    })
    scenes: list[TimelineScene] = []
    for index in range(len(boundaries) - 1):
        segment_start = boundaries[index]
        segment_end = boundaries[index + 1]
        if segment_end <= segment_start:
            continue
        active_clip = active_video_clip(video_tracks, (segment_start + segment_end) / 2)
        if active_clip is None:
            continue
        scenes.append(
            clip_timeline_scene(
                active_clip,
                interval_caption_clips(caption_clips, segment_start, segment_end),
                global_zooms,
                global_highlights,
                segment_start,
                segment_end,
                len(scenes) + 1,
            )
        )
    return merge_adjacent_scenes(scenes)


def active_video_clip(video_tracks: list[EditorTrackRecord], time: float) -> EditorClipRecord | None:
    for track in reversed(video_tracks):
        for clip in track.clips:
            if clip.muted:
                continue
            if clip.timeline_start <= time < clip.timeline_end:
                return clip
    return None


def interval_caption_clips(
    captions: list[EditorClipRecord],
    start: float,
    end: float,
) -> list[EditorClipRecord]:
    result: list[EditorClipRecord] = []
    for caption in captions:
        if caption.timeline_end <= start or caption.timeline_start >= end:
            continue
        result.append(
            caption.model_copy(
                update={
                    "timeline_end": round(min(caption.timeline_end, end), 2),
                    "timeline_start": round(max(caption.timeline_start, start), 2),
                }
            )
        )
    return result


def merge_adjacent_scenes(scenes: list[TimelineScene]) -> list[TimelineScene]:
    if not scenes:
        return []
    merged = [scenes[0]]
    for scene in scenes[1:]:
        previous = merged[-1]
        same_clip = (
            previous.get("scene_id") == scene.get("scene_id")
            and previous.get("clip_kind") == scene.get("clip_kind")
            and abs(float(cast(float, previous["editor_end"])) - float(cast(float, scene["editor_start"]))) < 0.01
        )
        if not same_clip:
            merged.append(scene)
            continue
        previous["editor_end"] = scene["editor_end"]
        previous["render_duration_seconds"] = round(float(cast(float, previous["editor_end"])) - float(cast(float, previous["editor_start"])), 2)
        previous["source_end"] = scene["source_end"]
        previous["captions"] = merge_scene_captions(
            cast(list[TimelineCaption], previous["captions"]),
            cast(list[TimelineCaption], scene["captions"]),
            float(cast(float, previous["editor_start"])),
            float(cast(float, scene["editor_start"])),
        )
    for index, scene in enumerate(merged, start=1):
        scene["scene_number"] = index
    return merged


def merge_scene_captions(
    prior: list[TimelineCaption],
    next_captions: list[TimelineCaption],
    scene_start: float,
    next_scene_start: float,
) -> list[TimelineCaption]:
    merged = [*prior]
    for caption in next_captions:
        rebased = {
            **caption,
            "end": round(float(cast(float, caption["end"])) + next_scene_start - scene_start, 2),
            "start": round(float(cast(float, caption["start"])) + next_scene_start - scene_start, 2),
        }
        absolute_start = round(scene_start + float(cast(float, rebased["start"])), 2)
        absolute_end = round(scene_start + float(cast(float, rebased["end"])), 2)
        duplicate = next(
            (
                item for item in merged
                if round(scene_start + float(cast(float, item["start"])), 2) == absolute_start
                and round(scene_start + float(cast(float, item["end"])), 2) == absolute_end
                and item["text"] == rebased["text"]
            ),
            None,
        )
        if duplicate:
            continue
        merged.append(rebased)
    return sorted(merged, key=lambda item: float(cast(float, item["start"])))


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
    segment_start: float | None = None,
    segment_end: float | None = None,
    scene_number: int | None = None,
) -> dict[str, object]:
    editor_start = segment_start if segment_start is not None else clip.timeline_start
    editor_end = segment_end if segment_end is not None else clip.timeline_end
    source_start, source_end = clip_source_span(clip, editor_start, editor_end)
    return {
        "asset_path": signed_asset_url(clip.asset_path),
        "camera_mode": "static" if clip.kind == "inserted_card" else "focus",
        "captions": clip_captions(captions, editor_start),
        "clip_kind": clip.kind,
        "content_type": clip.content_type,
        "editor_end": editor_end,
        "editor_start": editor_start,
        "highlights": localize_highlights(global_highlights, source_start, source_end),
        "is_inserted": clip.kind == "inserted_card",
        "on_screen_text": clip.text,
        "purpose": clip.title,
        "render_duration_seconds": round(editor_end - editor_start, 2),
        "scene_id": clip.scene_id,
        "scene_number": scene_number or inferred_scene_number(clip),
        "scene_role": "explanation" if clip.kind == "inserted_card" else "action",
        "source": "inserted" if clip.kind == "inserted_card" else "imported" if clip.kind == "media_video" else "edit_plan",
        "source_end": source_end,
        "source_excerpt": clip.text,
        "source_start": source_start,
        "spoken_line": clip.text,
        "title": clip.title,
        "transition_duration_seconds": 0.28,
        "transition_style": "fade",
        "zooms": localize_zooms(global_zooms, source_start, source_end),
    }


def clip_source_span(
    clip: EditorClipRecord,
    editor_start: float,
    editor_end: float,
) -> tuple[float, float]:
    source_offset = max(editor_start - clip.timeline_start, 0.0)
    base_source_start = clip.source_start or 0.0
    source_start = round(base_source_start + source_offset, 2)
    clip_duration = max(editor_end - editor_start, 0.0)
    if clip.source_end is None:
        return source_start, round(source_start + clip_duration, 2)
    return source_start, round(min(clip.source_end, source_start + clip_duration), 2)


def clip_captions(captions: list[EditorClipRecord], editor_start: float) -> list[dict[str, object]]:
    return [
        {
            "emphasis_words": [],
            "end": round(caption.timeline_end - editor_start, 2),
            "start": round(caption.timeline_start - editor_start, 2),
            "text": caption.text,
            "variant": "body",
        }
        for caption in captions
    ]


def timeline_track_clip(clip: EditorClipRecord) -> dict[str, object]:
    return {
        "asset_path": signed_asset_url(clip.asset_path),
        "content_type": clip.content_type,
        "effect_preset": clip.effect_preset,
        "id": clip.id,
        "kind": clip.kind,
        "locked": clip.locked,
        "muted": clip.muted,
        "scene_id": clip.scene_id,
        "source_project_id": clip.source_project_id,
        "source_end": clip.source_end,
        "source_start": clip.source_start,
        "style_preset": clip.style_preset,
        "text": clip.text,
        "timeline_end": clip.timeline_end,
        "timeline_start": clip.timeline_start,
        "title": clip.title,
        "track_id": clip.track_id,
        "volume_percent": clip.volume_percent,
        "fade_in_seconds": clip.fade_in_seconds,
        "fade_out_seconds": clip.fade_out_seconds,
        "loop": clip.loop,
    }


def signed_asset_url(storage_path: str | None) -> str | None:
    return create_signed_asset_url(storage_path) if storage_path else None


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
