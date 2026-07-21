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
from app.services.event_grounding import focus_box_for_event, normalize_event_timestamp, primary_event_for_window, region_for_box
from app.services.inferred_recording_support import normalize_label
from app.services.motion_director import build_motion_track, offset_for_box
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
) -> tuple[ScenePolicy, list[EditPlanCaption], list[EditPlanZoom], list[EditPlanHighlight]]:
    action_class = grounded_action_class(step, primary_event)
    scene_role = scene_role_from_action_class(action_class)
    policy = build_scene_policy(synthetic_scene, transcript_slice, visual_analysis, scene_role=scene_role, action_class=action_class)
    captions = build_caption_track(
        transcript_slice or [TranscriptSegment(start=start, end=end, text=step.narration)],
        start,
        end,
        project.template_config,
    )
    zooms, highlights = build_motion_track(synthetic_scene, start, end, policy, project.template_config)
    return policy, captions, *apply_grounded_focus(project, step, primary_event, zooms, highlights)


def build_grounded_overview(project: ProjectRecord, guide: GuideRecord) -> str:
    audience = project.target_audience or "the intended product audience"
    return f"Launchify grounded {len(guide.steps)} captured product actions for {audience}, turned them into synchronized steps, and prepared captions, zooms, and highlights from the event-backed guide."


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


def apply_grounded_focus(
    project: ProjectRecord,
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    zooms: list[EditPlanZoom],
    highlights: list[EditPlanHighlight],
) -> tuple[list[EditPlanZoom], list[EditPlanHighlight]]:
    event_focus_box = focus_box_for_event(project.recording_session, primary_event)
    if event_focus_box is None:
        return zooms, highlights
    focus_region = region_for_box(event_focus_box)
    return (
        grounded_zoom_track(step, primary_event, event_focus_box, focus_region, zooms),
        grounded_highlight_track(step, primary_event, event_focus_box, focus_region, highlights),
    )


def grounded_zoom_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    zooms: list[EditPlanZoom],
) -> list[EditPlanZoom]:
    hydrated = [hydrate_grounded_zoom(zoom, event_focus_box, focus_region) for zoom in zooms]
    if primary_event is not None:
        return segmented_grounded_zooms(step, primary_event, event_focus_box, focus_region)
    return hydrated or [seed_grounded_zoom(step, event_focus_box, focus_region)]


def grounded_highlight_track(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
    highlights: list[EditPlanHighlight],
) -> list[EditPlanHighlight]:
    hydrated = [hydrate_grounded_highlight(highlight, step, event_focus_box, focus_region) for highlight in highlights]
    if primary_event is not None:
        return [segmented_grounded_highlight(step, primary_event, event_focus_box, focus_region)]
    return hydrated or [seed_grounded_highlight(step, event_focus_box, focus_region)]


