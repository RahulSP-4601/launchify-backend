from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from app.models.projects import FocusBox, LaunchScriptScene, SceneRole, TranscriptSegment, VisualSceneAnalysisRecord

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
ACTION_KEYWORDS = (
    "click",
    "select",
    "open",
    "choose",
    "toggle",
    "save",
    "create",
    "edit",
    "invite",
    "publish",
    "filter",
    "search",
    "upload",
)
SPECIFIC_UI_KEYWORDS = (
    "button",
    "tab",
    "menu",
    "settings",
    "search",
    "filter",
    "workspace",
    "dashboard",
    "profile",
    "panel",
    "modal",
    "form",
)
FOCUS_REGION_HINTS = {
    "top-left": ("create", "new", "add", "compose"),
    "top-center": ("search", "find", "filter"),
    "top-right": ("settings", "profile", "account", "avatar"),
    "bottom-right": ("save", "publish", "continue", "confirm", "next"),
}


@dataclass(frozen=True)
class ScenePolicy:
    scene_confidence: float
    zoom_confidence: float
    highlight_confidence: float
    focus_region: str
    anchor_region: str
    highlight_style: str
    camera_mode: Literal["static", "focus"]
    decision_summary: str
    should_zoom: bool
    should_highlight: bool
    focus_box: FocusBox | None
    cursor_box: FocusBox | None
    click_target_box: FocusBox | None
    anchor_box: FocusBox | None
    target_label: str
    visual_summary: str
    scene_role: SceneRole
    action_class: str


@dataclass(frozen=True)
class PolicyEvidence:
    action_score: float
    alignment_score: float
    specificity_score: float
    duration_score: float
    label_score: float
    focus_region: str
    focus_confidence: float
    visual_confidence: float
    click_score: float
    motion_score: float
    frame_diff_score: float
    cursor_path_confidence: float
    ocr_match_score: float
    ocr_confidence: float
    focus_box: FocusBox | None
    cursor_box: FocusBox | None
    click_target_box: FocusBox | None
    anchor_box: FocusBox | None
    target_label: str
    visual_summary: str


def build_scene_policy(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    visual_analysis: VisualSceneAnalysisRecord | None,
    *,
    scene_role: SceneRole = "action",
    action_class: str = "generic_action",
) -> ScenePolicy:
    evidence = gather_evidence(scene, transcript, visual_analysis)
    scene_confidence, zoom_confidence, highlight_confidence = confidence_scores(evidence)
    should_zoom = decide_zoom(evidence, zoom_confidence, scene_role, action_class)
    should_highlight = decide_highlight(evidence, should_zoom, highlight_confidence, scene_role)
    return ScenePolicy(
        scene_confidence=round(scene_confidence, 2),
        zoom_confidence=round(zoom_confidence, 2),
        highlight_confidence=round(highlight_confidence, 2),
        focus_region=evidence.focus_region,
        anchor_region=evidence.focus_region,
        highlight_style=infer_highlight_style(scene_role, action_class, evidence),
        camera_mode="focus" if should_zoom else "static",
        focus_box=evidence.focus_box,
        cursor_box=evidence.cursor_box,
        click_target_box=evidence.click_target_box,
        anchor_box=evidence.anchor_box,
        target_label=evidence.target_label,
        visual_summary=evidence.visual_summary,
        decision_summary=build_decision_summary(
            should_zoom,
            should_highlight,
            scene_confidence,
            zoom_confidence,
            highlight_confidence,
            evidence,
        ),
        should_zoom=should_zoom,
        should_highlight=should_highlight,
        scene_role=scene_role,
        action_class=action_class,
    )


def confidence_scores(evidence: PolicyEvidence) -> tuple[float, float, float]:
    scene_confidence = weighted_average(
        (evidence.alignment_score, 0.24),
        (evidence.visual_confidence, 0.2),
        (evidence.action_score, 0.18),
        (evidence.specificity_score, 0.14),
        (evidence.duration_score, 0.12),
        (evidence.label_score, 0.08),
        (evidence.ocr_match_score, 0.04),
    )
    zoom_confidence = weighted_average(
        (evidence.visual_confidence, 0.24),
        (evidence.focus_confidence, 0.18),
        (evidence.click_score, 0.18),
        (evidence.cursor_path_confidence, 0.16),
        (evidence.frame_diff_score, 0.12),
        (evidence.motion_score, 0.12),
    )
    highlight_confidence = weighted_average(
        (evidence.visual_confidence, 0.22),
        (evidence.click_score, 0.2),
        (evidence.ocr_match_score, 0.18),
        (evidence.ocr_confidence, 0.12),
        (evidence.label_score, 0.16),
        (evidence.cursor_path_confidence, 0.06),
        (evidence.specificity_score, 0.06),
    )
    return scene_confidence, zoom_confidence, highlight_confidence


