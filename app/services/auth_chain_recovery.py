from __future__ import annotations

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.canonical_consistency import branch_family, event_branch_family
from app.services.inferred_transcript_fallback import transcript_scene_event
from app.services.scene_intent_resolver import resolve_scene_intent

AUTH_INTENTS = {"auth", "account_existing", "account_create"}


def recover_auth_chain_events(
    events: list[SessionEventRecord],
    launch_script: LaunchScriptRecord,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
) -> list[SessionEventRecord]:
    auth_scenes = [scene for scene in launch_script.scenes if is_auth_scene(scene)]
    if len(auth_scenes) < 2:
        return events
    recovered = events[:]
    covered = {int(event.metadata.get("scene_number", "0") or 0) for event in events}
    dominant_branch = resolved_auth_branch(events) or auth_scene_branch(auth_scenes)
    for scene in auth_scenes:
        if scene.scene_number in covered:
            continue
        if branch_conflicts(auth_scene_intent(scene), dominant_branch):
            continue
        fallback = transcript_scene_event(
            scene,
            analyses_by_scene.get(scene.scene_number),
            auth_fallback_time(scene, auth_scenes, recovered, analyses_by_scene),
            viewport_width,
            viewport_height,
        )
        if fallback is None:
            continue
        if branch_conflicts(event_branch_family(fallback), dominant_branch):
            continue
        if skip_fallback_scene(scene, dominant_branch, recovered):
            continue
        recovered.append(fallback)
        covered.add(scene.scene_number)
    return sorted(recovered, key=lambda event: event.timestamp)


def resolved_auth_branch(events: list[SessionEventRecord]) -> str:
    branches = [event_branch_family(event) for event in events if event_branch_family(event) != "generic"]
    if not branches:
        return ""
    return branches[-1]


def is_auth_scene(scene: LaunchScriptScene) -> bool:
    return auth_scene_intent(scene) != "generic" or resolve_scene_intent(scene.source_excerpt, scene.spoken_line).intent in AUTH_INTENTS


def auth_scene_branch(auth_scenes: list[LaunchScriptScene]) -> str:
    intents = [auth_scene_intent(scene) for scene in auth_scenes if auth_scene_intent(scene) != "generic"]
    return intents[-1] if intents else "generic"


def auth_scene_intent(scene: LaunchScriptScene) -> str:
    scene_text = f"{scene.on_screen_text} {scene.source_excerpt} {scene.spoken_line}".lower()
    family = branch_family(scene_text)
    if family != "generic":
        return family
    intent = resolve_scene_intent(scene.source_excerpt, scene.spoken_line).intent
    if intent == "account_existing":
        return "existing"
    if intent == "account_create":
        return "create"
    return "generic"


def branch_conflicts(branch: str, dominant_branch: str) -> bool:
    return branch != "generic" and dominant_branch != "generic" and branch != dominant_branch


def skip_fallback_scene(
    scene: LaunchScriptScene,
    dominant_branch: str,
    events: list[SessionEventRecord],
) -> bool:
    if dominant_branch == "generic":
        return False
    auth_events = [event for event in events if event_branch_family(event) == dominant_branch]
    if len(auth_events) < 2:
        return False
    return auth_scene_intent(scene) != dominant_branch


def auth_fallback_time(
    scene: LaunchScriptScene,
    auth_scenes: list[LaunchScriptScene],
    events: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> float:
    previous = max((event.timestamp for event in events if int(event.metadata.get("scene_number", "0") or 0) < scene.scene_number), default=0.0)
    next_event = min((event.timestamp for event in events if int(event.metadata.get("scene_number", "0") or 0) > scene.scene_number), default=0.0)
    analysis = analyses_by_scene.get(scene.scene_number)
    if analysis is not None:
        midpoint = round((analysis.start + analysis.end) / 2, 2)
        if next_event > previous:
            return max(previous + 0.35, min(midpoint, next_event - 0.45))
        return max(previous + 0.35, midpoint)
    prior_scenes = [candidate for candidate in auth_scenes if candidate.scene_number < scene.scene_number]
    offset = sum(candidate.estimated_duration_seconds for candidate in prior_scenes)
    if next_event > previous:
        return max(previous + 0.35, min(previous + offset, next_event - 0.45))
    return max(previous + 0.35, previous + offset)
