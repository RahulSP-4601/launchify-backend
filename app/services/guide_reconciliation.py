from __future__ import annotations

from typing import cast

from app.models.projects import GuideStepRecord, SessionEventType
from app.services.action_classifier import classify_action
from app.services.editorial_labels import canonical_highlight_label, canonical_on_screen_text, specific_target_label, title_case, selection_target_from_excerpt

GENERIC_EDITORIAL_TOKENS = {
    "a",
    "an",
    "the",
    "your",
    "my",
    "our",
    "continue",
    "click",
    "choose",
    "pick",
    "select",
    "open",
    "set",
    "use",
    "login",
    "log",
    "sign",
    "with",
    "in",
    "level",
    "course",
    "plan",
    "workspace",
    "project",
    "option",
    "account",
}


def finalized_guide_steps(steps: list[GuideStepRecord]) -> list[GuideStepRecord]:
    sanitized = [sanitized_step(step) for step in steps]
    merged = merge_redundant_steps(sanitized)
    return [step.model_copy(update={"step_index": index}) for index, step in enumerate(merged, start=1)]


def sanitized_step(step: GuideStepRecord) -> GuideStepRecord:
    inferred_specific = step.specific_target_label.strip() or specific_target_label(
        label=step.on_screen_text or step.focus_label or step.title,
        action_class=step.action_class,
        transcript_excerpt=step.source_excerpt,
    )
    specific_target = "" if invalid_specific_target(inferred_specific, step.action_class) else inferred_specific
    on_screen_text = resolved_on_screen_text(step, specific_target)
    highlight = resolved_highlight_label(step, specific_target, on_screen_text)
    instruction = sane_instruction(step, specific_target)
    narration = sane_narration(step, specific_target)
    return step.model_copy(
        update={
            "specific_target_label": specific_target,
            "on_screen_text": on_screen_text,
            "highlight_label": highlight,
            "narration": narration,
            "instruction": instruction,
        }
    )


def sane_instruction(step: GuideStepRecord, specific_target: str) -> str:
    if step.action_class == "card_selection":
        target = specific_target or default_course_target(step)
        if target:
            return f"Choose {article_phrase(target, noun='course' if len(target.split()) == 1 else '')} to continue into setup."
    return sane_copy(step.instruction, step)


def sane_narration(step: GuideStepRecord, specific_target: str) -> str:
    if step.action_class == "card_selection":
        target = specific_target or default_course_target(step)
        if target:
            return f"Open {article_phrase(target, noun='course' if len(target.split()) == 1 else '')} to continue into setup."
    return sane_copy(step.narration, step)


def merge_redundant_steps(steps: list[GuideStepRecord]) -> list[GuideStepRecord]:
    merged: list[GuideStepRecord] = []
    for step in steps:
        if merged and should_merge_steps(merged[-1], step):
            merged[-1] = merged_step(merged[-1], step)
            continue
        merged.append(step)
    return merged


def should_merge_steps(left: GuideStepRecord, right: GuideStepRecord) -> bool:
    if right.start - left.end > 1.2:
        return False
    if semantic_target(left) != semantic_target(right):
        return False
    if left.action_class == right.action_class and left.title == right.title:
        return True
    setup_classes = {"button_click", "focus", "result_state"}
    return left.action_class in setup_classes and right.action_class in setup_classes


def merged_step(left: GuideStepRecord, right: GuideStepRecord) -> GuideStepRecord:
    winner = left if step_priority(left) >= step_priority(right) else right
    return winner.model_copy(
        update={
            "start": min(left.start, right.start),
            "end": max(left.end, right.end),
            "source_excerpt": " ".join(part for part in dict.fromkeys([left.source_excerpt, right.source_excerpt]) if part).strip(),
        }
    )


def step_priority(step: GuideStepRecord) -> tuple[int, int, float]:
    action_weight = {
        "auth_action": 5,
        "card_selection": 5,
        "button_click": 4,
        "focus": 3,
        "result_state": 2,
    }.get(step.action_class, 1)
    specificity = 1 if step.specific_target_label.strip() else 0
    return action_weight, specificity, max(step.end - step.start, 0.0)


def semantic_target(step: GuideStepRecord) -> str:
    return normalize_key(step.specific_target_label or step.on_screen_text or step.focus_label or step.title)


def resolved_on_screen_text(step: GuideStepRecord, specific_target: str) -> str:
    if specific_target:
        return specific_target
    preferred = canonical_on_screen_text(
        label=step.focus_label or step.on_screen_text or step.title,
        title=step.title,
    )
    candidate = step.on_screen_text.strip()
    if should_replace_editorial_label(candidate, preferred, step.action_class, step):
        return preferred
    return candidate or preferred


def resolved_highlight_label(step: GuideStepRecord, specific_target: str, on_screen_text: str) -> str:
    if specific_target:
        return specific_target
    preferred = canonical_highlight_label(label=on_screen_text or step.focus_label or step.title, title=step.title)[:48]
    candidate = step.highlight_label.strip()
    if should_replace_editorial_label(candidate, preferred, step.action_class, step):
        return preferred
    return candidate or preferred


