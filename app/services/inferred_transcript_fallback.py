from __future__ import annotations

from typing import Callable

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, SessionEventRecord, SessionEventType, SessionTargetRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import classify_action
from app.services.inferred_recording_support import actionable_label, fallback_intent_label

CLICK_WORDS = frozenset({"click", "tap", "press", "select", "choose", "continue", "open", "start", "launch", "login", "log in"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password", "name"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "home", "next", "continue", "course"})


def backfill_transcript_scene_events(
    selected: list[SessionEventRecord],
    launch_script: LaunchScriptRecord,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    dedupe_events: Callable[[list[SessionEventRecord]], list[SessionEventRecord]],
) -> list[SessionEventRecord]:
    covered = {int(event.metadata.get("scene_number", "0") or 0) for event in selected}
    supplemented = selected[:]
    fallback_time = 0.0
    for scene in launch_script.scenes:
        if scene.scene_number in covered:
            fallback_time = max_known_scene_time(fallback_time, analyses_by_scene.get(scene.scene_number))
            continue
        event = transcript_scene_event(scene, analyses_by_scene.get(scene.scene_number), fallback_time)
        if event is None:
            continue
        supplemented.append(event)
        fallback_time = max(fallback_time, event.timestamp)
    return dedupe_events(sorted(supplemented, key=lambda item: item.timestamp))


def max_known_scene_time(fallback_time: float, analysis: VisualSceneAnalysisRecord | None) -> float:
    return max(fallback_time, analysis.end if analysis is not None else fallback_time)


def transcript_scene_event(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
    fallback_time: float,
) -> SessionEventRecord | None:
    label = fallback_intent_label(scene.spoken_line, scene.source_excerpt) or scene.on_screen_text
    if not actionable_label(label):
        return None
    timestamp = inferred_scene_timestamp(scene, analysis, fallback_time)
    event_type = transcript_scene_event_type(scene, label)
    action_class = classify_action(event_type, label, scene.spoken_line, scene.source_excerpt)
    return SessionEventRecord(
        type=event_type,
        timestamp=timestamp,
        target=SessionTargetRecord(label=label, text=scene.source_excerpt or scene.spoken_line, role="control"),
        metadata={
            "inferred": "true",
            "grounding_source": "transcript_fallback",
            "scene_number": str(scene.scene_number),
            "synthetic_selector": f"[data-launchify-scene='{scene.scene_number}']",
            "score": "0.46",
            "action_class": action_class,
            "transcript_excerpt": (scene.source_excerpt or scene.spoken_line)[:180],
        },
    )


def inferred_scene_timestamp(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
    fallback_time: float,
) -> float:
    if analysis is not None:
        midpoint = analysis.start + max(analysis.end - analysis.start, 0.8) * 0.5
        return round(max(midpoint, fallback_time + 0.9), 2)
    return round(max(fallback_time + 0.9, scene.estimated_duration_seconds * max(scene.scene_number - 0.5, 1.0)), 2)


def transcript_scene_event_type(
    scene: LaunchScriptScene,
    label: str,
) -> SessionEventType:
    combined = f"{scene.spoken_line} {scene.source_excerpt} {label}".lower()
    if any(token in combined for token in INPUT_WORDS):
        return "input"
    if any(token in combined for token in NAVIGATION_WORDS):
        return "navigation"
    if any(token in combined for token in CLICK_WORDS):
        return "click"
    return "focus"
