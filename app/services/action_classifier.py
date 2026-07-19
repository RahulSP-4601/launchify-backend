from __future__ import annotations

import re

from app.models.projects import SessionEventRecord, SessionEventType

ACTION_CLASSES = (
    "auth_action",
    "menu_open",
    "tab_switch",
    "card_selection",
    "button_click",
    "result_state",
    "explanatory_hold",
    "input_entry",
    "navigation",
    "generic_action",
)

AUTH_WORDS = frozenset({"google", "login", "log", "sign", "account", "auth"})
MENU_WORDS = frozenset({"menu", "dropdown", "sidebar", "panel", "settings", "profile"})
TAB_WORDS = frozenset({"tab", "overview", "dashboard", "home", "course", "courses"})
CARD_WORDS = frozenset({"card", "course", "workspace", "template", "plan"})
BUTTON_WORDS = frozenset({"button", "continue", "confirm", "submit", "start", "open", "create"})
RESULT_WORDS = frozenset({"available", "displayed", "appears", "shown", "loaded"})
EXPLANATION_WORDS = frozenset({"because", "notice", "here", "right", "this", "where", "then"})


def classify_action(
    event_type: SessionEventType,
    label: str,
    transcript_excerpt: str = "",
    summary: str = "",
) -> str:
    primary_text = " ".join(part for part in (label, summary) if part).lower()
    primary_tokens = set(normalize_label(primary_text).split())
    transcript_tokens = set(normalize_label(transcript_excerpt).split())
    tokens = primary_tokens | transcript_tokens
    if event_type == "input":
        return "input_entry"
    if event_type == "navigation":
        return "navigation"
    if any(token in primary_tokens for token in BUTTON_WORDS):
        return "button_click"
    if any(token in primary_tokens for token in CARD_WORDS):
        return "card_selection"
    if "tab" in primary_tokens or ("dashboard" in primary_tokens and ("open" in primary_tokens or "select" in primary_tokens)):
        return "tab_switch"
    if any(token in primary_tokens for token in MENU_WORDS):
        return "menu_open"
    if any(token in primary_tokens for token in AUTH_WORDS):
        return "auth_action"
    if any(token in transcript_tokens for token in AUTH_WORDS) and not primary_tokens.intersection(BUTTON_WORDS | CARD_WORDS | TAB_WORDS | MENU_WORDS):
        return "auth_action"
    if is_result_state(primary_tokens, transcript_tokens):
        return "result_state"
    if any(token in tokens for token in EXPLANATION_WORDS):
        return "explanatory_hold"
    return "generic_action"


def event_action_class(event: SessionEventRecord | None) -> str:
    if event is None:
        return "generic_action"
    metadata_class = event.metadata.get("action_class", "").strip()
    if metadata_class:
        return metadata_class
    return classify_action(
        event.type,
        event.target.label or event.target.text or event.target.selector,
        event.metadata.get("transcript_excerpt", ""),
        event.target.text,
    )


def normalize_label(label: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", label.lower()))


def is_result_state(primary_tokens: set[str], transcript_tokens: set[str]) -> bool:
    result_hits = sum(1 for token in RESULT_WORDS if token in primary_tokens or token in transcript_tokens)
    explanation_hits = {"see", "view", "loaded", "shown"} & transcript_tokens
    if primary_tokens.intersection(BUTTON_WORDS | CARD_WORDS | MENU_WORDS):
        return False
    return result_hits >= 1 and bool(explanation_hits)
