from __future__ import annotations

import re

LONG_LABEL_WORDS = 6
MAX_HIGHLIGHT_WORDS = 3
MAX_ON_SCREEN_WORDS = 5
CLAUSE_MARKERS = (" before ", " after ", " to ", " so ", " once ", " when ", " while ", " then ")
LEADING_VERBS = (
    "click ",
    "tap ",
    "select ",
    "choose ",
    "pick ",
    "open ",
    "continue with ",
    "continue ",
    "use ",
    "review ",
    "set ",
)
GENERIC_OBJECTS = {"step", "screen", "page", "option", "field", "control", "button"}
SELECTION_NOUNS = ("course", "courses", "plan", "plans", "template", "templates", "workspace", "workspaces", "project", "projects")
ADJECTIVE_LIKE_SUFFIXES = ("ese", "ish", "ian", "ic", "al", "ary", "ory", "able", "ible", "less", "ful")
GENERIC_HIGHLIGHT_WORDS = {"your", "the", "a", "an", "my", "our", "step", "screen", "page", "option", "field", "control", "button"}
AUTH_PROVIDER_WORDS = {"google", "github", "microsoft", "apple", "email", "sso", "account"}
SETUP_GENERIC_WORDS = {"continue", "pick", "choose", "select", "open", "use", "set", "level", "course", "plan", "workspace", "project", "option"}


def canonical_step_title(
    *,
    label: str,
    action_class: str,
    event_type: str,
    screen_after: str = "",
) -> str:
    cleaned = clean_editorial_text(label)
    if not cleaned:
        return fallback_title(action_class, event_type, screen_after)
    if is_short_action_label(cleaned):
        return title_case(cleaned)
    phrase = object_phrase(cleaned)
    if not phrase:
        return fallback_title(action_class, event_type, screen_after)
    if action_class == "auth_action":
        if cleaned.lower().startswith("continue with "):
            return title_case(cleaned)
        if "login" in cleaned.lower() or "sign in" in cleaned.lower():
            return title_case(cleaned)
        return title_case(f"Continue {phrase}")
    if action_class in {"button_click", "focus"} or "picker" in screen_after or "setup" in screen_after:
        return title_case(configuration_title(phrase))
    if action_class == "card_selection":
        return title_case(selection_title(phrase))
    if event_type == "focus":
        return title_case(configuration_title(phrase))
    return title_case(phrase)


def canonical_on_screen_text(
    *,
    label: str,
    title: str,
) -> str:
    cleaned = clean_editorial_text(label)
    if cleaned and len(cleaned.split()) <= MAX_ON_SCREEN_WORDS and not sentence_like(cleaned):
        return title_case(cleaned)
    return title


def contextual_on_screen_text(
    *,
    label: str,
    title: str,
    action_class: str,
    transcript_excerpt: str = "",
) -> str:
    specific = specific_target_label(label=label, action_class=action_class, transcript_excerpt=transcript_excerpt)
    if specific:
        return specific
    return canonical_on_screen_text(label=label, title=title)


def canonical_highlight_label(
    *,
    label: str,
    title: str,
) -> str:
    cleaned_label = clean_editorial_text(label)
    if preserved_auth_highlight(cleaned_label):
        return title_case(cleaned_label)
    source = object_phrase(label) or title or label
    cleaned = title_case(source)
    words = cleaned.split()
    if len(words) <= MAX_HIGHLIGHT_WORDS:
        return cleaned
    compact = compact_highlight_words(words)
    return compact or " ".join(words[:MAX_HIGHLIGHT_WORDS]).strip()


def compact_highlight_words(words: list[str]) -> str:
    preferred = [word for word in words if word.lower() not in GENERIC_HIGHLIGHT_WORDS]
    if preserved_specific_highlight(words):
        specific = [word for word in preferred if word.lower() not in SETUP_GENERIC_WORDS]
        merged = (specific[:1] + preferred) if specific else preferred
        compact = unique_words(merged)[:MAX_HIGHLIGHT_WORDS]
        if compact:
            return " ".join(compact).strip()
    compact = preferred[:MAX_HIGHLIGHT_WORDS]
    return " ".join(compact).strip()


