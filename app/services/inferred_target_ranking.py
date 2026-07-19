from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.projects import FocusBox
from app.services.inferred_recording_support import actionable_label, box_area, box_center_delta, intent_overlap_score, intent_tokens, normalize_label, state_like_label
from app.services.scene_intent_resolver import SceneIntentResolution, resolve_scene_intent

SceneIntent = Literal["auth", "account_existing", "account_create", "course", "result", "generic"]
UIRole = Literal["primary_action", "supporting_context", "state_only", "ambiguous"]
ACTION_WORDS = frozenset({"click", "choose", "continue", "enter", "log", "login", "open", "press", "select", "sign", "start", "tap", "type"})
STOP_WORDS = frozenset({"account", "already", "and", "can", "course", "courses", "existing", "first", "for", "from", "have", "into", "just", "need", "now", "once", "right", "that", "the", "then", "there", "this", "under", "with", "you", "your"})
STATE_WORDS = frozenset({"available", "coming", "displayed", "loaded", "logged", "opened", "shown", "soon", "view"})
AUTH_WORDS = frozenset({"account", "continue", "google", "login", "log", "sign"})
COURSE_WORDS = frozenset({"course", "courses", "japan", "japanese", "lesson", "open"})
RESULT_WORDS = frozenset({"after", "see", "shown", "view"})
AFFORDANCE_WORDS = frozenset({"continue", "enter", "login", "log", "open", "select", "sign", "start"})
AMBIGUITY_MARGIN = 0.08


@dataclass(frozen=True)
class RankedTarget:
    label: str
    focus_box: FocusBox | None
    score: float
    clear_winner: bool
    role: UIRole


@dataclass(frozen=True)
class SceneSemantics:
    intent: SceneIntent
    focus_tokens: set[str]
    context_tokens: set[str]
    transcript_tokens: set[str]
    negative_tokens: set[str]
    branch_tokens: set[str]
    alternate_branch_tokens: set[str]


def select_ranked_target(
    candidates: list[tuple[str, FocusBox | None, float]],
    transcript_excerpt: str,
    source_excerpt: str,
    focus_box: FocusBox | None,
) -> RankedTarget | None:
    valid = [(label, box, weight) for label, box, weight in candidates if label.strip()]
    if not valid:
        return None
    semantics = scene_semantics(transcript_excerpt, source_excerpt)
    families = sibling_families(valid)
    scored = sorted(
        (build_ranked_candidate(label, box, weight, valid, families, semantics, focus_box) for label, box, weight in valid),
        key=lambda item: item.score,
        reverse=True,
    )
    best = scored[0]
    second = scored[1].score if len(scored) > 1 else 0.0
    clear_winner = best.score >= 0.4 and (best.score - second >= AMBIGUITY_MARGIN or best.score >= 0.7)
    if best.role == "state_only" and semantics.intent != "result":
        return None
    if best.role == "ambiguous" or (not clear_winner and best.score < 0.7):
        return None
    return RankedTarget(best.label, best.focus_box or focus_box, round(best.score, 3), clear_winner, best.role)


def build_ranked_candidate(
    label: str,
    candidate_box: FocusBox | None,
    source_weight: float,
    candidates: list[tuple[str, FocusBox | None, float]],
    families: dict[str, int],
    semantics: SceneSemantics,
    focus_box: FocusBox | None,
) -> RankedTarget:
    tokens = set(normalize_label(label).split())
    role = role_for_candidate(tokens, semantics)
    score = (
        source_weight * 0.1
        + intent_overlap_score(label, semantics.transcript_tokens) * 0.18
        + focus_match_score(tokens, semantics) * 0.28
        + intent_class_bonus(tokens, semantics.intent) * 0.18
        + auth_branch_bonus(tokens, semantics) * 0.22
        + proximity_score(candidate_box, focus_box) * 0.12
        + compactness_score(candidate_box) * 0.08
        + sibling_preference(tokens, candidates, families, semantics) * 0.12
        - auth_branch_conflict_penalty(tokens, semantics) * 0.24
        - state_penalty(tokens, semantics) * 0.3
        - context_penalty(tokens, semantics) * 0.18
    )
    return RankedTarget(label, candidate_box, score, False, role)


def scene_semantics(transcript_excerpt: str, source_excerpt: str) -> SceneSemantics:
    resolution = resolve_scene_intent(transcript_excerpt, source_excerpt)
    text = normalize_label(f"{transcript_excerpt} {source_excerpt} {resolution.preferred_clause}")
    tokens = set(text.split())
    return SceneSemantics(
        resolution.intent,
        merged_focus_tokens(resolution),
        resolution.context_tokens,
        tokens,
        resolution.negative_tokens,
        resolution.branch_tokens,
        resolution.alternate_branch_tokens,
    )


