from __future__ import annotations

import re

from app.models.projects import GuideStepRecord, LaunchScriptScene

FILLER_PHRASES = (
    "hey so",
    "so to get started",
    "just you you need to",
    "since i've already",
    "right now",
    "officially",
)
ACTION_PREFIXES = ("click ", "select ", "choose ", "open ", "use ", "continue with ")


def normalized_step(step: GuideStepRecord) -> GuideStepRecord:
    narration = normalized_narration(step.narration, step.focus_label or step.title, step.instruction, step.action_class)
    instruction = normalized_instruction(step.instruction, step.focus_label or step.title, step.event_type)
    return step.model_copy(update={"narration": narration, "instruction": instruction, "source_excerpt": compact_excerpt(step.source_excerpt or narration)})


def normalized_scene(scene: LaunchScriptScene) -> LaunchScriptScene:
    narration = normalized_narration(scene.spoken_line, scene.on_screen_text or scene.purpose, scene.purpose, inferred_action_class(scene))
    purpose = normalized_purpose(scene.purpose, scene.on_screen_text or scene.source_excerpt)
    return scene.model_copy(update={"spoken_line": narration, "purpose": purpose, "source_excerpt": compact_excerpt(scene.source_excerpt or narration)})


def normalized_narration(text: str, label: str, fallback: str, action_class: str) -> str:
    cleaned = cleaned_text(text or fallback)
    if action_class == "result_state":
        return sentence(cleaned if cleaned else f"You'll see {article(label)}")
    if len(cleaned.split()) <= 4 or transcript_like(cleaned):
        return sentence(action_led_line(label, fallback, action_class))
    return sentence(cleaned)


def normalized_instruction(text: str, label: str, event_type: str) -> str:
    cleaned = cleaned_text(text)
    if cleaned and not transcript_like(cleaned):
        return sentence(cleaned)
    if event_type == "focus":
        return sentence(f"Review {article(label)}")
    return sentence(f"Select {article(label)}")


def normalized_purpose(text: str, label: str) -> str:
    cleaned = cleaned_text(text)
    if cleaned and not transcript_like(cleaned):
        return sentence(cleaned)
    return sentence(f"Guide the viewer through {article(label)}")


def compact_excerpt(text: str) -> str:
    cleaned = cleaned_text(text)
    words = cleaned.split()
    return " ".join(words[:16]).strip()


def action_led_line(label: str, fallback: str, action_class: str) -> str:
    target = article(label or fallback or "this step")
    if action_class == "auth_action":
        return f"Use {target} to move through sign in"
    if action_class == "card_selection":
        return f"Choose {target} to continue"
    return f"Select {target} to move forward"


def inferred_action_class(scene: LaunchScriptScene) -> str:
    combined = f"{scene.purpose} {scene.on_screen_text} {scene.source_excerpt}".lower()
    if "google" in combined or "login" in combined or "account" in combined:
        return "auth_action"
    if "pick your" in combined or "select a course" in combined:
        return "result_state"
    if "japanese" in combined:
        return "card_selection"
    return "generic_action"


def cleaned_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip().strip(".")
    lowered = cleaned.lower()
    for phrase in FILLER_PHRASES:
        lowered = lowered.replace(phrase, " ")
    cleaned = re.sub(r"\s+", " ", lowered).strip(" ,.")
    return cleaned


def transcript_like(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in ("you need to", "once you click", "after log in", "there are five"))


def article(label: str) -> str:
    cleaned = cleaned_text(label)
    if not cleaned:
        return "this step"
    if cleaned.startswith(("the ", "your ", "a ", "an ")):
        return cleaned
    for prefix in ACTION_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    return f"the {cleaned}".strip()


def sentence(text: str) -> str:
    cleaned = cleaned_text(text)
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."
