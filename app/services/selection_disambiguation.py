from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox, GuideStepRecord, VisualSceneAnalysisRecord


@dataclass(frozen=True)
class SelectionCandidate:
    label: str
    score: float


def disambiguated_guide_steps(
    steps: list[GuideStepRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[GuideStepRecord]:
    analyses = list(analyses_by_scene.values())
    enriched: list[GuideStepRecord] = []
    for index, step in enumerate(steps):
        next_step = steps[index + 1] if index + 1 < len(steps) else None
        analysis = best_analysis_for_step(step, analyses)
        enriched.append(disambiguated_selection_step(step, analysis, next_step))
    return enriched


def best_analysis_for_step(
    step: GuideStepRecord,
    analyses: list[VisualSceneAnalysisRecord],
) -> VisualSceneAnalysisRecord | None:
    if not analyses:
        return None
    ranked = sorted(
        analyses,
        key=lambda analysis: (
            overlap_seconds(step.start, step.end, analysis.start, analysis.end),
            -abs(((analysis.start + analysis.end) / 2) - ((step.start + step.end) / 2)),
        ),
        reverse=True,
    )
    best = ranked[0]
    return best if overlap_seconds(step.start, step.end, best.start, best.end) > 0.0 else None


def overlap_seconds(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def disambiguated_selection_step(
    step: GuideStepRecord,
    analysis: VisualSceneAnalysisRecord | None,
    next_step: GuideStepRecord | None,
) -> GuideStepRecord:
    if step.specific_target_label.strip():
        return step
    if step.action_class != "card_selection":
        return step
    if analysis is None or not analysis.frames:
        return step
    candidate = strongest_selection_candidate(step, analysis, next_step)
    if candidate is None:
        return step
    return step.model_copy(
        update={
            "specific_target_label": candidate.label,
            "on_screen_text": candidate.label,
            "highlight_label": candidate.label,
        }
    )


def strongest_selection_candidate(
    step: GuideStepRecord,
    analysis: VisualSceneAnalysisRecord,
    next_step: GuideStepRecord | None,
) -> SelectionCandidate | None:
    context_tokens = selection_context_tokens(step)
    next_tokens = selection_followup_tokens(next_step)
    outcome_tokens = selection_outcome_tokens(analysis)
    ranked: list[SelectionCandidate] = []
    for frame in analysis.frames:
        if abs(frame.timestamp - step.start) > 3.0 and abs(frame.timestamp - step.end) > 3.0:
            continue
        anchor = frame.click_target_box or frame.cursor_box
        for element in frame.ui_elements:
            label = (element.label or "").strip()
            if not valid_specific_selection_candidate(label, element.role):
                continue
            label_tokens = normalized_tokens(label)
            score = 0.0
            score += element.confidence * 0.28
            if element.role in {"button", "card"}:
                score += 0.12
            score += min(token_overlap_score(context_tokens, label_tokens) * 0.18, 0.24)
            score += min(token_overlap_score(next_tokens, label_tokens) * 0.28, 0.34)
            score += min(token_overlap_score(outcome_tokens, label_tokens) * 0.2, 0.24)
            if anchor is not None:
                score += max(0.0, 0.18 - focus_box_distance(anchor, element.box)) * 0.9
            ranked.append(SelectionCandidate(label=label, score=score))
    if not ranked:
        return None
    collapsed = collapse_selection_candidates(ranked)
    best = collapsed[0]
    runner_up = collapsed[1].score if len(collapsed) > 1 else 0.0
    if best.score < 0.62:
        return None
    if best.score - runner_up < 0.1 and runner_up > 0.0:
        return None
    return best


def selection_context_tokens(step: GuideStepRecord) -> set[str]:
    raw = " ".join(
        part
        for part in (
            step.title,
            step.on_screen_text,
            step.narration,
            step.instruction,
            step.source_excerpt,
            step.highlight_label,
        )
        if part
    )
    return normalized_tokens(raw)


def selection_followup_tokens(next_step: GuideStepRecord | None) -> set[str]:
    if next_step is None:
        return set()
    raw = " ".join(
        part
        for part in (
            next_step.title,
            next_step.on_screen_text,
            next_step.narration,
            next_step.instruction,
            next_step.source_excerpt,
        )
        if part
    )
    return normalized_tokens(raw)


def selection_outcome_tokens(analysis: VisualSceneAnalysisRecord) -> set[str]:
    raw = " ".join(analysis.visible_labels)
    return normalized_tokens(raw)


def collapse_selection_candidates(candidates: list[SelectionCandidate]) -> list[SelectionCandidate]:
    grouped: dict[str, list[float]] = {}
    for candidate in candidates:
        key = candidate.label.strip()
        grouped.setdefault(key, []).append(candidate.score)
    collapsed = [
        SelectionCandidate(label=label, score=max(scores) + min(len(scores) * 0.03, 0.09))
        for label, scores in grouped.items()
    ]
    collapsed.sort(key=lambda candidate: candidate.score, reverse=True)
    return collapsed


def valid_specific_selection_candidate(label: str, role: str) -> bool:
    normalized = " ".join(label.lower().split())
    if not normalized or normalized in {"select a course", "select course", "open course", "coming soon"}:
        return False
    if any(token in normalized for token in ("google", "sign in", "log in", "login", "account", "create account")):
        return False
    normalized_role = " ".join((role or "").lower().split())
    if normalized_role in {"button", "card", "control", "course", "plan", "template", "workspace", "project", "option", "item"}:
        return True
    if normalized_role in {"text", "heading", "label", "badge", "chip", "tag", "icon"}:
        return False
    return looks_like_unknown_role_selection_target(normalized)


def normalized_tokens(text: str) -> set[str]:
    return {token for token in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if token}


def focus_box_distance(left: FocusBox, right: FocusBox) -> float:
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)


def token_overlap_score(context_tokens: set[str], label_tokens: set[str]) -> float:
    exact = len(context_tokens & label_tokens)
    if exact:
        return float(exact)
    context_roots = {token_root(token) for token in context_tokens}
    label_roots = {token_root(token) for token in label_tokens}
    fuzzy = len({root for root in label_roots if root and root in context_roots})
    return fuzzy * 0.8


def token_root(token: str) -> str:
    cleaned = token.lower().strip()
    if len(cleaned) <= 4:
        return cleaned
    for suffix in ("ese", "ish", "ian", "ing", "ers", "ies", "s"):
        if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 4:
            return cleaned[: -len(suffix)]
    return cleaned[:5]


def looks_like_unknown_role_selection_target(label: str) -> bool:
    words = label.split()
    if not words or len(words) > 3:
        return False
    if any(word in {"new", "pro", "beta", "free", "popular", "featured", "top", "hot"} for word in words):
        return False
    if len(words) == 1:
        token = words[0]
        if token.isdigit() or len(token) < 5:
            return False
    return any(any(character.isalpha() for character in word) and len(word) >= 4 for word in words)
