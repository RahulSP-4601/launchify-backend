from __future__ import annotations

from app.models.projects import (
    EditPlanCaption,
    EditPlanHighlight,
    EditPlanZoom,
    FocusBox,
    GuideRecord,
    GuideStepRecord,
    LaunchScriptScene,
    ManualOverrideRecord,
    ProjectRecord,
    SessionEventRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
)
from app.services.action_classifier import classify_action, event_action_class
from app.services.caption_designer import build_caption_track
from app.services.editorial_targeting import ActionEnvelope, build_action_envelope, resolve_editorial_target
from app.services.event_grounding import focus_box_for_event, normalize_event_timestamp, primary_event_for_window, region_for_box
from app.services.grounded_motion_support import apply_grounded_focus, focus_box_area, grounded_event_focus_box
from app.services.inferred_recording_support import normalize_label
from app.services.motion_director import build_motion_track
from app.services.scene_roles import scene_role_from_action_class
from app.services.selection_disambiguation import valid_specific_selection_candidate
from app.services.visual_policy import ScenePolicy, build_scene_policy
from app.services.walkthrough_windows import action_result_window


def grounded_primary_event(
    project: ProjectRecord,
    step: GuideStepRecord,
    start: float,
    end: float,
) -> SessionEventRecord | None:
    preferred = " ".join(part for part in (step.focus_label, step.title, step.instruction, step.narration) if part)
    session = project.recording_session
    if session is None or not session.events:
        return primary_event_for_window(session, start, end, preferred)
    candidates = [event for event in session.events if start <= normalize_event_timestamp(event.timestamp) <= end]
    if not candidates:
        return primary_event_for_window(session, start, end, preferred)
    ranked = sorted(
        candidates,
        key=lambda event: grounded_event_rank(event, step.action_class, normalize_label(step.focus_label or step.title)),
        reverse=True,
    )
    return ranked[0]


def grounded_scene_number(primary_event: SessionEventRecord | None, step: GuideStepRecord) -> int:
    return int(primary_event.metadata.get("scene_number", "0")) if primary_event is not None else step.step_index


def grounded_synthetic_scene(step: GuideStepRecord, start: float, end: float) -> LaunchScriptScene:
    return LaunchScriptScene(
        scene_number=step.step_index,
        purpose=step.instruction,
        spoken_line=step.narration,
        on_screen_text=step.on_screen_text,
        specific_target_label=step.specific_target_label,
        source_excerpt=step.source_excerpt or step.focus_label or step.title,
        estimated_duration_seconds=max(end - start, 0.8),
    )


