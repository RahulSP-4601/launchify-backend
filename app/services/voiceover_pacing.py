from __future__ import annotations

CONNECTOR_WORDS = {"and", "then", "next", "so", "now"}
LEADING_FILLERS = ("now ", "then ", "so ", "just ", "here ", "you can ")
MIN_WORDS = 4


def fit_voice_line(text: str, available_seconds: float) -> str:
    cleaned = polish(text)
    if not cleaned or estimated_duration(cleaned) <= max(available_seconds, 0.8):
        return cleaned
    shortened = first_clause(cleaned) or trimmed_words(cleaned, available_seconds)
    return polish(shortened)


def first_clause(text: str) -> str:
    for separator in (". ", ", ", "; ", ": "):
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if len(head.split()) >= MIN_WORDS:
                return head
    return ""


def trimmed_words(text: str, available_seconds: float) -> str:
    limit = max(MIN_WORDS, min(len(text.split()), int(round(max(available_seconds, 1.0) * 2.4))))
    words = text.split()[:limit]
    while len(words) > MIN_WORDS and words[-1].lower().strip(".,") in CONNECTOR_WORDS:
        words.pop()
    return " ".join(words)


def normalize(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."


def polish(text: str) -> str:
    cleaned = normalize(text)
    lowered = cleaned.lower()
    for prefix in LEADING_FILLERS:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            lowered = cleaned.lower()
    cleaned = cleaned.replace("Here you can ", "").replace("here you can ", "")
    return normalize(cleaned)


def estimated_duration(text: str) -> float:
    words = max(1, len(text.split()))
    return round(max(2.8, min(10.0, words / 2.6)), 2)
