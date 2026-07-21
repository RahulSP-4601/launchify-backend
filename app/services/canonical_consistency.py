from __future__ import annotations

from typing import Literal

from app.models.projects import SessionEventRecord
from app.services.inferred_recording_support import normalize_label

BranchFamily = Literal["existing", "create", "generic"]


def branch_family(*texts: str) -> BranchFamily:
    combined = " ".join(normalize_label(text) for text in texts if text)
    if not combined:
        return "generic"
    if create_branch_tokens(combined):
        return "create"
    if existing_branch_tokens(combined):
        return "existing"
    return "generic"


def existing_branch_tokens(text: str) -> bool:
    return any(
        token in text
        for token in (
            "continue with google",
            "log in",
            "login",
            "sign in",
            "existing",
            "choose an account",
        )
    )


def create_branch_tokens(text: str) -> bool:
    return any(token in text for token in ("create account", "sign up", "signup"))


def auth_mapping_conflict(
    raw_target: str,
    canonical_label: str,
    result_label: str = "",
) -> bool:
    raw_branch = branch_family(raw_target)
    canonical_branch = branch_family(canonical_label)
    result_branch = branch_family(result_label)
    if raw_branch == "generic":
        return False
    if canonical_branch != "generic" and canonical_branch != raw_branch:
        return True
    if result_branch != "generic" and result_branch != raw_branch:
        return True
    return False


def result_state_conflict(
    raw_target: str,
    result_label: str,
    screen_after: str,
) -> bool:
    raw_branch = branch_family(raw_target)
    result_branch = branch_family(result_label)
    if raw_branch == "generic" or result_branch == "generic":
        return False
    if raw_branch != result_branch and screen_after in {"generic", "result_state", "auth_provider", "unknown"}:
        return True
    return False


def event_branch_family(event: SessionEventRecord) -> BranchFamily:
    return branch_family(
        event.target.label,
        event.target.text,
        event.metadata.get("canonical_label", ""),
        event.metadata.get("raw_target_label", ""),
        event.metadata.get("transcript_excerpt", ""),
    )

