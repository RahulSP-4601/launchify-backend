from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import LaunchScriptScene, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_type_classifier import SceneType, classify_scene_type


@dataclass(frozen=True)
class SemanticEvent:
    semantic_action: str
    entity: str
    branch: str
    score: float
    scene_type: SceneType


def semantic_event(
    event: SessionEventRecord,
    scene: LaunchScriptScene | None,
    analysis_scene: VisualSceneAnalysisRecord | None,
) -> SemanticEvent:
    scene_type = classify_scene_type(scene, analysis_scene) if scene is not None else "generic"
    label = normalize_label(event.target.label or event.target.text)
    transcript = normalize_label(event.metadata.get("transcript_excerpt", ""))
    label_tokens = set(label.split())
    transcript_tokens = set(transcript.split())
    return SemanticEvent(
        semantic_action=semantic_action(label_tokens, transcript_tokens, scene_type),
        entity=semantic_entity(label_tokens, transcript_tokens),
        branch=semantic_branch(label_tokens, transcript_tokens),
        score=float(event.metadata.get("score", "0") or 0.0),
        scene_type=scene_type,
    )


def semantic_action(label_tokens: set[str], transcript_tokens: set[str], scene_type: SceneType) -> str:
    if {"choose", "account"} <= label_tokens:
        return "auth_choose_account"
    if scene_type == "account_picker" and "account" in label_tokens:
        return "auth_choose_account"
    if {"log", "login"} & label_tokens and "google" in label_tokens:
        return "auth_login_google"
    if {"sign", "signup"} & label_tokens and "google" in label_tokens:
        return "auth_signup_google"
    if {"create", "account"} <= label_tokens:
        return "auth_create_account"
    if "google" in label_tokens:
        return "auth_entry_google"
    combined_tokens = label_tokens | transcript_tokens
    if scene_type == "course_catalog" and "open" in label_tokens and "course" in label_tokens:
        return "course_open"
    if scene_type == "course_catalog" and semantic_entity(label_tokens, transcript_tokens):
        return "course_select"
    if "course" in combined_tokens:
        return "course_select"
    return "generic"


def semantic_entity(label_tokens: set[str], transcript_tokens: set[str]) -> str:
    tokens = label_tokens | transcript_tokens
    entities = ["japanese", "japan", "google", "account"]
    for entity in entities:
        if entity in tokens:
            return entity
    return ""


def semantic_branch(label_tokens: set[str], transcript_tokens: set[str]) -> str:
    if {"sign", "signup", "create"} & label_tokens:
        return "create"
    if {"choose", "existing"} & label_tokens or {"log", "login"} & label_tokens:
        return "existing"
    if label_tokens == {"account"} and {"choose", "existing", "login", "log"} & transcript_tokens:
        return "existing"
    if {"choose", "existing"} & transcript_tokens:
        return "existing"
    if {"log", "login"} & transcript_tokens and "google" in label_tokens:
        return "existing"
    if {"sign", "signup", "create"} & transcript_tokens and "google" in label_tokens:
        return "create"
    return "generic"
