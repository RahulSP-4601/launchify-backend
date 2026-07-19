from __future__ import annotations

from app.models.projects import FrameSignalRecord, LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import InteractionWindow, build_session_event, normalize_label

MIN_RESULT_STATE_SCORE = 0.52


def supplement_result_state_events(
    selected: list[SessionEventRecord],
    scenes: list[LaunchScriptScene],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
    viewport_width: int,
    viewport_height: int,
) -> list[SessionEventRecord]:
    covered_scenes = {int(event.metadata.get("scene_number", "0") or 0) for event in selected}
    supplemented = selected[:]
    for scene in scenes:
        if scene.scene_number in covered_scenes:
            continue
        analysis = analyses_by_scene.get(scene.scene_number)
        event = result_state_event(scene, analysis, viewport_width, viewport_height)
        if event is None:
            continue
        supplemented.append(event)
    return sorted(supplemented, key=lambda item: item.timestamp)


def result_state_event(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
    viewport_width: int,
    viewport_height: int,
) -> SessionEventRecord | None:
    if analysis is None or analysis.click_detected or not analysis.frames:
        return None
    frame = best_result_state_frame(analysis)
    label = result_state_label(frame)
    score = result_state_score(analysis, frame, label)
    if not label or score < MIN_RESULT_STATE_SCORE:
        return None
    focus_box = frame.dominant_box or analysis.primary_focus_box or analysis.anchor_box
    window = InteractionWindow(
        timestamp=round(frame.timestamp, 2),
        score=score,
        event_type="focus",
        label=label,
        text=label,
        focus_box=focus_box,
        transcript_excerpt=scene.source_excerpt,
    )
    event = build_session_event(window, scene.scene_number, viewport_width, viewport_height)
    event.metadata["action_class"] = "result_state"
    event.metadata["scene_state"] = "result_state"
    event.metadata["result_label"] = label
    return event


def best_result_state_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord:
    return max(analysis.frames, key=result_frame_rank)


def result_frame_rank(frame: FrameSignalRecord) -> tuple[float, float, float]:
    return (
        frame.diff_score,
        frame.importance_score,
        -frame.click_confidence,
    )


def result_state_label(frame: FrameSignalRecord) -> str:
    prominent = prominent_instruction_label(frame)
    if prominent:
        return prominent
    ranked = sorted(
        (element for element in frame.ui_elements if element.label.strip()),
        key=lambda element: result_label_rank(element.label, element.role, element.confidence),
        reverse=True,
    )
    return ranked[0].label.strip() if ranked else ""


def result_label_rank(
    label: str,
    role: str,
    confidence: float,
) -> tuple[float, float, float]:
    normalized = normalize_label(label)
    role_bonus = 1.0 if "header" in role or "heading" in role or "instruction" in role else 0.6 if "text" in role else 0.3
    compactness = 1.0 if len(normalized.split()) <= 4 else 0.7
    return (role_bonus, confidence, compactness)


def prominent_instruction_label(frame: FrameSignalRecord) -> str:
    for element in frame.ui_elements:
        label = element.label.strip()
        role = element.role.lower()
        if not label:
            continue
        if ("header" in role or "heading" in role or "text" in role) and instruction_like_label(label):
            return label
    return ""


def instruction_like_label(label: str) -> bool:
    normalized = normalize_label(label)
    return any(phrase in normalized for phrase in ("pick your", "choose your", "select a", "select your", "before you start"))


def result_state_score(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    label: str,
) -> float:
    label_bonus = 0.12 if label else 0.0
    stability_bonus = stable_state_bonus(analysis, frame, label)
    score = frame.diff_score * 0.34 + frame.importance_score * 0.28 + analysis.confidence * 0.16 + label_bonus + stability_bonus
    return round(min(score, 1.0), 3)


def stable_state_bonus(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    label: str,
) -> float:
    bonus = 0.0
    if instruction_like_label(label):
        bonus += 0.18
    if len(frame.ui_elements) >= 4:
        bonus += 0.08
    if len(analysis.visible_labels) >= 4:
        bonus += 0.06
    if frame.importance_score >= 0.75 and analysis.motion_score <= 0.12:
        bonus += 0.08
    return round(bonus, 3)