def preserved_specific_highlight(words: list[str]) -> bool:
    lowered = [word.lower() for word in words]
    if any(word in AUTH_PROVIDER_WORDS for word in lowered):
        return True
    return any(
        word not in SETUP_GENERIC_WORDS
        and word not in GENERIC_HIGHLIGHT_WORDS
        and len(word) >= 4
        for word in lowered
    )


def unique_words(words: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(word)
    return unique


def specific_target_label(
    *,
    label: str,
    action_class: str,
    transcript_excerpt: str = "",
) -> str:
    cleaned = clean_editorial_text(label)
    if action_class != "card_selection":
        return ""
    if cleaned and cleaned.lower() not in {"select a course", "choose a course", "select course", "a course", "course"}:
        return title_case(cleaned)
    return title_case(selection_target_from_excerpt(transcript_excerpt))


def safe_selection_target(
    *,
    label: str,
    transcript_excerpt: str,
) -> str:
    cleaned = object_phrase(label) or clean_editorial_text(label)
    if cleaned and cleaned.lower() not in {"select a course", "choose a course", "select course", "a course", "course", "select an option", "choose an option"}:
        return title_case(cleaned)
    candidate = selection_target_from_excerpt(transcript_excerpt)
    if candidate:
        return title_case(candidate)
    noun = selection_noun_from_label_or_excerpt(label, transcript_excerpt)
    return f"the selected {noun}" if noun else "the selected option"


def selection_target_phrase(
    *,
    specific_target_label: str,
    canonical_label: str,
    transcript_excerpt: str,
) -> str:
    target = clean_editorial_text(specific_target_label)
    noun = selection_noun_from_label_or_excerpt(canonical_label, transcript_excerpt)
    if not target:
        return safe_selection_target(label=canonical_label, transcript_excerpt=transcript_excerpt)
    if target.lower().startswith(("the ", "a ", "an ", "your ")):
        return target
    if len(target.split()) == 1 and noun and noun != "option":
        return f"the {title_case(target)} {noun}"
    return f"the {target}"


def clean_editorial_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").replace("_", " ")).strip().strip(".")
    for marker in CLAUSE_MARKERS:
        index = cleaned.lower().find(marker)
        if index > 0:
            cleaned = cleaned[:index].strip(" ,.")
            break
    return cleaned


def is_short_action_label(label: str) -> bool:
    lowered = label.lower()
    return len(label.split()) <= LONG_LABEL_WORDS and not sentence_like(label) and not lowered.startswith("pick your ")


def sentence_like(label: str) -> bool:
    lowered = label.lower()
    return (
        len(label.split()) > LONG_LABEL_WORDS
        or any(marker.strip() in lowered for marker in ("before", "after", "once", "when", "while"))
    )


def object_phrase(label: str) -> str:
    cleaned = clean_editorial_text(label)
    lowered = cleaned.lower()
    for prefix in LEADING_VERBS:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            lowered = cleaned.lower()
            break
    words = [word for word in cleaned.split() if word]
    if not words:
        return ""
    while words and words[0].lower() in {"the", "a", "an"}:
        words.pop(0)
    if not words:
        return ""
    if words[0].lower() == "your":
        return "your " + " ".join(words[1:]).strip()
    return " ".join(words)


def configuration_title(phrase: str) -> str:
    normalized = phrase.strip()
    lowered = normalized.lower()
    if lowered.startswith("your "):
        return f"Choose {normalized}"
    if any(token in lowered for token in ("level", "plan", "workspace", "preference", "settings", "role", "template")):
        return f"Choose Your {strip_possessive(normalized)}"
    return f"Choose {normalized}"


def selection_title(phrase: str) -> str:
    lowered = phrase.lower()
    if any(token in lowered for token in ("course", "plan", "template", "workspace", "project", "option")):
        return f"Select {phrase}"
    return f"Choose {phrase}"


def strip_possessive(phrase: str) -> str:
    lowered = phrase.lower()
    if lowered.startswith("your "):
        return phrase[5:].strip()
    return phrase


def fallback_title(action_class: str, event_type: str, screen_after: str) -> str:
    if action_class == "auth_action":
        return "Sign In"
    if action_class == "card_selection":
        return "Select An Option"
    if action_class in {"button_click", "focus"} or "picker" in screen_after:
        return "Choose Your Settings"
    if event_type == "focus":
        return "Review The Screen"
    return "Continue"


def preserved_auth_highlight(label: str) -> bool:
    lowered = label.lower()
    if len(label.split()) > 4:
        return False
    auth_markers = (
        "continue with ",
        "sign in with ",
        "sign up with ",
        "log in with ",
        "login with ",
        "google login",
        "choose an account",
    )
    return any(marker in lowered for marker in auth_markers)


def title_case(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    result: list[str] = []
    for word in words:
        if word.lower() in {"a", "an", "the", "to", "of", "and"} and result:
            result.append(word.lower())
        else:
            result.append(word[:1].upper() + word[1:])
    return " ".join(result)


def selection_target_from_excerpt(excerpt: str) -> str:
    lowered = re.sub(r"\s+", " ", (excerpt or "").replace("_", " ")).strip().strip(".").lower()
    if not lowered:
        return ""
    for noun in SELECTION_NOUNS:
        pattern = rf"\b([a-z0-9][a-z0-9\s&/-]{{0,40}}?)\s+{noun}\b"
        matches = re.findall(pattern, lowered)
        if matches:
            phrase = cleaned_selection_phrase(matches[-1])
            if phrase:
                singular = noun[:-1] if noun.endswith("s") else noun
                if selection_phrase_quality(phrase, singular) >= 0.7:
                    return f"{phrase} {singular}"
                return ""
    return ""


def selection_noun_from_label_or_excerpt(label: str, excerpt: str) -> str:
    lowered = f"{clean_editorial_text(label)} {excerpt}".lower()
    for noun in SELECTION_NOUNS:
        singular = noun[:-1] if noun.endswith("s") else noun
        if noun in lowered or singular in lowered:
            return singular
    return "option"


def completed_selection_target(
    *,
    specific_target_label: str,
    canonical_label: str,
    transcript_excerpt: str,
) -> str:
    target = object_phrase(specific_target_label) or clean_editorial_text(specific_target_label)
    if not target:
        return safe_selection_target(label=canonical_label, transcript_excerpt=transcript_excerpt)
    noun = selection_noun_from_label_or_excerpt(canonical_label, transcript_excerpt)
    if target.lower().startswith(("the ", "a ", "an ", "your ")):
        return target
    if len(target.split()) == 1 and noun and noun != "option":
        return f"the {title_case(target)} {noun}"
    return f"the {target}"


def selection_phrase_quality(token: str, noun: str) -> float:
    score = 0.0
    words = token.split()
    head = words[-1] if words else token
    if head.endswith(ADJECTIVE_LIKE_SUFFIXES):
        score += 0.9
    if token in {"selected", "recommended", "featured", "primary"}:
        score += 0.8
    if any(len(word) >= 5 for word in words):
        score += 0.2
    if len(words) >= 2:
        score += 0.35
    if noun in {"template", "workspace", "project"}:
        score += 0.1
    return min(score, 1.0)


def cleaned_selection_phrase(text: str) -> str:
    raw_words = [word for word in re.split(r"\s+", text.strip()) if word]
    if not raw_words:
        return ""
    stopwords = {"the", "a", "an", "this", "that", "existing", "available", "selected", "recommended", "featured", "primary"}
    while raw_words and raw_words[0] in stopwords:
        raw_words.pop(0)
    while raw_words and raw_words[-1] in stopwords:
        raw_words.pop()
    while raw_words and any(" ".join(raw_words).startswith(prefix.strip()) for prefix in LEADING_VERBS):
        raw_words.pop(0)
    filtered = [word for word in raw_words if word not in {"with", "for", "to", "into", "from"}]
    if not filtered:
        return ""
    if len(filtered) > 3:
        filtered = filtered[-3:]
    if all(len(word) <= 2 for word in filtered):
        return ""
    return " ".join(filtered)
