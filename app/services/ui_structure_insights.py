from __future__ import annotations

from typing import Literal

from app.models.projects import FrameSignalRecord
from app.services.inferred_recording_support import box_area, normalize_label

StructureKind = Literal["picker", "dashboard", "result", "generic"]
HEADER_WORDS = ("choose", "pick", "select", "dashboard", "overview", "before you start")


def frame_structure(frame: FrameSignalRecord, visible_labels: list[str]) -> StructureKind:
    labels = collected_labels(frame, visible_labels)
    if picker_signal(frame, labels):
        return "picker"
    if dashboard_signal(labels):
        return "dashboard"
    if result_signal(labels):
        return "result"
    return "generic"


def structure_state_label(frame: FrameSignalRecord, visible_labels: list[str]) -> str:
    structure = frame_structure(frame, visible_labels)
    preferred = preferred_structure_header(frame, visible_labels, structure)
    if preferred:
        return preferred
    ranked = sorted(frame.ui_elements, key=lambda item: structure_rank(item.label, item.role, structure), reverse=True)
    if ranked and structure_rank(ranked[0].label, ranked[0].role, structure) >= 0.6:
        return ranked[0].label.strip()
    visible = sorted((label for label in visible_labels if label.strip()), key=lambda item: structure_rank(item, "visible", structure), reverse=True)
    return visible[0].strip() if visible and structure_rank(visible[0], "visible", structure) >= 0.6 else ""


def prefers_state_event(frame: FrameSignalRecord, visible_labels: list[str]) -> bool:
    return frame_structure(frame, visible_labels) in {"picker", "dashboard", "result"}


def compact_action_target(frame: FrameSignalRecord) -> bool:
    return frame.click_target_box is not None and box_area(frame.click_target_box) <= 0.14


def picker_signal(frame: FrameSignalRecord, labels: list[str]) -> bool:
    label_text = " ".join(labels)
    list_roles = sum(1 for item in frame.ui_elements if item.role.lower() in {"dialog", "list", "listitem"})
    return "choose an account" in label_text or "account list" in label_text or (list_roles >= 2 and "account" in label_text)


def dashboard_signal(labels: list[str]) -> bool:
    label_text = " ".join(labels)
    course_cards = sum(1 for label in labels if "course" in label or "coming soon" in label or "open course" in label)
    return "dashboard" in label_text or "select a course" in label_text or course_cards >= 3


def result_signal(labels: list[str]) -> bool:
    label_text = " ".join(labels)
    level_cards = sum(1 for label in labels if "jlpt level" in label)
    return "pick your" in label_text or "choose your" in label_text or level_cards >= 3


def collected_labels(frame: FrameSignalRecord, visible_labels: list[str]) -> list[str]:
    labels = [normalize_label(item.label) for item in frame.ui_elements if item.label.strip()]
    labels.extend(normalize_label(label) for label in visible_labels if label.strip())
    return [label for label in labels if label]


def frame_local_labels(
    frame: FrameSignalRecord,
    visible_labels: list[str],
) -> list[str]:
    labels = [item.label.strip() for item in frame.ui_elements if item.label.strip()]
    if labels:
        return list(dict.fromkeys(labels))
    return [label.strip() for label in visible_labels if label.strip()]


def structure_rank(label: str, role: str, structure: StructureKind) -> float:
    normalized = normalize_label(label)
    if not normalized:
        return 0.0
    role_bonus = 0.34 if any(word in role.lower() for word in ("header", "heading", "dialog", "text")) else 0.12
    header_bonus = 0.3 if any(phrase in normalized for phrase in HEADER_WORDS) else 0.0
    if structure == "picker":
        return role_bonus + header_bonus + (0.26 if "account" in normalized else 0.0)
    if structure == "dashboard":
        return role_bonus + header_bonus + (0.24 if "dashboard" in normalized or "select a course" in normalized else 0.08 if "course" in normalized else 0.0)
    if structure == "result":
        return role_bonus + header_bonus + (0.26 if "pick your" in normalized or "choose your" in normalized else 0.0)
    return 0.0


def preferred_structure_header(
    frame: FrameSignalRecord,
    visible_labels: list[str],
    structure: StructureKind,
) -> str:
    labels = [item.label.strip() for item in frame.ui_elements if item.label.strip()]
    labels.extend(label.strip() for label in visible_labels if label.strip())
    normalized = [(label, normalize_label(label)) for label in labels]
    if structure == "dashboard":
        return next((label for label, key in normalized if "select a course" in key), "")
    if structure == "picker":
        return next((label for label, key in normalized if "choose an account" in key), "")
    if structure == "result":
        return next((label for label, key in normalized if "pick your" in key or "choose your" in key), "")
    return ""
