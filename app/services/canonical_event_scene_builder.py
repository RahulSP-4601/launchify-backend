from __future__ import annotations

from collections.abc import Callable

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, SessionEventRecord
from app.services.inferred_recording_support import normalize_label

SCENE_VARIANT_MULTIPLIER = 100000


def scenes_from_canonical_events(
    launch_script: LaunchScriptRecord,
    events: list[SessionEventRecord],
    event_scene_label: Callable[[SessionEventRecord | None], str],
    event_scene_purpose: Callable[[str, SessionEventRecord | None], str],
) -> list[LaunchScriptScene]:
    canonical_events = distinct_canonical_events(events)
    if len(canonical_events) < 2:
        return []
    scene_event_counts = canonical_event_counts_by_scene(canonical_events)
    scene_occurrences: dict[int, int] = {}
    scenes: list[LaunchScriptScene] = []
    for event in canonical_events:
        source_number = event_scene_number(event)
        base_scene = source_scene_for_event(launch_script, event)
        label = event_scene_label(event)
        purpose = event_scene_purpose(label, event)
        spoken_line = preferred_spoken_line(base_scene, event, event_scene_label)
        duration = allocated_scene_duration(base_scene, event, scene_event_counts)
        occurrence = scene_occurrences.get(source_number, 0)
        scene_occurrences[source_number] = occurrence + 1
        scenes.append(
            LaunchScriptScene(
                scene_number=canonical_scene_number(source_number, occurrence),
                purpose=purpose,
                spoken_line=spoken_line,
                on_screen_text=label,
                source_excerpt=label,
                estimated_duration_seconds=duration,
            )
        )
    return scenes


def distinct_canonical_events(events: list[SessionEventRecord]) -> list[SessionEventRecord]:
    selected: list[SessionEventRecord] = []
    for event in sorted(events, key=lambda item: item.timestamp):
        canonical = normalize_label(event.metadata.get("canonical_label", ""))
        if not canonical:
            continue
        if duplicate_canonical_event(selected, event, canonical):
            continue
        selected.append(event)
    return selected


def duplicate_canonical_event(
    selected: list[SessionEventRecord],
    candidate: SessionEventRecord,
    canonical_label: str,
) -> bool:
    candidate_scene = event_scene_number(candidate)
    for existing in reversed(selected):
        existing_label = normalize_label(existing.metadata.get("canonical_label", ""))
        if existing_label != canonical_label:
            continue
        same_scene = event_scene_number(existing) == candidate_scene
        close_in_time = abs(existing.timestamp - candidate.timestamp) <= 4.5
        if same_scene and close_in_time:
            return True
        break
    return False


def canonical_event_counts_by_scene(
    events: list[SessionEventRecord],
) -> dict[int, int]:
    counts: dict[int, int] = {}
    for event in events:
        scene_number = event_scene_number(event)
        if scene_number <= 0:
            continue
        counts[scene_number] = counts.get(scene_number, 0) + 1
    return counts


def allocated_scene_duration(
    scene: LaunchScriptScene | None,
    event: SessionEventRecord,
    scene_event_counts: dict[int, int],
) -> float:
    if scene is None:
        return 3.0
    scene_number = event_scene_number(event)
    event_count = max(scene_event_counts.get(scene_number, 1), 1)
    if event_count == 1:
        return scene.estimated_duration_seconds
    split_duration = scene.estimated_duration_seconds / event_count
    return round(max(split_duration, 0.8), 2)


def source_scene_for_event(
    launch_script: LaunchScriptRecord,
    event: SessionEventRecord,
) -> LaunchScriptScene | None:
    scene_number = event_scene_number(event)
    return next((scene for scene in launch_script.scenes if scene.scene_number == scene_number), None)


def preferred_spoken_line(
    scene: LaunchScriptScene | None,
    event: SessionEventRecord,
    event_scene_label: Callable[[SessionEventRecord | None], str],
) -> str:
    if scene is not None and scene.spoken_line.strip():
        return scene.spoken_line
    return event.metadata.get("transcript_excerpt", "").strip() or event_scene_label(event)


def event_scene_number(event: SessionEventRecord) -> int:
    try:
        return int(event.metadata.get("scene_number", "0") or 0)
    except (TypeError, ValueError):
        return 0


def canonical_scene_number(source_scene_number: int, occurrence: int) -> int:
    if occurrence <= 0:
        return source_scene_number
    return source_scene_number * SCENE_VARIANT_MULTIPLIER + occurrence


def source_scene_number(scene_number: int) -> int:
    if scene_number >= SCENE_VARIANT_MULTIPLIER:
        return scene_number // SCENE_VARIANT_MULTIPLIER
    return scene_number
