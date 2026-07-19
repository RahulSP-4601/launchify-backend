from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.inference_step_builder import InferenceStep

FILLER_WORDS = frozenset({"actually", "basically", "just", "kind", "like", "really", "simply", "sort", "that", "then", "you"})
ACTION_HINTS = frozenset({
    "click", "continue", "course", "create", "dashboard", "enter", "explore", "launch", "learn",
    "log", "login", "open", "password", "profile", "search", "select", "sign", "start", "submit", "type",
})
MAX_REBALANCED_STEP_SECONDS = 5.6


def semantic_merge_steps(steps: list[InferenceStep]) -> list[InferenceStep]:
    merged: list[InferenceStep] = []
    for step in steps:
        if merged and should_semantically_merge(merged[-1], step):
            merged[-1] = merge_group([merged[-1], step])
            continue
        merged.append(step)
    return merged


def should_semantically_merge(left: InferenceStep, right: InferenceStep) -> bool:
    if merged_duration(left, right) > MAX_REBALANCED_STEP_SECONDS:
        return False
    left_tokens = semantic_tokens(left.text)
    right_tokens = semantic_tokens(right.text)
    if not right_tokens:
        return True
    if left_tokens.intersection(right_tokens):
        return True
    return action_score(right.text) == 0 and len(right_tokens) <= 3


def semantic_tokens(text: str) -> set[str]:
    return {token for token in tokenized(text) if token not in FILLER_WORDS and len(token) >= 4}


def merge_group(group: list[InferenceStep]) -> InferenceStep:
    from app.services.inference_step_builder import InferenceStep
    return InferenceStep(start=group[0].start, end=group[-1].end, text=" ".join(step.text for step in group).strip())


def merged_duration(left: InferenceStep, right: InferenceStep) -> float:
    return max(right.end - left.start, 0.0)


def action_score(text: str) -> int:
    return sum(1 for token in tokenized(text) if token in ACTION_HINTS)


def tokenized(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())
