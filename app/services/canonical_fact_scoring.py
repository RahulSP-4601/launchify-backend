from __future__ import annotations

from app.services.canonical_consistency import auth_mapping_conflict, result_state_conflict
from app.services.inferred_recording_support import normalize_label


def auth_result_label_conflict(raw_key: str, result_label: str) -> bool:
    return auth_mapping_conflict(raw_key, "", result_label)


def fact_penalty(raw_target_label: str, canonical_label: str, screen_after: str) -> float:
    penalty = 0.0
    if auth_mapping_conflict(raw_target_label, canonical_label, canonical_label):
        penalty += 1.35
    if result_state_conflict(raw_target_label, canonical_label, screen_after):
        penalty += 0.9
    return penalty


def label_priority(label: str) -> float:
    normalized = normalize_label(label)
    if normalized in {"google login", "continue with google", "select a course"}:
        return 1.0
    if normalized.startswith("pick your"):
        return 0.92
    return 0.6
