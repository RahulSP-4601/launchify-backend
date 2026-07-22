from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import EditPlanScene, FrameSignalRecord, LaunchScriptScene, ResponseStateKind, VisualSceneAnalysisRecord
from app.services.stable_state_reconstruction import StateFingerprint, frame_fingerprint

ACTION_DELAY_SECONDS = 0.22


@dataclass(frozen=True)
class SceneTransitionDecision:
    before_state_label: str
    before_state_structure: str
    action_target_label: str
    transition_evidence: str
    after_state_label: str
    after_state_structure: str
    transition_confidence: float
    response_state_kind: ResponseStateKind
    final_destination_label: str


def infer_scene_transition(
    scene: LaunchScriptScene | EditPlanScene,
    analysis: VisualSceneAnalysisRecord | None,
    *,
    start_time: float = 0.0,
    end_time: float | None = None,
    action_time: float | None = None,
) -> SceneTransitionDecision:
    if analysis is None or not analysis.frames:
        return fallback_transition(scene)
    scene_end = end_time if end_time is not None else getattr(scene, "end", analysis.end)
    anchor = choose_action_time(analysis, start_time, scene_end, action_time)
    before = frame_fingerprint(analysis, best_before_frame(analysis, anchor))
    action = frame_fingerprint(analysis, best_action_frame(analysis, anchor))
    immediate = frame_fingerprint(analysis, best_immediate_frame(analysis, anchor))
    settled = frame_fingerprint(analysis, best_settled_frame(analysis, anchor, scene_end))
    before_label = friendly_label(before, fallback_label(scene))
    target_label = resolved_target_label(scene, action)
    resolved_state = validated_resolved_state(scene, before, settled if settled is not None else immediate, target_label)
    after_label = friendly_label(resolved_state, before_label)
    changed_state = normalize_label(before_label) != normalize_label(after_label)
    waiting_state = response_kind(before, immediate, settled, changed_state)
    confidence = transition_confidence(before, action, immediate, settled, changed_state, waiting_state)
    evidence = transition_evidence(before, action, immediate, settled, waiting_state)
    final_destination = destination_label(settled, immediate, scene, target_label)
    return SceneTransitionDecision(
        before_state_label=before_label,
        before_state_structure=before.structure if before is not None else "",
        action_target_label=target_label,
        transition_evidence=evidence,
        after_state_label=after_label,
        after_state_structure=resolved_state.structure if resolved_state is not None else "",
        transition_confidence=confidence,
        response_state_kind=waiting_state,
        final_destination_label=final_destination,
    )


def response_state_kind(scene: EditPlanScene) -> ResponseStateKind:
    return scene.response_state_kind


def should_compress_waiting(scene: EditPlanScene) -> bool:
    return response_state_kind(scene) == "waiting" and scene.transition_confidence < 0.72


def has_clear_destination(scene: EditPlanScene) -> bool:
    return bool(scene.final_destination_label.strip() or scene.after_state_label.strip())


def semantic_voice_line(scene: EditPlanScene) -> str:
    target = scene.action_target_label or scene.specific_target_label or scene.on_screen_text or scene.title
    after = scene.final_destination_label or scene.after_state_label
    before = scene.before_state_label
    after_changed = bool(after and normalize_label(after) != normalize_label(before))
    if scene.transition_confidence < 0.46:
        return neutral_voice_line(scene, target, after)
    if scene.action_class == "auth_action":
        if response_state_kind(scene) == "waiting":
            return f"Use {compact_label(target)} to move through sign-in and into the product."
        if after_changed:
            return f"Use {compact_label(target)} to move from {article_label(before)} into {article_label(after)}."
        return f"Use {compact_label(target)} to sign in and continue."
    if scene.action_class == "card_selection":
        if after_changed:
            return f"Choose {compact_label(target)} so the flow lands on {article_label(after)}."
        return f"Choose {compact_label(target)} to continue into the learning flow."
    if scene.scene_role == "result" and after:
        return f"Hold on {article_label(after)} so the next step is easy to read."
    if scene.action_class == "button_click" and not after_changed:
        return neutral_voice_line(scene, target, after)
    if after and before and normalize_label(before) != normalize_label(after):
        return f"Move from {article_label(before)} into {article_label(after)}."
    return ""


