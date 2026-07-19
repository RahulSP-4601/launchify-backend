from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IntentKind = Literal["auth", "account_existing", "account_create", "course", "result", "generic"]

ACTION_WORDS = frozenset({"click", "choose", "continue", "enter", "log", "login", "open", "press", "select", "sign", "start", "tap", "type"})
STOP_WORDS = frozenset({"account", "already", "and", "can", "for", "from", "have", "into", "just", "need", "now", "once", "right", "that", "the", "then", "there", "this", "under", "with", "you", "your"})
NEGATIVE_WORDS = frozenset({"coming", "other", "others", "remaining", "rest", "soon"})


@dataclass(frozen=True)
class SceneIntentResolution:
    intent: IntentKind
    focus_tokens: set[str]
    focus_phrase: str
    context_tokens: set[str]
    negative_tokens: set[str]
    preferred_clause: str
    branch_tokens: set[str]
    alternate_branch_tokens: set[str]


def resolve_scene_intent(
    transcript_excerpt: str,
    source_excerpt: str,
    frame_progress: float = 0.5,
) -> SceneIntentResolution:
    combined = " ".join(part.strip() for part in (transcript_excerpt, source_excerpt) if part.strip())
    clauses = split_clauses(combined)
    preferred = choose_clause(clauses, frame_progress)
    preferred_tokens = content_tokens(preferred)
    branch_tokens, alternate_branch_tokens = auth_branch_tokens(clauses, preferred)
    return SceneIntentResolution(
        intent=infer_intent(preferred_tokens),
        focus_tokens=focus_tokens(preferred),
        focus_phrase=matched_focus_phrase(preferred),
        context_tokens=context_tokens(clauses),
        negative_tokens=negative_tokens(clauses),
        preferred_clause=preferred,
        branch_tokens=branch_tokens,
        alternate_branch_tokens=alternate_branch_tokens,
    )


def split_clauses(text: str) -> list[str]:
    normalized = auth_option_splits(text.lower())
    parts = re.split(r"\bafter\b|\bonce\b|\bthen\b|\bbut\b|[.!?,;]+", normalized)
    clauses = [" ".join(part.split()) for part in parts if part.strip()]
    return clauses or [text.lower()]


def auth_option_splits(text: str) -> str:
    patterns = (
        r"\bor you can\b",
        r"\bor just\b",
        r"\bor\b(?=\s+(?:log in|login|sign in|use|choose))",
    )
    normalized = text
    for pattern in patterns:
        normalized = re.sub(pattern, ". ", normalized)
    return normalized


def choose_clause(clauses: list[str], frame_progress: float) -> str:
    ranked = sorted(
        ((clause_rank(clause, index, len(clauses), frame_progress), clause) for index, clause in enumerate(clauses)),
        key=lambda item: item[0],
        reverse=True,
    )
    return ranked[0][1] if ranked else ""


def clause_rank(clause: str, index: int, total: int, frame_progress: float) -> tuple[float, float, float]:
    tokens = content_tokens(clause)
    progress_match = 1.0 - abs((index + 0.5) / max(total, 1) - min(max(frame_progress, 0.0), 1.0))
    action_score = 1.0 if set(clause.split()) & ACTION_WORDS else 0.0
    specificity = len(tokens & {"google", "existing", "create", "japan", "japanese", "course", "login", "account"})
    return action_score, specificity + progress_match, -index / max(total, 1)


def infer_intent(tokens: set[str]) -> IntentKind:
    if {"existing", "account"} <= tokens or ({"login", "log"} & tokens and "existing" in tokens):
        return "account_existing"
    if "create" in tokens and "account" in tokens:
        return "account_create"
    if tokens & {"japan", "japanese", "course", "courses"}:
        return "course"
    if tokens & {"see", "shown", "opened", "view"}:
        return "result"
    if tokens & {"google", "login", "log", "sign"}:
        return "auth"
    return "generic"


def focus_tokens(clause: str) -> set[str]:
    matched = matched_phrase_tokens(clause)
    return matched or content_tokens(clause)


def matched_focus_phrase(clause: str) -> str:
    patterns = (
        r"(?:click|select|open|choose|tap|press)\s+(?:on\s+|the\s+|to\s+|under\s+)*([a-z0-9 ]{3,40})",
        r"(?:log in with|sign in with)\s+([a-z0-9 ]{3,30})",
        r"(?:log in to|login to)\s+(?:the\s+|your\s+)*([a-z0-9 ]{3,30})",
    )
    for pattern in patterns:
        match = re.search(pattern, clause)
        if match:
            return " ".join(match.group(1).split())
    return ""


def matched_phrase_tokens(clause: str) -> set[str]:
    patterns = (
        r"(?:click|select|open|choose|tap|press)\s+(?:on\s+|the\s+|to\s+|under\s+)*([a-z0-9 ]{3,40})",
        r"(?:log in with|sign in with)\s+([a-z0-9 ]{3,30})",
        r"(?:log in to|login to)\s+(?:the\s+|your\s+)*([a-z0-9 ]{3,30})",
    )
    tokens: set[str] = set()
    for pattern in patterns:
        for match in re.findall(pattern, clause):
            tokens.update(content_tokens(match))
    return tokens


def auth_branch_tokens(
    clauses: list[str],
    preferred_clause: str,
) -> tuple[set[str], set[str]]:
    preferred_tokens = content_tokens(preferred_clause)
    if not auth_clause(preferred_tokens):
        return set(), set()
    branch_tokens = preferred_tokens & {"existing", "create", "signup", "login", "log", "google", "account"}
    alternate_tokens: set[str] = set()
    for clause in clauses:
        if clause == preferred_clause:
            continue
        clause_tokens = content_tokens(clause)
        if auth_clause(clause_tokens):
            alternate_tokens.update(clause_tokens & {"existing", "create", "signup", "login", "log", "google", "account"})
    return branch_tokens, alternate_tokens


def auth_clause(tokens: set[str]) -> bool:
    return bool(tokens & {"existing", "create", "signup", "login", "log", "google", "account"})


def context_tokens(clauses: list[str]) -> set[str]:
    context: set[str] = set()
    for clause in clauses:
        if "can see" in clause or "there are" in clause or "opened" in clause:
            context.update(content_tokens(clause))
    return context


def negative_tokens(clauses: list[str]) -> set[str]:
    negatives: set[str] = set()
    for clause in clauses:
        clause_tokens = content_tokens(clause)
        if clause_tokens & NEGATIVE_WORDS:
            negatives.update(clause_tokens)
    return negatives


def content_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) >= 3 and token not in STOP_WORDS and token not in ACTION_WORDS
    }
