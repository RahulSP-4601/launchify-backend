from __future__ import annotations

from typing import Literal

from app.models.projects import LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import resolve_scene_intent
from app.services.ui_structure_insights import frame_structure

SceneType = Literal["auth_entry", "auth_provider", "account_picker", "course_catalog", "result_state", "generic"]


def classify_scene_type(
    scene: LaunchScriptScene,
    analysis: VisualSceneAnalysisRecord | None,
) -> SceneType:
    resolution = resolve_scene_intent(scene.source_excerpt, scene.spoken_line)
    labels = visible_scene_labels(analysis)
    frame_labels = latest_frame_labels(analysis)
    structure = frame_structure(analysis.frames[-1], frame_labels) if analysis is not None and analysis.frames else "generic"
    if structure == "picker":
        return "account_picker"
    if structure == "dashboard":
        return "course_catalog"
    if structure == "result":
        return "result_state"
    combined_text = normalize_label(f"{scene.spoken_line} {scene.source_excerpt} {' '.join(labels)}")
    tokens = set(combined_text.split())
    if has_course_state_signal(labels, tokens):
        return "course_catalog"
    has_auth_buttons = any(
        phrase in " ".join(labels)
        for phrase in ("log in with google", "login with google", "sign up with google", "google login")
    ) or ({"google"} <= tokens and ({"log", "login"} & tokens or {"sign", "signup"} & tokens))
    if ("choose account" in " ".join(labels) or {"choose", "account"} <= tokens) and not has_auth_buttons:
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


def latest_frame_labels(analysis: VisualSceneAnalysisRecord | None) -> list[str]:
    if analysis is None or not analysis.frames:
        return []
    frame = analysis.frames[-1]
    labels = [element.label.strip().lower() for element in frame.ui_elements if element.label.strip()]
    labels.extend(label.strip().lower() for label in frame.ocr_labels if label.strip())
    return list(dict.fromkeys(labels)) or visible_scene_labels(analysis)


def has_course_state_signal(labels: list[str], tokens: set[str]) -> bool:
    label_text = " ".join(labels)
    language_count = sum(1 for label in labels if normalize_label(label) in {"japanese", "english", "german", "spanish", "french"})
    has_course_cards = "course card" in label_text or "open course" in label_text or "coming soon" in label_text
    has_level_picker = "jlpt level" in label_text or "pick your japanese level" in label_text
    has_course_copy = "course" in tokens and (language_count >= 2 or "japanese" in tokens)
    return has_level_picker or has_course_cards or has_course_copy