def gather_evidence(
    scene: LaunchScriptScene,
    transcript: list[TranscriptSegment],
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> PolicyEvidence:
    scene_text = joined_text(scene.purpose, scene.spoken_line, scene.on_screen_text, scene.source_excerpt)
    transcript_text = " ".join(segment.text for segment in transcript)
    focus_region, focus_confidence = infer_focus_region(scene, visual_analysis)
    return PolicyEvidence(
        action_score=keyword_density(scene_text, ACTION_KEYWORDS),
        alignment_score=overlap_score(scene_text, transcript_text),
        specificity_score=ui_specificity_score(scene),
        duration_score=duration_score(scene.estimated_duration_seconds),
        label_score=label_specificity(scene),
        focus_region=focus_region,
        focus_confidence=focus_confidence,
        visual_confidence=visual_score(visual_analysis),
        click_score=click_score(visual_analysis),
        motion_score=visual_analysis.motion_score if visual_analysis else 0.0,
        frame_diff_score=frame_diff_score(visual_analysis),
        cursor_path_confidence=visual_analysis.cursor_path_confidence if visual_analysis else 0.0,
        ocr_match_score=ocr_match_score(scene_text, visual_analysis),
        ocr_confidence=visual_analysis.ocr_confidence if visual_analysis else 0.0,
        focus_box=visual_analysis.primary_focus_box if visual_analysis else None,
        cursor_box=visual_analysis.cursor_box if visual_analysis else None,
        click_target_box=visual_analysis.click_target_box if visual_analysis else None,
        anchor_box=visual_analysis.anchor_box if visual_analysis else None,
        target_label=best_label(visual_analysis),
        visual_summary=scene_visual_summary(visual_analysis),
    )


def frame_diff_score(visual_analysis: VisualSceneAnalysisRecord | None) -> float:
    if not visual_analysis or not visual_analysis.frame_diff_available:
        return 0.0
    return visual_analysis.frame_diff_score


def scene_visual_summary(visual_analysis: VisualSceneAnalysisRecord | None) -> str:
    if not visual_analysis:
        return "No frame analysis available for this scene."
    if visual_analysis.frame_diff_available:
        return visual_analysis.summary
    return f"{visual_analysis.summary} Motion diff evidence was unavailable."


def keyword_density(text: str, keywords: tuple[str, ...]) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    matches = sum(1 for keyword in keywords if keyword in tokens)
    return min(1.0, matches / 3)


def overlap_score(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), 1)


def ui_specificity_score(scene: LaunchScriptScene) -> float:
    text = joined_text(scene.purpose, scene.on_screen_text, scene.source_excerpt)
    tokens = tokenize(text)
    keyword_hits = sum(1 for keyword in SPECIFIC_UI_KEYWORDS if keyword in tokens)
    dense_text_bonus = min(0.4, len(tokens) / 16)
    return min(1.0, keyword_hits / 3 + dense_text_bonus)


def duration_score(duration_seconds: float) -> float:
    if 2.0 <= duration_seconds <= 7.5:
        return 1.0
    if duration_seconds < 1.2 or duration_seconds > 10.0:
        return 0.3
    return 0.65


def label_specificity(scene: LaunchScriptScene) -> float:
    tokens = tokenize(scene.on_screen_text or scene.source_excerpt or scene.purpose)
    if not tokens:
        return 0.0
    return min(1.0, len(tokens) / 6)