def candidate_role(label: str, transcript_excerpt: str, source_excerpt: str) -> UIRole:
    semantics = scene_semantics(transcript_excerpt, source_excerpt)
    return role_for_candidate(set(normalize_label(label).split()), semantics)


def merged_focus_tokens(resolution: SceneIntentResolution) -> set[str]:
    return resolution.focus_tokens or resolution.context_tokens


def semantic_clauses(text: str) -> list[str]:
    raw_parts = re.split(r"\bafter\b|\bonce\b|\bthen\b|\bbut\b|[.!?]+", text)
    clauses = [" ".join(part.split()) for part in raw_parts if part.strip()]
    return clauses or [text]


def extracted_focus_tokens(clauses: list[str]) -> set[str]:
    focus: set[str] = set()
    patterns = (
        r"(?:click|select|open|choose|tap|press)\s+(?:on\s+|the\s+|to\s+|under\s+)*([a-z0-9 ]{3,40})",
        r"(?:log in with|sign in with)\s+([a-z0-9 ]{3,30})",
    )
    for clause in clauses:
        if not any(word in clause.split() for word in ACTION_WORDS):
            continue
        for pattern in patterns:
            for match in re.findall(pattern, clause):
                focus.update(clean_semantic_tokens(match))
        if not focus:
            focus.update(clean_semantic_tokens(clause))
    return focus


def extracted_context_tokens(clauses: list[str]) -> set[str]:
    context: set[str] = set()
    patterns = (
        r"(?:rest of the|other|others|remaining)\s+([a-z0-9 ]{3,40})",
        r"(?:can see|you see|there are)\s+([a-z0-9 ]{3,40})",
    )
    for clause in clauses:
        if "coming soon" in clause or "rest of the" in clause or "there are" in clause or "can see" in clause:
            context.update(clean_semantic_tokens(clause))
        for pattern in patterns:
            for match in re.findall(pattern, clause):
                context.update(clean_semantic_tokens(match))
    return context


def clean_semantic_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 3 and token not in STOP_WORDS and token not in ACTION_WORDS}


def role_for_candidate(tokens: set[str], semantics: SceneSemantics) -> UIRole:
    if state_like_tokens(tokens) and not semantics.focus_tokens.intersection(tokens):
        return "state_only"
    if semantics.intent == "course" and state_like_tokens(tokens) and not has_affordance(tokens):
        return "state_only"
    if semantics.negative_tokens.intersection(tokens) and not semantics.focus_tokens.intersection(tokens):
        return "supporting_context"
    if semantics.focus_tokens and semantics.focus_tokens.intersection(tokens):
        return "primary_action"
    if semantics.context_tokens.intersection(tokens):
        return "supporting_context"
    if semantics.intent == "course" and "course" in tokens and not state_like_tokens(tokens):
        return "primary_action"
    if semantics.intent == "course" and has_affordance(tokens) and not state_like_tokens(tokens):
        return "primary_action"
    if semantics.intent == "account_existing" and tokens & {"existing", "account"}:
        return "primary_action"
    if semantics.intent == "account_existing" and tokens & {"login", "log", "google"}:
        return "primary_action"
    if semantics.intent == "account_create" and tokens & {"create", "signup", "sign", "account"}:
        return "primary_action"
    if actionable_candidate(tokens, semantics.intent):
        return "primary_action"
    return "ambiguous"


def focus_match_score(tokens: set[str], semantics: SceneSemantics) -> float:
    if not semantics.focus_tokens:
        return 0.2 if actionable_candidate(tokens, semantics.intent) else 0.0
    overlap = len(tokens & semantics.focus_tokens)
    affordance_bonus = 0.18 if has_affordance(tokens) and semantics.intent == "course" else 0.0
    return min(overlap / max(len(semantics.focus_tokens), 1), 1.0) + affordance_bonus


def intent_class_bonus(tokens: set[str], intent: SceneIntent) -> float:
    if intent == "auth":
        return 1.0 if tokens & {"google"} and tokens & {"login", "log", "sign"} else 0.3 if tokens & AUTH_WORDS else 0.0
    if intent == "account_existing":
        return 1.0 if tokens & {"existing", "login", "log"} else 0.2 if "account" in tokens else 0.0
    if intent == "account_create":
        return 1.0 if tokens & {"create", "signup", "sign"} and "account" in tokens else 0.0
    if intent == "course":
        if tokens & {"japan", "japanese"} and "course" in tokens:
            return 1.0
        if has_affordance(tokens) and "course" in tokens:
            return 0.82
        if has_affordance(tokens):
            return 0.68
        return 0.0
    if intent == "result":
        return 0.7 if tokens & RESULT_WORDS else 0.0
    return 0.2 if actionable_candidate(tokens, intent) else 0.0


