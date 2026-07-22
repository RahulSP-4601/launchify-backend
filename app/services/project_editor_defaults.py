from __future__ import annotations

from app.models.project_editor import (
    EditorCaptionRecord,
    EditorSceneRecord,
    ProjectEditorState,
)
from app.models.projects import EditPlanScene, LaunchScriptScene, ProjectRecord, TranscriptSegment


def build_project_editor_state(project: ProjectRecord) -> ProjectEditorState:
    scenes = build_editor_scenes(project)
    return ProjectEditorState(
        aspect_ratio="16:9",
        captions=build_editor_captions(project, scenes),
        selected_scene_id=scenes[0].id if scenes else "",
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
    return current_state.model_copy(
        update={
            "captions": sorted([*kept_captions, *restored_captions], key=lambda caption: (caption.start, caption.end)),
            "scenes": [
                restored_scene if scene.id == scene_id else scene
                for scene in current_state.scenes
            ],
            "selected_scene_id": scene_id,
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