def infer_focus_region(
    scene: LaunchScriptScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> tuple[str, float]:
    if visual_analysis and visual_analysis.primary_focus_box is not None:
        return box_region(visual_analysis.primary_focus_box), visual_analysis.confidence
    text = joined_text(scene.on_screen_text, scene.spoken_line, scene.purpose)
    tokens = tokenize(text)
    for region, hints in FOCUS_REGION_HINTS.items():
        if any(hint in tokens for hint in hints):
            return region, 0.82
    if tokens:
        return "center", 0.55
    return "center", 0.2


def decide_zoom(
    evidence: PolicyEvidence,
    zoom_confidence: float,
    scene_role: SceneRole,
    action_class: str,
) -> bool:
    if scene_role == "explanation":
        return False
    if scene_role == "result":
        return result_scene_zoom(evidence, zoom_confidence)
    if evidence.visual_confidence == 0:
        return (
            zoom_confidence >= 0.44
            and evidence.focus_confidence >= 0.5
            and (
                (evidence.action_score >= 0.22 and evidence.specificity_score >= 0.2)
                or (
                    evidence.alignment_score >= 0.24
                    and evidence.label_score >= 0.34
                    and evidence.specificity_score >= 0.18
                )
            )
        )
    if evidence.anchor_box is not None and focus_signal_is_trustworthy(evidence):
        return (
            zoom_confidence >= 0.57
            and evidence.visual_confidence >= 0.44
            and (
                evidence.cursor_path_confidence >= 0.34
                or evidence.click_score >= 0.52
                or evidence.ocr_match_score >= 0.42
            )
        )
    return (
        zoom_confidence >= 0.6
        and evidence.visual_confidence >= 0.48
        and evidence.focus_confidence >= 0.54
        and (evidence.frame_diff_score >= 0.22 or evidence.click_score >= 0.62)
        and evidence.cursor_path_confidence >= 0.34
        and focus_signal_is_trustworthy(evidence)
    )


def decide_highlight(
    evidence: PolicyEvidence,
    should_zoom: bool,
    highlight_confidence: float,
    scene_role: SceneRole,
) -> bool:
    if scene_role != "action":
        return False
    return (
        should_zoom
        and highlight_confidence >= 0.58
        and evidence.click_score >= 0.45
        and evidence.anchor_box is not None
        and target_signal_is_trustworthy(evidence)
    )


def infer_highlight_style(scene_role: SceneRole, action_class: str, evidence: PolicyEvidence) -> str:
    if scene_role != "action":
        return "ambient"
    if compact_target(evidence):
        return "soft-glow"
    if action_class == "card_selection":
        return "ambient-lift"
    return "spotlight"


def result_scene_zoom(evidence: PolicyEvidence, zoom_confidence: float) -> bool:
    return (
        zoom_confidence >= 0.62
        and evidence.visual_confidence >= 0.52
        and evidence.anchor_box is not None
        and box_area(evidence.anchor_box) <= 0.18
    )


def build_decision_summary(
    should_zoom: bool,
    should_highlight: bool,
    scene_confidence: float,
    zoom_confidence: float,
    highlight_confidence: float,
    evidence: PolicyEvidence,
) -> str:
    motion = "Focus move approved" if should_zoom else "Static framing preserved"
    marker = "highlight added" if should_highlight else "highlight skipped"
    return (
        f"{motion} because transcript alignment is {scene_confidence:.2f}, "
        f"visual confidence is {evidence.visual_confidence:.2f}, cursor path confidence is {evidence.cursor_path_confidence:.2f}, "
        f"zoom confidence is {zoom_confidence:.2f}, {marker}, and the strongest focus region is {evidence.focus_region}."
    )


def visual_score(visual_analysis: VisualSceneAnalysisRecord | None) -> float:
    return visual_analysis.confidence if visual_analysis else 0.0


def click_score(visual_analysis: VisualSceneAnalysisRecord | None) -> float:
    if visual_analysis is None or not visual_analysis.click_detected:
        return 0.0
    if visual_analysis.click_target_box is not None:
        return min(1.0, visual_analysis.confidence + 0.15)
    return visual_analysis.confidence * 0.8


def ocr_match_score(scene_text: str, visual_analysis: VisualSceneAnalysisRecord | None) -> float:
    if visual_analysis is None:
        return 0.0
    detected_text = " ".join(visual_analysis.visible_labels)
    if not detected_text.strip():
        return visual_analysis.ocr_match_score
    return max(visual_analysis.ocr_match_score, overlap_score(scene_text, detected_text))


def best_label(visual_analysis: VisualSceneAnalysisRecord | None) -> str:
    if visual_analysis is None or not visual_analysis.visible_labels:
        return ""
    ranked = sorted(
        (label.strip() for label in visual_analysis.visible_labels if label.strip()),
        key=lambda label: (len(label.split()) > 4, len(label), label.lower()),
    )
    return ranked[0] if ranked else ""


def focus_signal_is_trustworthy(evidence: PolicyEvidence) -> bool:
    if evidence.target_label:
        return evidence.ocr_confidence >= 0.32 or evidence.ocr_match_score >= 0.34
    return evidence.visual_confidence >= 0.5


def target_signal_is_trustworthy(evidence: PolicyEvidence) -> bool:
    return evidence.ocr_confidence >= 0.36 or evidence.click_score >= 0.62


def compact_target(evidence: PolicyEvidence) -> bool:
    if evidence.anchor_box is None:
        return False
    return box_area(evidence.anchor_box) <= 0.08


def box_area(box: FocusBox) -> float:
    return box.width * box.height


def box_region(box: FocusBox) -> str:
    center_x = box.x + box.width / 2
    center_y = box.y + box.height / 2
    if center_y < 0.33 and center_x < 0.33:
        return "top-left"
    if center_y < 0.33 and center_x > 0.66:
        return "top-right"
    if center_y < 0.33:
        return "top-center"
    if center_y > 0.66 and center_x > 0.66:
        return "bottom-right"
    return "center"


def weighted_average(*items: tuple[float, float]) -> float:
    weighted_sum = sum(value * weight for value, weight in items)
    total_weight = sum(weight for _, weight in items)
    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


def joined_text(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part.strip())


def tokenize(value: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(value.lower()) if len(token) > 1}