def state_penalty(tokens: set[str], semantics: SceneSemantics) -> float:
    penalty = 0.0
    if state_like_tokens(tokens):
        penalty += 0.8
    if semantics.intent == "account_existing" and {"create", "signup"} & tokens:
        penalty += 0.9
    if semantics.intent == "account_create" and {"existing", "login", "log"} & tokens:
        penalty += 0.7
    if semantics.intent == "course" and semantics.focus_tokens and not semantics.focus_tokens.intersection(tokens) and {"coming", "soon"} & tokens:
        penalty += 1.0
    if semantics.negative_tokens.intersection(tokens) and not semantics.focus_tokens.intersection(tokens):
        penalty += 0.8
    return penalty


def context_penalty(tokens: set[str], semantics: SceneSemantics) -> float:
    if semantics.context_tokens and semantics.context_tokens.intersection(tokens) and not semantics.focus_tokens.intersection(tokens):
        return 0.8
    return 0.0


def auth_branch_bonus(tokens: set[str], semantics: SceneSemantics) -> float:
    if semantics.intent not in {"auth", "account_existing", "account_create"}:
        return 0.0
    relevant = tokens & {"existing", "create", "signup", "login", "log", "google", "account"}
    if not relevant:
        return 0.0
    branch_overlap = len(relevant & semantics.branch_tokens)
    alternate_overlap = len(relevant & semantics.alternate_branch_tokens)
    return min(branch_overlap * 0.42, 0.84) - min(alternate_overlap * 0.34, 0.68)


def auth_branch_conflict_penalty(tokens: set[str], semantics: SceneSemantics) -> float:
    if semantics.intent == "account_existing" and {"create", "signup", "sign"} & tokens and not {"login", "log", "existing"} & tokens:
        return 1.0
    if semantics.intent == "account_create" and {"existing", "login", "log"} & tokens and not {"create", "signup", "sign"} & tokens:
        return 0.9
    return 0.0


def sibling_preference(
    tokens: set[str],
    candidates: list[tuple[str, FocusBox | None, float]],
    families: dict[str, int],
    semantics: SceneSemantics,
) -> float:
    family = candidate_family(tokens)
    if families.get(family, 0) < 2:
        return 0.0
    if semantics.focus_tokens and semantics.focus_tokens.intersection(tokens):
        return 0.9
    if semantics.intent == "course" and has_affordance(tokens) and not state_like_tokens(tokens):
        return 0.75
    if state_like_tokens(tokens):
        return -0.6
    return 0.15 if actionable_candidate(tokens, semantics.intent) else -0.1


def sibling_families(candidates: list[tuple[str, FocusBox | None, float]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label, _box, _weight in candidates:
        family = candidate_family(set(normalize_label(label).split()))
        counts[family] = counts.get(family, 0) + 1
    return counts


def candidate_family(tokens: set[str]) -> str:
    if tokens & COURSE_WORDS:
        return "course"
    if tokens & AUTH_WORDS:
        return "auth"
    if tokens & STATE_WORDS:
        return "state"
    return "generic"


def actionable_candidate(tokens: set[str], intent: SceneIntent) -> bool:
    if intent == "course":
        return has_affordance(tokens) or "course" in tokens or bool(tokens & {"japan", "japanese"})
    if intent in {"auth", "account_existing", "account_create"}:
        return bool(tokens & AUTH_WORDS or has_affordance(tokens)) and not {"coming", "soon"} & tokens
    return not state_like_tokens(tokens)


def proximity_score(candidate_box: FocusBox | None, focus_box: FocusBox | None) -> float:
    if candidate_box is None:
        return 0.0
    return max(0.0, 1.0 - box_center_delta(candidate_box, focus_box) * 3.0)


def compactness_score(candidate_box: FocusBox | None) -> float:
    if candidate_box is None:
        return 0.0
    return max(0.0, 0.18 - box_area(candidate_box))


def state_like_tokens(tokens: set[str]) -> bool:
    return state_like_label(" ".join(tokens)) or bool(tokens & STATE_WORDS)


def has_affordance(tokens: set[str]) -> bool:
    return bool(tokens & AFFORDANCE_WORDS)
