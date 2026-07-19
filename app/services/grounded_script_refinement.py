from __future__ import annotations

from app.models.projects import FocusBox, FrameSignalRecord, LaunchScriptRecord, LaunchScriptScene, SessionEventRecord, UiElementRecord, VisualSceneAnalysisRecord
from app.services.generic_target_labeling import should_promote_generic_label, title_case_phrase
from app.services.inferred_recording_support import box_center_delta, normalize_label
from app.services.scene_intent_resolver import SceneIntentResolution, resolve_scene_intent
from app.services.ui_structure_insights import compact_action_target, frame_local_labels, frame_structure, prefers_state_event, structure_state_label
from app.services.visual_target_context import best_matching_entity_label, exact_visual_target_label


def refine_launch_script_with_visuals(
    launch_script: LaunchScriptRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None,
) -> LaunchScriptRecord:
    if not visual_analyses:
        return launch_script
    analyses_by_scene = {analysis.scene_number: analysis for analysis in visual_analyses}
    scenes = [refined_scene(scene, analyses_by_scene.get(scene.scene_number)) for scene in launch_script.scenes]
    return LaunchScriptRecord(
        hook=launch_script.hook,
        summary=launch_script.summary,
        title_options=launch_script.title_options,
        scenes=scenes,
        cta=launch_script.cta,
        notes=launch_script.notes,
    )


def refine_launch_script_with_events(
    launch_script: LaunchScriptRecord,
    events: list[SessionEventRecord] | None,
) -> LaunchScriptRecord:
    if not events:
        return launch_script
    events_by_scene = preferred_events_by_scene(events)
    scenes = [refined_scene_from_event(scene, events_by_scene.get(scene.scene_number)) for scene in launch_script.scenes]
    return LaunchScriptRecord(
        hook=launch_script.hook,
        summary=launch_script.summary,
        title_options=launch_script.title_options,
        scenes=scenes,
        cta=launch_script.cta,
        notes=launch_script.notes,
    )


def refined_scene(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
) -> LaunchScriptScene:
    label = refined_scene_label(scene, analysis)
    if not label:
        return scene
    return LaunchScriptScene(
        scene_number=scene.scene_number,
        purpose=refined_scene_purpose(scene.purpose, label, analysis),
        spoken_line=scene.spoken_line,
        on_screen_text=label,
        source_excerpt=refined_source_excerpt(scene.source_excerpt, label),
        estimated_duration_seconds=scene.estimated_duration_seconds,
    )


def refined_scene_from_event(
    scene: LaunchScriptScene,
    event: SessionEventRecord | None,
) -> LaunchScriptScene:
    label = event_scene_label(event)
    if not label:
        return scene
    return LaunchScriptScene(
        scene_number=scene.scene_number,
        purpose=event_scene_purpose(label, event),
        spoken_line=scene.spoken_line,
        on_screen_text=label,
        source_excerpt=label,
        estimated_duration_seconds=scene.estimated_duration_seconds,
    )


def preferred_events_by_scene(
    events: list[SessionEventRecord],
) -> dict[int, SessionEventRecord]:
    by_scene: dict[int, SessionEventRecord] = {}
    for event in events:
        scene_number = int(event.metadata.get("scene_number", "0") or 0)
        if scene_number <= 0:
            continue
        current = by_scene.get(scene_number)
        if current is None or event_scene_rank(event) > event_scene_rank(current):
            by_scene[scene_number] = event
    return by_scene


def event_scene_rank(event: SessionEventRecord) -> tuple[float, float, float]:
    score = float(event.metadata.get("score", "0") or 0.0)
    click_bonus = 1.0 if event.type == "click" else 0.0
    label_bonus = 1.0 if event_scene_label(event) else 0.0
    return (click_bonus, score, label_bonus)


def event_scene_label(event: SessionEventRecord | None) -> str:
    if event is None:
        return ""
    return (
        event.target.label.strip()
        or event.metadata.get("result_label", "").strip()
        or event.target.text.strip()
    )


def event_scene_purpose(
    label: str,
    event: SessionEventRecord | None,
) -> str:
    clean_label = label.strip().rstrip(".")
    if event is not None and event.type == "focus":
        return f"Show the viewer the {clean_label} screen."
    return f"Capture the product action on {clean_label}."


def refined_scene_label(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
) -> str:
    if analysis is None:
        return ""
    resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    state_label = best_state_label(analysis)
    action_label = best_action_label(scene, analysis, resolution)
    if auth_action_scene(resolution, analysis, action_label):
        return action_label
    if prefers_state_label(scene, analysis, resolution, state_label, action_label):
        return state_label or action_label
    return action_label or state_label


def best_state_label(analysis: VisualSceneAnalysisRecord) -> str:
    frame = representative_state_frame(analysis)
    if frame is None:
        return ""
    labels = frame_local_labels(frame, analysis.visible_labels) or analysis.visible_labels
    return structure_state_label(frame, labels)


def best_action_label(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord,
    resolution: SceneIntentResolution,
) -> str:
    auth_label = auth_action_label(analysis, resolution)
    if auth_label:
        return auth_label
    clicked = clicked_action_label(analysis)
    if clicked:
        return clicked
    entity = best_matching_entity_label(analysis, resolution)
    exact = exact_visual_target_label(analysis, None, entity)
    return exact or entity


def auth_action_scene(
    resolution: SceneIntentResolution,
    analysis: VisualSceneAnalysisRecord,
    action_label: str,
) -> bool:
    return resolution.intent in {"auth", "account_existing", "account_create"} and analysis.click_detected and bool(action_label)