def grounded_action_class(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> str:
    if primary_event is not None and primary_event.type == "focus":
        return classify_action(
            "focus",
            step.focus_label or step.on_screen_text or step.title,
            step.narration,
            step.source_excerpt,
        )
    if primary_event is not None:
        return event_action_class(primary_event)
    return step.action_class or classify_action("click", step.focus_label or step.title, step.narration, step.source_excerpt)


def enrich_specific_target_from_visuals(
    step: GuideStepRecord,
    visual_analysis: VisualSceneAnalysisRecord | None,
    primary_event: SessionEventRecord | None,
) -> GuideStepRecord:
    if step.specific_target_label.strip() or grounded_action_class(step, primary_event) != "card_selection":
        return step
    target = inferred_specific_selection_target(step, visual_analysis, primary_event)
    if not target:
        return step
    return step.model_copy(update={"specific_target_label": target, "on_screen_text": target, "highlight_label": target})


def grounded_motion_assets(
    project: ProjectRecord,
    step: GuideStepRecord,
    synthetic_scene: LaunchScriptScene,
    transcript_slice: list[TranscriptSegment],
    start: float,
    end: float,
    visual_analysis: VisualSceneAnalysisRecord | None,
    primary_event: SessionEventRecord | None,
) -> tuple[ScenePolicy, list[EditPlanCaption], list[EditPlanZoom], list[EditPlanHighlight], ActionEnvelope]:
    action_class = grounded_action_class(step, primary_event)
    scene_role = scene_role_from_action_class(action_class)
    target = resolve_editorial_target(synthetic_scene, transcript_slice, visual_analysis)
    envelope = build_action_envelope(synthetic_scene, transcript_slice, visual_analysis, target, start_time=start, end_time=end)
    policy = build_scene_policy(
        synthetic_scene,
        transcript_slice,
        visual_analysis,
        scene_role=scene_role,
        action_class=action_class,
        editorial_target=target,
        action_envelope=envelope,
    )
    policy = grounded_policy(policy, project, step, primary_event, scene_role)
    captions = build_caption_track(
        transcript_slice or [TranscriptSegment(start=start, end=end, text=step.narration)],
        start,
        end,
        project.template_config,
    )
    zooms, highlights = build_motion_track(synthetic_scene, start, end, policy, project.template_config)
    return policy, captions, *apply_grounded_focus(project, step, primary_event, zooms, highlights), envelope


def grounded_policy(
    policy: ScenePolicy,
    project: ProjectRecord,
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    scene_role: str,
) -> ScenePolicy:
    event_focus_box = grounded_event_focus_box(project, primary_event)
    if event_focus_box is None:
        return policy
    preferred_box = grounded_focus_anchor(policy, event_focus_box)
    focus_region = region_for_box(preferred_box)
    target_label = step.highlight_label or step.focus_label or step.on_screen_text or policy.target_label
    return ScenePolicy(
        scene_confidence=max(policy.scene_confidence, 0.82),
        zoom_confidence=max(policy.zoom_confidence, 0.82),
        highlight_confidence=max(policy.highlight_confidence, 0.84),
        focus_region=focus_region,
        anchor_region=focus_region,
        highlight_style=policy.highlight_style,
        camera_mode="focus",
        decision_summary=policy.decision_summary,
        should_zoom=True,
        should_highlight=scene_role == "action",
        focus_box=preferred_box,
        cursor_box=policy.cursor_box,
        click_target_box=preferred_box,
        anchor_box=preferred_box,
        target_label=target_label,
        visual_summary=policy.visual_summary,
        scene_role=policy.scene_role,
        action_class=policy.action_class,
    )


def grounded_focus_anchor(policy: ScenePolicy, event_focus_box: FocusBox) -> FocusBox:
    candidates = [
        box
        for box in (policy.click_target_box, policy.anchor_box, policy.focus_box, event_focus_box)
        if box is not None
    ]
    if not candidates:
        return event_focus_box
    compact = min(candidates, key=focus_box_area)
    return compact if focus_box_area(compact) <= focus_box_area(event_focus_box) else event_focus_box


def build_grounded_overview(project: ProjectRecord, guide: GuideRecord) -> str:
    return f"Launchify grounded {len(guide.steps)} captured product actions for {project.project_name}, turned them into synchronized steps, and prepared captions, zooms, and highlights from the event-backed guide."


def grounded_decision_summary(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> str:
    if primary_event is None:
        return f"Grounded from synthesized step timing around {step.focus_label or step.focus_selector or 'the active element'}."
    label = primary_event.target.label or primary_event.target.text or primary_event.target.selector
    return f"Grounded from captured {primary_event.type} event near {label or 'the active element'} at {normalize_event_timestamp(primary_event.timestamp):.2f}s with event-led camera timing."


def grounded_visual_summary(step: GuideStepRecord, primary_event: SessionEventRecord | None, fallback: str) -> str:
    if primary_event is None:
        return fallback or f"Focus attention on {step.focus_label or step.focus_selector or 'the active control'}."
    label = primary_event.target.label or primary_event.target.text or primary_event.target.selector
    return f"Spotlight the real UI action around {label or 'the active control'} and keep surrounding context subdued."


def normalized_overrides(manual_overrides: ManualOverrideRecord | None) -> ManualOverrideRecord | None:
    return manual_overrides


def grounded_event_rank(
    event: SessionEventRecord,
    expected_action: str,
    normalized_focus: str,
) -> tuple[int, int, int, float]:
    action = event_action_class(event)
    label = normalize_label(event.target.label or event.target.text or event.metadata.get("canonical_label", ""))
    return (
        1 if action == expected_action else 0,
        1 if action_family(action) == action_family(expected_action) else 0,
        1 if normalized_focus and (label == normalized_focus or normalized_focus in label or label in normalized_focus) else 0,
        float(event.metadata.get("score", "0") or "0"),
    )


def action_family(action_class: str) -> str:
    if action_class in {"button_click", "focus", "result_state"}:
        return "setup"
    if action_class == "auth_action":
        return "auth"
    if action_class == "card_selection":
        return "selection"
    return action_class or "generic"


def inferred_specific_selection_target(
    step: GuideStepRecord,
    visual_analysis: VisualSceneAnalysisRecord | None,
    primary_event: SessionEventRecord | None,
) -> str:
    if visual_analysis is None or not visual_analysis.frames:
        return ""
    focus_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    candidate_frames = [frame for frame in visual_analysis.frames if abs(frame.timestamp - focus_time) <= 0.45] or visual_analysis.frames
    context_tokens = selection_context_tokens(step)
    ranked: list[tuple[float, str]] = []
    for frame in candidate_frames:
        anchor = frame.click_target_box or frame.cursor_box
        for element in frame.ui_elements:
            label = (element.label or "").strip()
            if not valid_specific_selection_candidate(label, element.role):
                continue
            score = specific_selection_score(label, element.confidence, element.role, context_tokens, anchor, element.box)
            if score >= 0.76:
                ranked.append((score, label))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_label = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    return "" if best_score - runner_up < 0.08 and runner_up > 0.0 else best_label


def selection_context_tokens(step: GuideStepRecord) -> set[str]:
    raw = " ".join(part for part in (step.source_excerpt, step.narration, step.instruction) if part)
    return normalized_tokens(raw)


def specific_selection_score(
    label: str,
    confidence: float,
    role: str,
    context_tokens: set[str],
    anchor: FocusBox | None,
    candidate_box: FocusBox,
) -> float:
    score = confidence * 0.46
    if role in {"button", "card"}:
        score += 0.14
    overlap = token_overlap_score(context_tokens, normalized_tokens(label))
    if overlap:
        score += min(overlap * 0.28, 0.6)
    if anchor is not None:
        score += max(0.0, 0.2 - focus_box_distance(anchor, candidate_box)) * 1.1
    return score


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
    return len({root for root in label_roots if root and root in context_roots}) * 0.8


def token_root(token: str) -> str:
    cleaned = token.lower().strip()
    if len(cleaned) <= 4:
        return cleaned
    for suffix in ("ese", "ish", "ian", "ing", "ers", "ies", "s"):
        if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 4:
            return cleaned[: -len(suffix)]
    return cleaned[:5]
