from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Protocol, Sequence, cast

from app.services.editorial_labels import title_case
from app.services.inferred_recording_support import normalize_label

GENERIC_SELECTION_LABELS = frozenset({
    "select a course",
    "choose a course",
    "select course",
    "choose course",
    "select an option",
    "choose an option",
    "select a plan",
    "select a template",
    "select a workspace",
    "select a project",
})
FOLLOWUP_NOUNS = ("course", "level", "lesson", "difficulty", "path", "track", "journey", "module")
FOLLOWUP_SETUP_STATES = {"difficulty_picker", "result_state"}
MAX_FOLLOWUP_FACT_GAP_SECONDS = 14.0
MAX_FOLLOWUP_LOOKAHEAD = 2
LEADING_ACTION_WORDS = {"click", "choose", "continue", "open", "pick", "press", "review", "select", "set", "start", "tap", "use"}
GENERIC_TARGET_WORDS = frozenset({
    "beginner",
    "default",
    "difficulty",
    "first",
    "general",
    "initial",
    "intro",
    "introductory",
    "level",
    "path",
    "recommended",
    "settings",
    "standard",
    "starting",
})


class FactLike(Protocol):
    @property
    def timestamp(self) -> float: ...

    @property
    def canonical_label(self) -> str: ...

    @property
    def raw_target_label(self) -> str: ...

    @property
    def action_class(self) -> str: ...

    @property
    def screen_after(self) -> str: ...

def resolved_selection_target_label(
    *,
    raw_target_label: str,
    canonical_label: str,
    action_class: str,
    screen_after: str,
    result_label: str,
) -> str:
    if not generic_selection_fact(canonical_label, action_class):
        return raw_target_label
    followup = extracted_target_phrase(result_label)
    if not followup:
        return raw_target_label
    if not promotes_followup_target(raw_target_label, followup, screen_after):
        return raw_target_label
    return followup


def resolved_selection_targets_with_followup(facts: Sequence[FactLike]) -> list[FactLike]:
    resolved: list[FactLike] = []
    for index, fact in enumerate(facts):
        target = followup_selection_target(facts, index)
        resolved.append(updated_fact_target(fact, target) if target != fact.raw_target_label else fact)
    return resolved


def followup_selection_target(facts: Sequence[FactLike], index: int) -> str:
    fact = facts[index]
    if not generic_selection_fact(fact.canonical_label, fact.action_class):
        return fact.raw_target_label
    followup = best_followup_target(fact, facts[index + 1 : index + 1 + MAX_FOLLOWUP_LOOKAHEAD])
    if not followup:
        return fact.raw_target_label
    if not promotes_followup_target(fact.raw_target_label, followup.label, followup.screen_after, followup.score):
        return fact.raw_target_label
    return followup.label


def best_followup_target(fact: FactLike, candidates: Sequence[FactLike]) -> ResolvedFollowupTarget | None:
    ranked = ranked_followup_targets(fact, candidates)
    return ranked[0] if ranked else None


def ranked_followup_targets(fact: FactLike, candidates: Sequence[FactLike]) -> list[ResolvedFollowupTarget]:
    ranked: list[ResolvedFollowupTarget] = []
    for offset, candidate in enumerate(candidates, start=1):
        gap = candidate.timestamp - fact.timestamp
        if gap <= 0 or gap > MAX_FOLLOWUP_FACT_GAP_SECONDS:
            continue
        if candidate.screen_after not in FOLLOWUP_SETUP_STATES:
            continue
        time_score = max(0.0, 1.0 - (gap / MAX_FOLLOWUP_FACT_GAP_SECONDS))
        for value, base_score in (
            (extracted_target_phrase(candidate.raw_target_label), 1.0),
            (extracted_target_phrase(candidate.canonical_label), 0.95),
            (specific_followup_target(candidate.raw_target_label), 0.9),
            (specific_followup_target(candidate.canonical_label), 0.82),
        ):
            if not value:
                continue
            ranked.append(
                ResolvedFollowupTarget(
                    label=value,
                    screen_after=candidate.screen_after,
                    score=round(base_score + time_score + max(0.0, 0.12 - ((offset - 1) * 0.06)), 4),
                )
            )
    ranked.sort(key=lambda item: item.score, reverse=True)
    deduped: list[ResolvedFollowupTarget] = []
    seen: set[str] = set()
    for resolved_target in ranked:
        key = normalize_label(resolved_target.label)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved_target)
    return deduped


