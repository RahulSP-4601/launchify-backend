from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox
from app.models.projects import EditPlanCaption, EditPlanHighlight, EditPlanRecord, EditPlanScene, EditPlanZoom, VisualSceneAnalysisRecord
from app.services.focus_tracking import tracked_focus_box

SCREEN_ONLY_LAYOUTS = {"screen-only", "dashboard-wide"}


@dataclass(frozen=True)
class EditorialBeatPlan:
    establish_end: float
    focus_start: float
    focus_end: float
    settle_end: float
    result_anchor: float | None


def apply_editorial_direction(
    edit_plan: EditPlanRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None = None,
) -> EditPlanRecord:
    analyses_by_scene = {analysis.scene_number: analysis for analysis in visual_analyses or []}
    scenes = [direct_scene(scene, analyses_by_scene.get(scene.scene_number)) for scene in edit_plan.scenes]
    return edit_plan.model_copy(update={"scenes": scenes})


def direct_scene(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> EditPlanScene:
    layout_mode = infer_layout_mode(scene, visual_analysis)
    beats = beat_plan(scene, visual_analysis, layout_mode)
    readable_hold = readable_hold_seconds(scene, visual_analysis, layout_mode)
    focus_box = stable_focus_box(scene, visual_analysis, beats)
    show_captions = should_show_captions(scene, layout_mode)
    captions = polished_captions(scene, show_captions, layout_mode)
    zooms = calibrated_zooms(scene, layout_mode, beats, focus_box)
    highlights = calibrated_highlights(scene, layout_mode, beats, focus_box)
    camera_mode = "static" if prefer_static_camera(scene, layout_mode) else scene.camera_mode
    return scene.model_copy(
        update={
            "layout_mode": layout_mode,
            "spoken_line": polished_spoken_line(scene, layout_mode),
            "result_anchor_timestamp": beats.result_anchor,
            "readable_hold_seconds": readable_hold,
            "establish_end_timestamp": beats.establish_end,
            "focus_start_timestamp": beats.focus_start,
            "focus_end_timestamp": beats.focus_end,
            "settle_end_timestamp": beats.settle_end,
            "show_captions": show_captions,
            "captions": captions,
            "zooms": zooms,
            "highlights": highlights,
            "camera_mode": camera_mode,
            "decision_summary": editorial_decision_summary(scene, layout_mode, beats),
            "visual_summary": editorial_visual_summary(scene, visual_analysis),
        }
    )


def infer_layout_mode(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> str:
    combined = normalized_text(
        scene.on_screen_text,
        scene.source_excerpt,
        scene.purpose,
        " ".join(visual_analysis.visible_labels) if visual_analysis is not None else "",
    )
    if "choose an account" in combined or ("account" in combined and scene.action_class == "auth_action"):
        return "screen-only"
    if scene.scene_role == "result" and any(token in combined for token in ("select a course", "pick your", "choose a course", "dashboard")):
        return "screen-only"
    if scene.scene_role == "result" and any(token in combined for token in ("select a course", "dashboard", "course", "japanese", "level")):
        return "dashboard-wide"
    if scene.action_class in {"auth_action", "card_selection"}:
        return "split-right"
    if scene.scene_role == "result":
        return "dashboard-wide"
    return "feature-center"


def stable_result_anchor(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> float | None:
    if scene.action_timestamp is None:
        return None
    if visual_analysis is None or not visual_analysis.frames:
        return round(min(scene.end, scene.action_timestamp + 0.55), 2)
    candidates = [
        frame
        for frame in visual_analysis.frames
        if frame.timestamp >= scene.action_timestamp + 0.12 and frame.timestamp <= scene.end
    ]
    if not candidates:
        return round(min(scene.end, scene.action_timestamp + 0.55), 2)
    best = max(candidates, key=stability_score)
    return round(min(max(best.timestamp, scene.start), scene.end), 2)


def beat_plan(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
    layout_mode: str,
) -> EditorialBeatPlan:
    action_time = scene.action_timestamp or scene.start
    result_anchor = stable_result_anchor(scene, visual_analysis)
    establish_end = round(min(max(scene.start + 0.42, action_time - 0.28), scene.end), 2)
    focus_start = round(min(max(scene.start, action_time - 0.18), scene.end), 2)
    focus_end = round(min(scene.end, max((result_anchor or action_time) + 0.26, focus_start + 0.64)), 2)
    settle_end = round(min(scene.end, max(focus_end + 0.58, result_anchor or focus_end)), 2)
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        establish_end = scene.start
        focus_start = scene.start
        focus_end = round(min(scene.end, max((result_anchor or action_time) + 0.1, scene.start + 0.9)), 2)
        settle_end = round(min(scene.end, max(focus_end + 0.78, scene.start + 1.35)), 2)
    return EditorialBeatPlan(establish_end=establish_end, focus_start=focus_start, focus_end=focus_end, settle_end=settle_end, result_anchor=result_anchor)


def stability_score(frame: object) -> float:
    diff_score = getattr(frame, "diff_score", 0.0)
    importance = getattr(frame, "importance_score", 0.0)
    ocr_confidence = getattr(frame, "ocr_confidence", 0.0)
    click_confidence = getattr(frame, "click_confidence", 0.0)
    click_target_box = getattr(frame, "click_target_box", None)
    settled_bonus = 0.08 if click_target_box is None else 0.0
    return ((1.0 - diff_score) * 0.42) + (importance * 0.28) + (ocr_confidence * 0.18) + ((1.0 - click_confidence) * 0.04) + settled_bonus


def readable_hold_seconds(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
    layout_mode: str,
) -> float:
    label_count = len(visual_analysis.visible_labels) if visual_analysis is not None else 0
    density = min(label_count / 12.0, 1.0)
    base = 1.0 + density * 0.65
    if scene.scene_role == "result":
        base += 0.35
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        base += 0.25
    if scene.action_class in {"auth_action", "navigation", "tab_switch", "card_selection"}:
        base += 0.2
    return round(min(base, 2.2), 2)


def stable_focus_box(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
    beats: EditorialBeatPlan,
) -> FocusBox | None:
    existing = primary_scene_focus_box(scene)
    return tracked_focus_box(
        visual_analysis,
        focus_start=beats.focus_start,
        focus_end=beats.focus_end,
        result_anchor=beats.result_anchor,
        fallback=existing,
    )


def should_show_captions(scene: EditPlanScene, layout_mode: str) -> bool:
    if not scene.show_captions:
        return False
    if layout_mode == "screen-only":
        return True
    if layout_mode == "feature-center":
        return True
    return False


def polished_captions(scene: EditPlanScene, show_captions: bool, layout_mode: str) -> list[EditPlanCaption]:
    if not show_captions:
        return []
    text = premium_caption_text(scene, layout_mode)
    if not text:
        return []
    if scene.captions:
        selected = scene.captions[0]
        return [selected.model_copy(update={"text": text, "variant": "minimal"})]
    return [EditPlanCaption(start=scene.start, end=scene.end, text=text, emphasis_words=[], variant="minimal")]


def compact_caption_text(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    words = cleaned.split()
    compact = " ".join(words[:12]).strip()
    if len(words) > 12 and not compact.endswith(("...", ".", "!", "?")):
        compact = f"{compact}..."
    return compact[:84]


def premium_caption_text(scene: EditPlanScene, layout_mode: str) -> str:
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        return ""
    if scene.action_class == "auth_action":
        return "Start with one clean login, then continue into onboarding."
    if scene.action_class == "card_selection":
        return "Choose the course card that starts the guided path."
    if scene.scene_role == "result":
        return compact_caption_text(scene.purpose or scene.on_screen_text or scene.spoken_line)
    return compact_caption_text(scene.spoken_line or scene.purpose)


def polished_spoken_line(scene: EditPlanScene, layout_mode: str) -> str:
    if layout_mode == "screen-only":
        if "account" in normalized_text(scene.on_screen_text, scene.source_excerpt, scene.purpose):
            return "Pick the existing account to keep moving."
    return compact_caption_text(scene.spoken_line or scene.purpose) or scene.spoken_line


def calibrated_zooms(scene: EditPlanScene, layout_mode: str, beats: EditorialBeatPlan, focus_box: FocusBox | None) -> list[EditPlanZoom]:
    if not scene.zooms:
        return []
    if layout_mode == "screen-only":
        return []
    tuned: list[EditPlanZoom] = []
    for zoom in scene.zooms[:2]:
        scale = min(zoom.scale, 1.1 if layout_mode == "dashboard-wide" else 1.16)
        start = max(scene.start, beats.focus_start if zoom.start <= beats.focus_start else zoom.start)
        end = min(scene.end, max(zoom.end, beats.focus_end))
        tuned.append(
            zoom.model_copy(
                update={
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "scale": scale,
                    "smoothing": max(zoom.smoothing, 0.14),
                    "hold_ratio": max(zoom.hold_ratio, 0.72),
                    "focus_box": focus_box or zoom.focus_box,
                }
            )
        )
    return tuned


def calibrated_highlights(scene: EditPlanScene, layout_mode: str, beats: EditorialBeatPlan, focus_box: FocusBox | None) -> list[EditPlanHighlight]:
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        return []
    if scene.scene_role == "result":
        return []
    highlights = scene.highlights[:1]
    if not highlights and focus_box is not None:
        return [
            EditPlanHighlight(
                start=round(beats.focus_start, 2),
                end=round(beats.focus_end, 2),
                label=compact_caption_text(scene.on_screen_text or scene.title or scene.purpose),
                style="soft-glow",
                anchor_region="center",
                confidence=0.84,
                focus_box=focus_box,
                placement_preference="avoid-ui-cover",
                ui_label=scene.on_screen_text or scene.title,
            )
        ]
    return [highlight.model_copy(update={"start": round(max(highlight.start, beats.focus_start), 2), "end": round(min(max(highlight.end, beats.focus_end), scene.end), 2), "focus_box": focus_box or highlight.focus_box}) for highlight in highlights]


def prefer_static_camera(scene: EditPlanScene, layout_mode: str) -> bool:
    return layout_mode in SCREEN_ONLY_LAYOUTS or (scene.scene_role == "result" and not scene.highlights)


def editorial_decision_summary(
    scene: EditPlanScene,
    layout_mode: str,
    beats: EditorialBeatPlan,
) -> str:
    anchor_text = f" result_anchor={beats.result_anchor:.2f}s" if beats.result_anchor is not None else ""
    return f"{scene.decision_summary} Editorial layout={layout_mode}; beats={beats.focus_start:.2f}-{beats.focus_end:.2f}/{beats.settle_end:.2f}.{anchor_text}".strip()


def editorial_visual_summary(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> str:
    if visual_analysis is None:
        return f"{scene.visual_summary} Editorial planning used grounded step timing."
    return f"{scene.visual_summary} Editorial planning emphasized a stable result frame and reduced overlay noise."


def normalized_text(*parts: str) -> str:
    return " ".join(" ".join(part.lower().split()) for part in parts if part).strip()


def primary_scene_focus_box(scene: EditPlanScene) -> FocusBox | None:
    if scene.highlights and scene.highlights[0].focus_box is not None:
        return scene.highlights[0].focus_box
    if scene.zooms and scene.zooms[0].focus_box is not None:
        return scene.zooms[0].focus_box
    return None
