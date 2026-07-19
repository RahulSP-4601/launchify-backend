from __future__ import annotations

from typing import Literal

from app.models.projects import LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent

SceneType = Literal["auth_entry", "auth_provider", "account_picker", "course_catalog", "result_state", "generic"]


def classify_scene_type(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
) -> SceneType:
    resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    labels = visible_scene_labels(analysis)
    tokens = set(normalize_label(f"{scene.spoken_line} {scene.source_excerpt} {' '.join(labels)}").split())
    if "choose account" in " ".join(labels) or {"choose", "account"} <= tokens:
        return "account_picker"
    if {"google", "login"} <= tokens or {"google", "log"} <= tokens:
        return "auth_provider"
    if resolution.intent in {"auth", "account_existing", "account_create"}:
        return "auth_provider"
    if resolution.intent == "course":
        return "course_catalog"
    if resolution.intent == "result":
        return "result_state"
    if "login" in tokens and "button" in tokens:
        return "auth_entry"
    return "generic"


def visible_scene_labels(analysis: VisualSceneAnalysisRecord | None) -> list[str]:
    if analysis is None:
        return []
    labels = [label.strip().lower() for label in analysis.visible_labels if label.strip()]
    labels.extend(element.label.strip().lower() for frame in analysis.frames for element in frame.ui_elements if element.label.strip())
    return list(dict.fromkeys(labels))
