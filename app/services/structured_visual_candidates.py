from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox, FrameSignalRecord, UiElementRecord
from app.services.inferred_recording_support import box_area, box_center_delta, intent_overlap_score, intent_tokens, normalize_label, state_like_label

AFFORDANCE_WORDS = frozenset({"continue", "enter", "login", "log", "open", "select", "sign", "start"})


@dataclass(frozen=True)
class StructuredVisualCandidate:
    label: str
    box: FocusBox | None
    source_weight: float


def structured_visual_candidates(
    frame: FrameSignalRecord,
    transcript_excerpt: str,
    source_excerpt: str,
) -> list[StructuredVisualCandidate]:
    grouped = element_groups([element for element in frame.ui_elements if element.label.strip() and element.box is not None])
    tokens = intent_tokens(transcript_excerpt, source_excerpt)
    candidates: list[StructuredVisualCandidate] = []
    for group in grouped:
        label = group_label(group, tokens)
        box = merged_group_box(group)
        if label:
            candidates.append(StructuredVisualCandidate(label=label, box=box, source_weight=1.14))
    return candidates


def element_groups(elements: list[UiElementRecord]) -> list[list[UiElementRecord]]:
    groups: list[list[UiElementRecord]] = []
    for element in sorted(elements, key=lambda item: (item.box.y, item.box.x)):
        target_group = next((group for group in groups if belongs_to_group(element, group)), None)
        if target_group is None:
            groups.append([element])
            continue
        target_group.append(element)
    return groups


def belongs_to_group(element: UiElementRecord, group: list[UiElementRecord]) -> bool:
    for candidate in group:
        if boxes_related(element.box, candidate.box):
            return True
    return False


def boxes_related(left: FocusBox, right: FocusBox) -> bool:
    same_band = abs((left.y + left.height / 2) - (right.y + right.height / 2)) <= 0.14
    overlapping = overlap_ratio(left, right) >= 0.08
    nearby = box_center_delta(left, right) <= 0.22
    return overlapping or (same_band and nearby)


def overlap_ratio(left: FocusBox, right: FocusBox) -> float:
    left_x2 = left.x + left.width
    left_y2 = left.y + left.height
    right_x2 = right.x + right.width
    right_y2 = right.y + right.height
    overlap_width = max(0.0, min(left_x2, right_x2) - max(left.x, right.x))
    overlap_height = max(0.0, min(left_y2, right_y2) - max(left.y, right.y))
    overlap_area = overlap_width * overlap_height
    if overlap_area <= 0:
        return 0.0
    return overlap_area / max(min(box_area(left), box_area(right)), 0.0001)


def group_label(group: list[UiElementRecord], tokens: set[str]) -> str:
    labels = [element.label.strip() for element in group if element.label.strip()]
    if not labels:
        return ""
    affordance = next((label for label in labels if has_affordance(label)), "")
    matched = sorted(labels, key=lambda label: intent_overlap_score(label, tokens), reverse=True)
    primary = next((label for label in matched if not state_like_label(label) and not has_affordance(label)), matched[0])
    if affordance and primary and normalize_label(affordance) != normalize_label(primary):
        return f"{primary} {affordance}".strip()
    return primary or affordance


def merged_group_box(group: list[UiElementRecord]) -> FocusBox | None:
    if not group:
        return None
    min_x = min(element.box.x for element in group)
    min_y = min(element.box.y for element in group)
    max_x = max(element.box.x + element.box.width for element in group)
    max_y = max(element.box.y + element.box.height for element in group)
    return FocusBox(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y)


def has_affordance(label: str) -> bool:
    return bool(set(normalize_label(label).split()) & AFFORDANCE_WORDS)