def sane_copy(text: str, step: GuideStepRecord) -> str:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return fallback_copy(step)
    for broken in (
        "click click",
        "to start to get started",
        "move attention to pick",
        "open move attention",
    ):
        if broken in cleaned.lower():
            return fallback_copy(step)
    if step.action_class == "card_selection" and any(token in cleaned.lower() for token in ("sign in with google", "continue with google", "google login")):
        return fallback_copy(step)
    if step.action_class == "auth_action" and any(token in cleaned.lower() for token in ("selected course", "japanese course", "course selection")):
        return fallback_copy(step)
    return cleaned


def should_replace_editorial_label(
    candidate: str,
    preferred: str,
    action_class: str,
    step: GuideStepRecord,
) -> bool:
    if is_weak_editorial_copy(candidate, step) or mismatched_step_family(candidate, action_class):
        return True
    if loses_specific_qualifier(candidate, preferred):
        return True
    return label_variant_conflict(candidate, preferred, action_class)


def label_variant_conflict(candidate: str, preferred: str, action_class: str) -> bool:
    candidate_key = normalize_key(candidate)
    preferred_key = normalize_key(preferred)
    if not candidate_key or not preferred_key or candidate_key == preferred_key:
        return False
    if action_class == "auth_action":
        candidate_variant = auth_label_variant(candidate_key)
        preferred_variant = auth_label_variant(preferred_key)
        return bool(candidate_variant and preferred_variant and candidate_variant != preferred_variant)
    return False


def auth_label_variant(label: str) -> str:
    if any(
        phrase in label
        for phrase in (
            "continue with google",
            "sign up with google",
            "sign in with google",
            "log in with google",
            "login with google",
        )
    ):
        return "continue_google"
    if "google login" in label or ("google" in label and "login" in label and "continue" not in label):
        return "google_login"
    if "choose an account" in label or "account picker" in label:
        return "account_picker"
    if "account" in label and "google" in label:
        return "account_google"
    if any(token in label for token in ("google", "login", "log in", "sign in", "account")):
        return "generic_auth"
    return ""


def fallback_copy(step: GuideStepRecord) -> str:
    action_class = step.action_class or classify_action(
        cast(SessionEventType, step.event_type),
        step.focus_label,
        step.narration,
        step.source_excerpt,
    )
    label = step.specific_target_label or step.on_screen_text or step.focus_label or step.title
    if action_class == "auth_action":
        if "continue with google" in normalize_key(label):
            return "Continue with Google to sign in quickly."
        return "Click Google Login to get started."
    if action_class == "card_selection":
        target = step.specific_target_label or default_course_target(step)
        return f"Open {article_phrase(target, noun='course' if len(target.split()) == 1 else '')} to continue into setup."
    if action_class in {"button_click", "focus", "result_state"}:
        return "Choose your starting level before you begin."
    return f"Continue with {label}."


def article_phrase(target: str, noun: str = "") -> str:
    cleaned = " ".join(target.split()).strip()
    lowered = cleaned.lower()
    if lowered.startswith(("the ", "a ", "an ", "your ")):
        return cleaned
    if noun and len(cleaned.split()) == 1:
        return f"the {cleaned} {noun}"
    return f"the {cleaned}"


def is_weak_editorial_copy(text: str, step: GuideStepRecord) -> bool:
    cleaned = " ".join((text or "").split()).strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    if len(cleaned.split()) > 5:
        return True
    if any(marker in lowered for marker in ("click ", "choose ", "open ", "continue ", "move attention", "review ")):
        return True
    if step.action_class == "card_selection" and any(token in lowered for token in ("login", "google", "sign in")):
        return True
    return False


def mismatched_step_family(text: str, action_class: str) -> bool:
    lowered = normalize_key(text)
    auth_tokens = ("google", "login", "log in", "sign in", "account")
    selection_tokens = ("course", "workspace", "template", "plan", "project", "japanese")
    if action_class == "card_selection":
        return any(token in lowered for token in auth_tokens)
    if action_class == "auth_action":
        return any(token in lowered for token in selection_tokens)
    return False


def invalid_specific_target(text: str, action_class: str) -> bool:
    if not text:
        return False
    return mismatched_step_family(text, action_class)


def default_course_target(step: GuideStepRecord) -> str:
    candidate = selection_target_from_excerpt(step.source_excerpt)
    if candidate:
        return title_case(candidate)
    return "the selected option"


def normalize_key(text: str) -> str:
    return " ".join((text or "").lower().split()).strip().rstrip(".")


def loses_specific_qualifier(candidate: str, preferred: str) -> bool:
    preferred_tokens = qualifier_tokens(preferred)
    if not preferred_tokens:
        return False
    candidate_tokens = qualifier_tokens(candidate)
    return not preferred_tokens.issubset(candidate_tokens)


def qualifier_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in text.split()
        if meaningful_qualifier_token(token)
    }


def meaningful_qualifier_token(token: str) -> bool:
    lowered = token.lower().strip().rstrip(".")
    if lowered in GENERIC_EDITORIAL_TOKENS:
        return False
    if len(lowered) >= 4:
        return True
    if len(lowered) >= 3 and lowered.isalpha():
        return True
    if lowered.isdigit():
        return True
    if any(char.isdigit() for char in lowered) and len(lowered) >= 2:
        return True
    return token.isupper() and len(token) >= 2
