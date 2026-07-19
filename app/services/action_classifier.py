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
    text = " ".join(part for part in (label, transcript_excerpt, summary) if part).lower()
    tokens = set(normalize_label(text).split())
    if event_type == "input":
        return "input_entry"
    if event_type == "navigation":
        return "navigation"
    if any(token in tokens for token in AUTH_WORDS):
        return "auth_action"
    if "tab" in tokens or ("dashboard" in tokens and ("open" in tokens or "select" in tokens)):
        return "tab_switch"
    if any(token in tokens for token in MENU_WORDS):
        return "menu_open"
    if any(token in tokens for token in CARD_WORDS):
        return "card_selection"
    if any(token in tokens for token in BUTTON_WORDS):
        return "button_click"
    if is_result_state(tokens):
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


def is_result_state(tokens: set[str]) -> bool:
    result_hits = sum(1 for token in RESULT_WORDS if token in tokens)
    return result_hits >= 1 and ("see" in tokens or "view" in tokens or "loaded" in tokens or "shown" in tokens)