class ResolvedFollowupTarget:
    def __init__(self, *, label: str, score: float, screen_after: str) -> None:
        self.label = label
        self.score = score
        self.screen_after = screen_after


def updated_fact_target(fact: FactLike, target: str) -> FactLike:
    return cast(FactLike, replace(cast(Any, fact), raw_target_label=target))


def specific_followup_target(label: str) -> str:
    normalized = normalize_label(label)
    if not normalized or normalized in GENERIC_SELECTION_LABELS:
        return ""
    cleaned = cleaned_target_phrase(label)
    if not cleaned or generic_target_phrase(cleaned):
        return ""
    return title_case(cleaned)


def generic_selection_fact(canonical_label: str, action_class: str) -> bool:
    return action_class == "card_selection" and normalize_label(canonical_label) in GENERIC_SELECTION_LABELS


def extracted_target_phrase(label: str) -> str:
    lowered = normalize_label(label)
    if not lowered:
        return ""
    patterns = [
        rf"(?:pick|choose|set|select)\s+your\s+([a-z0-9][a-z0-9 &/-]{{2,30}}?)\s+(?:{'|'.join(FOLLOWUP_NOUNS)})\b",
        rf"(?:open|choose|select)\s+(?:the\s+)?([a-z0-9][a-z0-9 &/-]{{2,30}}?)\s+(?:{'|'.join(FOLLOWUP_NOUNS)})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            candidate = cleaned_target_phrase(match.group(1))
            if candidate:
                return title_case(candidate)
    return ""


def cleaned_target_phrase(value: str) -> str:
    words = [word for word in re.split(r"\s+", value.strip()) if word]
    while words and words[0] in {"the", "a", "an", "your", "selected", "existing", "available"}:
        words.pop(0)
    while words and words[0].lower() in LEADING_ACTION_WORDS:
        words.pop(0)
    while len(words) > 1 and words[0].lower() == "with":
        words.pop(0)
    while words and words[-1] in {"the", "a", "an", "your", "selected", "existing", "available"}:
        words.pop()
    while words and words[-1].lower() in FOLLOWUP_NOUNS:
        words.pop()
    if not words:
        return ""
    compact = " ".join(words[:3]).strip()
    if len(compact) < 3 or compact.lower() in LEADING_ACTION_WORDS or generic_target_phrase(compact):
        return ""
    return compact


def promotes_followup_target(current_label: str, followup: str, screen_after: str, followup_score: float = 0.0) -> bool:
    if screen_after not in FOLLOWUP_SETUP_STATES:
        return False
    current = normalize_label(current_label)
    followup_key = normalize_label(followup)
    if not current or current in GENERIC_SELECTION_LABELS:
        return True
    if current == followup_key:
        return False
    if specific_selection_overlap(current, followup_key) > 0.0:
        return True
    return followup_score >= 1.15 and len(meaningful_tokens(followup_key)) <= 3


def specific_selection_overlap(current: str, followup: str) -> float:
    current_tokens = meaningful_tokens(current)
    followup_tokens = meaningful_tokens(followup)
    if not current_tokens or not followup_tokens:
        return 0.0
    exact = len(current_tokens & followup_tokens)
    if exact:
        return float(exact)
    current_roots = {token_root(token) for token in current_tokens}
    followup_roots = {token_root(token) for token in followup_tokens}
    return float(len({root for root in followup_roots if root and root in current_roots}))


def meaningful_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in {"course", "level", "lesson", "path", "track", "module"}
    }


def generic_target_phrase(value: str) -> bool:
    tokens = meaningful_tokens(value)
    if not tokens:
        return True
    return tokens <= GENERIC_TARGET_WORDS


def token_root(token: str) -> str:
    if len(token) <= 4:
        return token
    for suffix in ("ese", "ish", "ian", "ing", "ers", "ies", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 4:
            return token[: -len(suffix)]
    return token[:5]
