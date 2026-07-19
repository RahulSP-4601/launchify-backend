from __future__ import annotations

import re

from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import SceneIntentResolution

GENERIC_LABELS = frozenset({
    "continue",
    "continue with",
    "open",
    "open course",
    "select",
    "select item",
    "start",
    "view",
})
ACTION_TOKENS = frozenset({"click", "choose", "continue", "open", "press", "select", "start", "tap", "view"})
NOUN_STOP_WORDS = frozenset({"button", "card", "item", "option", "screen", "page"})


def should_promote_generic_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not normalized:
        return False
    if normalized in GENERIC_LABELS:
        return True
    tokens = normalized.split()
    return len(tokens) <= 2 and any(token in ACTION_TOKENS for token in tokens)


def promoted_target_label(current_label: str, resolution: SceneIntentResolution) -> str:
    target_phrase = cleaned_focus_phrase(resolution)
    if not target_phrase:
        return ""
    current_tokens = normalize_label(current_label).split()
    verb = next((token for token in current_tokens if token in ACTION_TOKENS), "")
    if verb in {"click", "choose", "press", "tap"}:
        return title_case_phrase(target_phrase)
    if verb:
        return title_case_phrase(f"{verb} {target_phrase}")
    return title_case_phrase(target_phrase)


def cleaned_focus_phrase(resolution: SceneIntentResolution) -> str:
    phrase = resolution.focus_phrase or ordered_focus_phrase(resolution)
    normalized_tokens = [
        token for token in normalize_label(phrase).split()
        if token not in ACTION_TOKENS and token not in NOUN_STOP_WORDS
    ]
    return " ".join(normalized_tokens[:5]).strip()


def ordered_focus_phrase(resolution: SceneIntentResolution) -> str:
    if not resolution.focus_tokens:
        return ""
    tokens_in_order = [
        token
        for token in re.findall(r"[a-z0-9]+", resolution.preferred_clause.lower())
        if token in resolution.focus_tokens and token not in NOUN_STOP_WORDS
    ]
    deduped = list(dict.fromkeys(tokens_in_order))
    return " ".join(deduped)


def title_case_phrase(value: str) -> str:
    return " ".join(part.capitalize() for part in value.split() if part)
