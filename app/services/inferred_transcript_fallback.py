from __future__ import annotations

from typing import Callable

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, SessionEventRecord, SessionEventType, SessionTargetRecord, UiElementRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import classify_action
from app.services.inferred_recording_support import actionable_label, fallback_intent_label, normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent

CLICK_WORDS = frozenset({"click", "tap", "press", "select", "choose", "continue", "open", "start", "launch", "login", "log in"})
INPUT_WORDS = frozenset({"type", "enter", "write", "search", "email", "password", "name"})
NAVIGATION_WORDS = frozenset({"page", "screen", "dashboard", "home", "next", "continue", "course"})
AUTH_TOKENS = frozenset({"account", "continue", "create", "existing", "google", "log", "login", "sign", "signup"})


def backfill_transcript_scene_events(
    selected: list[SessionEventRecord],
    launch_script: LaunchScriptRecord,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
    dedupe_events: Callable[[list[SessionEventRecord]], list[SessionEventRecord]],
) -> list[SessionEventRecord]:
    covered = {int(event.metadata.get("scene_number", "0") or 0) for event in selected}
    supplemented = selected[:]
    fallback_time = 0.0
    for scene in launch_script.scenes:
        if scene.scene_number in covered:
            fallback_time = max_known_scene_time(fallback_time, analyses_by_scene.get(scene.scene_number))
            continue
        event = transcript_scene_event(
            scene,
            analyses_by_scene.get(scene.scene_number),
            fallback_time,
            viewport_width,
            viewport_height,
        )
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
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord | None:
    target, grounding_source, score = fallback_scene_target(scene, analysis, viewport_width, viewport_height)
    label = target.label or scene.on_screen_text
    if not actionable_label(label):
        return None
    timestamp = inferred_scene_timestamp(scene, analysis, fallback_time)
    event_type = transcript_scene_event_type(scene, label)
    action_class = classify_action(event_type, label, scene.spoken_line, scene.source_excerpt)
    return SessionEventRecord(
        type=event_type,
        timestamp=timestamp,
        x=target_center_x(target),
        y=target_center_y(target),
        target=target,
        metadata={
            "inferred": "true",
            "grounding_source": grounding_source,
            "scene_number": str(scene.scene_number),
            "synthetic_selector": f"[data-launchify-scene='{scene.scene_number}']",
            "score": score,
            "action_class": action_class,
            "transcript_excerpt": (scene.source_excerpt or scene.spoken_line)[:180],
        },
    )


def fallback_scene_target(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
    viewport_width: int,
    viewport_height: int,
) -> tuple[SessionTargetRecord, str, str]:
    visual_target = visual_fallback_target(scene, analysis, viewport_width, viewport_height)
    if visual_target is not None:
        return visual_target, "visual_fallback", "0.58"
    label = fallback_intent_label(scene.spoken_line, scene.source_excerpt) or scene.on_screen_text
    return (
        SessionTargetRecord(label=label, text=scene.source_excerpt or scene.spoken_line, role="control"),
        "transcript_fallback",
        "0.46",
    )


def visual_fallback_target(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
    viewport_width: int,
    viewport_height: int,
) -> SessionTargetRecord | None:
    if analysis is None:
        return None
    resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    candidates = [
        element
        for frame in analysis.frames
        for element in frame.ui_elements
        if element.label.strip()
    ]
    if not candidates:
        return None
    ranked = sorted(candidates, key=lambda element: visual_fallback_rank(element, resolution.preferred_clause), reverse=True)
    top = ranked[0]
    if visual_fallback_rank(top, resolution.preferred_clause)[0] < 0.3:
        return None
    return SessionTargetRecord(
        label=top.label.strip(),
        text=top.label.strip(),
        role=top.role or "control",
        bbox_x=round(top.box.x * viewport_width, 2),
        bbox_y=round(top.box.y * viewport_height, 2),
        bbox_width=round(top.box.width * viewport_width, 2),
        bbox_height=round(top.box.height * viewport_height, 2),
    )


def visual_fallback_rank(
    element: UiElementRecord,
    preferred_clause: str,
) -> tuple[float, float, float]:
    tokens = set(normalize_label(element.label).split())
    clause_tokens = set(normalize_label(preferred_clause).split())
    auth_bonus = 1.0 if tokens & AUTH_TOKENS else 0.0
    branch_bonus = auth_branch_rank(tokens, clause_tokens)
    specificity = len(tokens) / 6.0
    return (branch_bonus + auth_bonus, element.confidence, specificity)


def auth_branch_rank(tokens: set[str], clause_tokens: set[str]) -> float:
    if {"existing", "login", "log"} & clause_tokens:
        score = 1.0 if ({"log", "login"} & tokens or "continue" in tokens) else 0.0
        if {"sign", "signup", "create"} & tokens:
            score -= 0.8
        return score
    if {"sign", "signup", "create"} & clause_tokens:
        score = 1.0 if {"sign", "signup", "create"} & tokens else 0.0
        if {"log", "login"} & tokens:
            score -= 0.6
        return score
    return 0.4 if {"log", "login", "google", "continue"} & tokens else 0.0


def target_center_x(target: SessionTargetRecord) -> float | None:
    if target.bbox_x is None or target.bbox_width is None:
        return None
    return round(target.bbox_x + target.bbox_width / 2, 2)


def target_center_y(target: SessionTargetRecord) -> float | None:
    if target.bbox_y is None or target.bbox_height is None:
        return None
    return round(target.bbox_y + target.bbox_height / 2, 2)


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