def segmented_grounded_zooms(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> list[EditPlanZoom]:
    focus_start, focus_peak_end, settle_end = grounded_focus_windows(step, primary_event)
    lead_end = max(min(focus_start, step.end), min(step.start + 0.34, focus_start))
    zooms: list[EditPlanZoom] = []
    if lead_end - step.start >= 0.35:
        zooms.append(build_zoom_segment(step.start, lead_end, 1.04, "grounded lead-in", 0.74, event_focus_box, focus_region, 0.35, 0.3, 0.08))
    zooms.append(build_zoom_segment(focus_start, focus_peak_end, 1.24, "grounded action focus", 0.9, event_focus_box, focus_region, 1.0, 0.72, 0.12))
    if settle_end - focus_peak_end >= 0.35:
        zooms.append(build_zoom_segment(focus_peak_end, settle_end, 1.12, "grounded settle hold", 0.82, event_focus_box, focus_region, 0.7, 0.58, 0.14))
    return zooms


def build_zoom_segment(
    start: float,
    end: float,
    scale: float,
    reason: str,
    confidence: float,
    focus_box: FocusBox,
    focus_region: str,
    offset_multiplier: float,
    hold_ratio: float,
    smoothing: float,
) -> EditPlanZoom:
    return EditPlanZoom(
        start=round(start, 2),
        end=round(end, 2),
        scale=scale,
        focus_region=focus_region,
        reason=reason,
        confidence=confidence,
        focus_box=focus_box,
        x_offset=offset_for_box(focus_box, focus_region, axis="x") * offset_multiplier,
        y_offset=offset_for_box(focus_box, focus_region, axis="y") * offset_multiplier,
        hold_ratio=hold_ratio,
        smoothing=smoothing,
    )


def segmented_grounded_highlight(
    step: GuideStepRecord,
    primary_event: SessionEventRecord | None,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start = max(step.start, event_time - 0.14)
    focus_peak_end = min(step.end, focus_start + 1.35)
    if focus_peak_end - focus_start < 0.8:
        focus_peak_end = min(step.end, focus_start + 0.8)
    label = step.specific_target_label or step.highlight_label or step.focus_label or step.title
    return EditPlanHighlight(
        start=round(focus_start, 2),
        end=round(focus_peak_end, 2),
        label=label,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.92,
        focus_box=event_focus_box,
        ui_label=label,
    )


def grounded_focus_windows(step: GuideStepRecord, primary_event: SessionEventRecord | None) -> tuple[float, float, float]:
    event_time = normalize_event_timestamp(primary_event.timestamp) if primary_event is not None else step.start
    focus_start, focus_peak_end, settle_end = action_result_window(step.start, step.end, event_time, step.narration)
    if focus_peak_end - focus_start < 0.7:
        focus_peak_end = min(step.end, focus_start + 0.7)
    return focus_start, focus_peak_end, settle_end


def hydrate_grounded_zoom(zoom: EditPlanZoom, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    resolved_focus_box = zoom.focus_box or event_focus_box
    return zoom.model_copy(update={
        "focus_box": resolved_focus_box,
        "focus_region": focus_region if zoom.focus_region == "center" else zoom.focus_region,
        "confidence": max(zoom.confidence, 0.82),
        "scale": max(zoom.scale, 1.2),
        "x_offset": offset_for_box(resolved_focus_box, focus_region, axis="x"),
        "y_offset": offset_for_box(resolved_focus_box, focus_region, axis="y"),
    })


def hydrate_grounded_highlight(
    highlight: EditPlanHighlight,
    step: GuideStepRecord,
    event_focus_box: FocusBox,
    focus_region: str,
) -> EditPlanHighlight:
    label = step.specific_target_label or step.highlight_label or step.focus_label or highlight.label
    return highlight.model_copy(update={
        "focus_box": highlight.focus_box or event_focus_box,
        "anchor_region": focus_region if highlight.anchor_region == "center" else highlight.anchor_region,
        "confidence": max(highlight.confidence, 0.84),
        "ui_label": label,
        "label": label,
    })


def seed_grounded_zoom(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanZoom:
    return EditPlanZoom(
        start=step.start,
        end=step.end,
        scale=1.22,
        focus_region=focus_region,
        reason="grounded session focus",
        confidence=0.86,
        focus_box=event_focus_box,
        x_offset=offset_for_box(event_focus_box, focus_region, axis="x"),
        y_offset=offset_for_box(event_focus_box, focus_region, axis="y"),
        hold_ratio=0.68,
        smoothing=0.14,
    )


def seed_grounded_highlight(step: GuideStepRecord, event_focus_box: FocusBox, focus_region: str) -> EditPlanHighlight:
    label = step.specific_target_label or step.highlight_label or step.focus_label or step.title
    return EditPlanHighlight(
        start=step.start,
        end=step.end,
        label=label,
        style="spotlight",
        anchor_region=focus_region,
        confidence=0.88,
        focus_box=event_focus_box,
        ui_label=label,
    )