def choose_action_time(
    analysis: VisualSceneAnalysisRecord,
    start_time: float,
    end_time: float,
    action_time: float | None,
) -> float:
    if action_time is not None:
        return action_time
    ranked = [frame for frame in analysis.frames if start_time <= frame.timestamp <= end_time]
    if not ranked:
        ranked = analysis.frames
    best = max(ranked, key=lambda frame: frame.click_confidence * 0.52 + frame.importance_score * 0.28 + frame.diff_score * 0.2)
    return best.timestamp


def best_before_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    candidates = [frame for frame in analysis.frames if frame.timestamp <= anchor]
    return candidates[-1] if candidates else (analysis.frames[0] if analysis.frames else None)


def best_action_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    candidates = [frame for frame in analysis.frames if abs(frame.timestamp - anchor) <= 0.65] or analysis.frames
    return max(candidates, key=lambda frame: frame.click_confidence * 0.5 + frame.importance_score * 0.26 + frame.diff_score * 0.24)


def best_immediate_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    candidates = [frame for frame in analysis.frames if frame.timestamp >= anchor + ACTION_DELAY_SECONDS]
    return candidates[0] if candidates else (analysis.frames[-1] if analysis.frames else None)


def best_settled_frame(
    analysis: VisualSceneAnalysisRecord,
    anchor: float,
    scene_end: float,
) -> FrameSignalRecord | None:
    candidates = [frame for frame in analysis.frames if anchor + ACTION_DELAY_SECONDS <= frame.timestamp <= scene_end]
    if not candidates:
        candidates = analysis.frames
    return max(candidates, key=lambda frame: (1.0 - frame.diff_score) * 0.42 + frame.importance_score * 0.24 + frame.ocr_confidence * 0.18 + frame.timestamp * 0.01)


def friendly_label(state: StateFingerprint | None, fallback: str) -> str:
    label = state.friendly_label if state is not None else ""
    return label.strip() or fallback.strip()


def fallback_transition(scene: LaunchScriptScene | EditPlanScene) -> SceneTransitionDecision:
    target = fallback_target(scene)
    after = fallback_label(scene)
    return SceneTransitionDecision(
        before_state_label=fallback_label(scene),
        before_state_structure="",
        action_target_label=target,
        transition_evidence="fallback scene text",
        after_state_label=after,
        after_state_structure="",
        transition_confidence=0.32,
        response_state_kind="unknown",
        final_destination_label=after,
    )


def fallback_label(scene: LaunchScriptScene | EditPlanScene) -> str:
    return first_non_empty(getattr(scene, "on_screen_text", ""), getattr(scene, "purpose", ""), getattr(scene, "title", ""), getattr(scene, "source_excerpt", ""))


def fallback_target(scene: LaunchScriptScene | EditPlanScene) -> str:
    return first_non_empty(getattr(scene, "specific_target_label", ""), getattr(scene, "on_screen_text", ""), getattr(scene, "title", ""), "the next step")


def resolved_target_label(
    scene: LaunchScriptScene | EditPlanScene,
    action: StateFingerprint | None,
) -> str:
    event_target = action.target_label if action is not None else ""
    specific = first_non_empty(getattr(scene, "specific_target_label", ""), getattr(scene, "on_screen_text", ""))
    if getattr(scene, "action_class", "") == "card_selection":
        if specific:
            return specific
    if event_target and concise_target(event_target):
        return event_target
    if specific and concise_target(specific):
        return specific
    return concise_target(fallback_target(scene)) or fallback_target(scene)


def response_kind(
    before: StateFingerprint | None,
    immediate: StateFingerprint | None,
    settled: StateFingerprint | None,
    changed_state: bool,
) -> ResponseStateKind:
    settled_state = settled or immediate
    if settled_state is None:
        return "unknown"
    if not changed_state:
        return "waiting" if getattr(settled_state, "stability_score", 0.0) < 0.72 else "static"
    structure = getattr(settled_state, "structure", "")
    if structure in {"result", "dashboard", "picker"}:
        return "response"
    return "response"