def auth_action_label(
    analysis: VisualSceneAnalysisRecord,
    resolution: SceneIntentResolution,
) -> str:
    if resolution.intent not in {"auth", "account_existing", "account_create"}:
        return ""
    frame = strongest_action_frame(analysis)
    if frame is None:
        return ""
    labels = [element.label.strip() for element in frame.ui_elements if element.label.strip()]
    if resolution.intent == "account_create":
        return next((label for label in labels if signup_action_label(label)), "")
    if resolution.intent in {"auth", "account_existing"}:
        return next((label for label in labels if login_action_label(label)), "")
    return ""


def prefers_state_label(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord,
    resolution: SceneIntentResolution,
    state_label: str,
    action_label: str,
) -> bool:
    if not state_label:
        return False
    result_hint = any(word in normalize_label(scene.source_excerpt) for word in ("opened", "shown", "can see", "there are"))
    if analysis.click_detected and action_label and resolution.intent in {"auth", "account_existing", "account_create", "course"} and not result_hint:
        return False
    frame = representative_state_frame(analysis)
    if frame is None:
        return False
    local_labels = frame_local_labels(frame, analysis.visible_labels)
    if prefers_state_event(frame, local_labels) and not compact_action_target(frame):
        return True
    return result_hint


def strongest_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    if not analysis.frames:
        return None
    return max(
        analysis.frames,
        key=lambda frame: (frame.click_confidence, frame.importance_score, frame.diff_score),
    )


def representative_state_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    if not analysis.frames:
        return None
    return max(
        analysis.frames,
        key=lambda frame: state_frame_rank(frame, analysis),
    )


def state_frame_rank(
    frame: FrameSignalRecord,
    analysis: VisualSceneAnalysisRecord,
) -> tuple[float, float, float, float]:
    labels = frame_local_labels(frame, analysis.visible_labels) or analysis.visible_labels
    structure = frame_structure(frame, labels)
    structure_weight = 1.0 if structure in {"dashboard", "result"} else 0.4 if structure == "picker" else 0.0
    timestamp_weight = frame.timestamp
    return (
        structure_weight,
        1.0 if structure_state_label(frame, labels) else 0.0,
        frame.importance_score + frame.diff_score - frame.click_confidence * 0.2,
        timestamp_weight,
    )


def strongest_action_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    actionable = [frame for frame in analysis.frames if frame.click_target_box is not None or frame.click_confidence >= 0.45]
    if not actionable:
        return None
    return max(
        actionable,
        key=lambda frame: (frame.click_confidence, 1.0 if frame.click_target_box is not None else 0.0, frame.importance_score, frame.diff_score),
    )


def clicked_action_label(analysis: VisualSceneAnalysisRecord) -> str:
    frame = strongest_action_frame(analysis)
    if frame is None:
        return ""
    clicked = nearest_click_label(frame)
    if not clicked:
        return ""
    if not should_promote_generic_label(clicked):
        return clicked
    companion = companion_entity_label(frame, clicked)
    return title_case_phrase(companion) if companion else ""


def nearest_click_label(frame: FrameSignalRecord) -> str:
    anchor = frame.click_target_box or frame.cursor_box or frame.dominant_box
    if anchor is None:
        return ""
    ranked = sorted(
        (element for element in frame.ui_elements if element.label.strip()),
        key=lambda element: click_label_rank(element, anchor),
    )
    return ranked[0].label.strip() if ranked else ""


def login_action_label(label: str) -> bool:
    normalized = normalize_label(label)
    return "log in" in normalized or "login" in normalized or "sign in" in normalized


def signup_action_label(label: str) -> bool:
    normalized = normalize_label(label)
    return "sign up" in normalized or "create account" in normalized or "continue with google" in normalized


def click_label_rank(
    element: UiElementRecord,
    anchor: FocusBox,
) -> tuple[float, float]:
    return (
        box_center_delta(anchor, element.box),
        -element.confidence,
    )


def companion_entity_label(
    frame: FrameSignalRecord,
    clicked_label: str,
) -> str:
    anchor = frame.dominant_box or frame.click_target_box
    candidates = [
        element
        for element in frame.ui_elements
        if element.label.strip()
        and normalize_label(element.label) != normalize_label(clicked_label)
        and not should_promote_generic_label(element.label)
        and len(normalize_label(element.label).split()) <= 3
    ]
    if not candidates:
        return ""
    ranked = sorted(
        candidates,
        key=lambda element: companion_rank(element, anchor),
    )
    return ranked[0].label.strip() if ranked else ""


def companion_rank(
    element: UiElementRecord,
    anchor: FocusBox | None,
) -> tuple[float, float, float]:
    return (
        box_center_delta(anchor, element.box) if anchor is not None else 1.0,
        0.0 if len(normalize_label(element.label).split()) == 1 else 1.0,
        -element.confidence,
    )


def refined_scene_purpose(
    current_purpose: str,
    label: str,
    analysis: VisualSceneAnalysisRecord | None,
) -> str:
    clean_label = label.strip().rstrip(".")
    normalized = normalize_label(clean_label)
    if analysis is not None and normalized == normalize_label(best_state_label(analysis)):
        return f"Show the viewer the {clean_label} screen."
    return f"Capture the product action on {clean_label}."


def refined_source_excerpt(current_excerpt: str, label: str) -> str:
    if not current_excerpt.strip():
        return label
    current = normalize_label(current_excerpt)
    target = normalize_label(label)
    return label if target and target not in current else current_excerpt
