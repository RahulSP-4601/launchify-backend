from __future__ import annotations

from app.models.projects import EditPlanScene


def polished_auth_transcript(line: str, scene: EditPlanScene, context_excerpt: str, target: str) -> str:
    lowered = line.lower()
    context = context_excerpt.lower()
    target_lowered = target.lower()
    if "google login" in target_lowered and any(token in context for token in ("create a new account", "create an account", "existing one", "existing account")):
        return (
            f"To get started, go to the top right and click {target}. "
            "From there, new users can create an account, while returning users can log in with an existing one."
        )
    if "continue with google" in target_lowered:
        if any(token in context for token in ("existing account", "existing one", "already created", "already created the account")):
            return "If you already have an account, continue with Google to sign in and move straight into the workspace."
        if any(token in context for token in ("create a new account", "create an account")):
            return "Continue with Google to either create a new account or sign in with an existing one before entering the workspace."
        return "Continue with Google to complete sign-in and move directly into the workspace."
    if "google login" in target_lowered or ("login" in target_lowered and "continue" not in target_lowered):
        return f"To get started, go to the top right and click {target}."
    if "existing account" in lowered or "existing one" in lowered:
        return "Log in with the existing account so the workspace opens right away."
    if "create a new account" in lowered or "create an account" in lowered:
        return "Once you click it, you can create a new account or log in with an existing one."
    if "google login" in lowered or "top right" in lowered:
        return f"To get started, go to the top right and click {target}."
    return line


def polished_selection_transcript(line: str, scene: EditPlanScene, context_excerpt: str, target: str) -> str:
    context = context_excerpt.lower()
    if "five courses" in context and "coming soon" in context:
        return (
            f"After sign-in, the dashboard shows five courses. {target} is ready to start while the other paths are still marked coming soon, "
            f"so choose {target} to enter the live learning flow."
        )
    if "five courses" in context:
        return f"After sign-in, the dashboard opens with five courses, and {target} is ready to open first."
    if "coming soon" in context:
        return f"Right now, {target} is the live course while the other paths are still marked coming soon."
    if "click on" in context or "open" in context or "japanese course" in context or "japan course" in context:
        return f"From the course library, choose {target} to open the live learning path."
    if (scene.render_duration_seconds or 0.0) >= 8.0:
        return f"From the course library, choose {target} to enter the live learning path and move into setup."
    return f"Choose {target} to open the live learning path."


def level_voiceover_line(scene: EditPlanScene, context_excerpt: str = "") -> str:
    label = normalized_level_target(scene.on_screen_text or scene.title)
    context = context_excerpt.lower()
    if any(token in context for token in ("start learning", "before you start", "before the lesson", "starting point")):
        return f"Choose {label} first so the lesson flow opens at the right starting point."
    if any(token in context for token in ("level", "difficulty", "path")):
        return f"Next, choose {label} so the learning path opens at the right difficulty for the learner."
    return f"Choose {label} so the lesson path starts at the right level and stays aligned with the learner's starting point."


def auth_voiceover_line(scene: EditPlanScene, target: str) -> str:
    lowered = target.lower()
    if lowered == "continue with google":
        return "Continue with Google to sign in and move directly into the workspace."
    if "account" in lowered:
        return f"Choose {target} so the workspace opens right away."
    return f"Use {target} to sign in and continue into the product."


def selection_voiceover_line(scene: EditPlanScene, target: str, mentions_courses: bool) -> str:
    if mentions_courses:
        return f"From the course library, choose {target} to open the live learning path."
    return f"Choose {target} to open the next learning path."


def concise_auth_voiceover(scene: EditPlanScene, target: str, context_excerpt: str = "") -> str:
    lowered = target.lower()
    context = context_excerpt.lower()
    if "continue with google" in lowered:
        if any(token in context for token in ("existing account", "existing one", "already created", "already created the account")):
            return "If you already have an account, continue with Google to enter the workspace."
        return "Continue with Google to sign in and enter the workspace."
    if "google login" in lowered or ("login" in lowered and "continue" not in lowered):
        if any(token in context for token in ("create a new account", "create an account", "existing one", "existing account")):
            return "Click Google Login to create an account or sign in."
        return "Click Google Login to get started."
    if "account" in lowered:
        return "Log in with the existing account."
    return f"Use {target} to sign in."


def concise_selection_voiceover(target: str, mentions_courses: bool) -> str:
    if mentions_courses:
        return f"{target} is live now, so choose it to open the course."
    return f"Choose {target} to open the live learning path."


def concise_level_voiceover(scene: EditPlanScene, context_excerpt: str = "") -> str:
    label = normalized_level_target(scene.on_screen_text or scene.title)
    context = context_excerpt.lower()
    if any(token in context for token in ("starting point", "start learning", "lesson", "path")):
        return f"Choose {label} so the lesson flow starts at the right starting point."
    return f"Choose {label} so the lesson path starts at the right level."


def normalized_level_target(source: str) -> str:
    cleaned = " ".join(source.split()).strip().rstrip(".")
    lowered = cleaned.lower()
    if "japanese level" in lowered:
        return "the Japanese level"
    if lowered.startswith(("pick your ", "choose your ", "select your ")):
        lowered = lowered.split(" ", 2)[-1]
        return f"the {lowered}".strip()
    return cleaned if lowered.startswith(("the ", "your ")) else f"the {cleaned}".strip()