def transition_confidence(
    before: StateFingerprint | None,
    action: StateFingerprint | None,
    immediate: StateFingerprint | None,
    settled: StateFingerprint | None,
    changed_state: bool,
    waiting_state: str,
) -> float:
    score = 0.18
    if action is not None:
        score += min(getattr(action, "stability_score", 0.0) * 0.24, 0.24)
    if settled is not None:
        score += min(getattr(settled, "stability_score", 0.0) * 0.26, 0.26)
    elif immediate is not None:
        score += min(getattr(immediate, "stability_score", 0.0) * 0.18, 0.18)
    if changed_state:
        score += 0.24
    if before is not None and settled is not None and normalize_label(getattr(before, "friendly_label", "")) != normalize_label(getattr(settled, "friendly_label", "")):
        score += 0.12
    if waiting_state == "waiting":
        score -= 0.1
    return round(max(0.0, min(score, 1.0)), 3)


def transition_evidence(
    before: StateFingerprint | None,
    action: StateFingerprint | None,
    immediate: StateFingerprint | None,
    settled: StateFingerprint | None,
    waiting_state: str,
) -> str:
    fragments: list[str] = []
    if before is not None and before.friendly_label:
        fragments.append(f"start:{before.friendly_label}")
    if action is not None and action.target_label:
        fragments.append(f"target:{action.target_label}")
    if settled is not None and settled.friendly_label:
        fragments.append(f"result:{settled.friendly_label}")
    elif immediate is not None and immediate.friendly_label:
        fragments.append(f"immediate:{immediate.friendly_label}")
    fragments.append(f"response:{waiting_state}")
    return " | ".join(fragments)[:240]


def destination_label(
    settled: StateFingerprint | None,
    immediate: StateFingerprint | None,
    scene: LaunchScriptScene | EditPlanScene,
    target_label: str,
) -> str:
    state = settled if settled is not None else immediate
    if state is not None and state.friendly_label.strip():
        if destination_conflicts(scene, state, target_label):
            return ""
        return state.friendly_label.strip()
    return first_non_empty(scene.on_screen_text, scene.purpose, target_label)


def validated_resolved_state(
    scene: LaunchScriptScene | EditPlanScene,
    before: StateFingerprint | None,
    candidate: StateFingerprint | None,
    target_label: str,
) -> StateFingerprint | None:
    if candidate is None or not destination_conflicts(scene, candidate, target_label):
        return candidate
    return before


def destination_conflicts(
    scene: LaunchScriptScene | EditPlanScene,
    state: StateFingerprint,
    target_label: str,
) -> bool:
    label = normalize_label(state.friendly_label)
    scene_text = normalize_label(" ".join(
        part for part in (
            getattr(scene, "specific_target_label", ""),
            getattr(scene, "on_screen_text", ""),
            getattr(scene, "title", ""),
            getattr(scene, "purpose", ""),
            target_label,
        )
        if part
    ))
    if getattr(scene, "action_class", "") == "card_selection" and any(token in label for token in ("account", "login", "sign in")):
        return True
    if "level" in scene_text and any(token in label for token in ("login", "account", "sign in")):
        return True
    return False


def compact_label(value: str) -> str:
    cleaned = " ".join(value.split()).strip().rstrip(".")
    return cleaned[:72] or "the next step"


def concise_target(value: str) -> str:
    cleaned = compact_label(value)
    if len(cleaned) > 48 or len(cleaned.split()) > 7:
        return ""
    return cleaned


def neutral_voice_line(scene: EditPlanScene, target: str, after: str) -> str:
    if scene.scene_role == "result" and after:
        return f"Pause on {article_label(after)} so the state reads clearly."
    if scene.action_class == "card_selection":
        return f"Focus on {compact_label(target)} and let the next state settle."
    if scene.action_class == "auth_action":
        return f"Use {compact_label(target)} and let the sign-in flow resolve on screen."
    if after:
        return f"Focus on {compact_label(target)} as the screen settles into {article_label(after)}."
    return f"Focus on {compact_label(target)} as this step resolves."


def article_label(value: str) -> str:
    cleaned = compact_label(value)
    lowered = cleaned.lower()
    if lowered.startswith(("the ", "your ", "a ", "an ")):
        return cleaned
    return f"the {cleaned}"


def first_non_empty(*values: str) -> str:
    for value in values:
        cleaned = " ".join((value or "").split()).strip().rstrip(".")
        if cleaned:
            return cleaned
    return ""


def normalize_label(value: str) -> str:
    return " ".join(value.lower().split()).strip().rstrip(".")
