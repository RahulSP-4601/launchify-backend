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
    if is_account_picker_scene(scene):
        return "screen-only"
    if scene.scene_role == "action" and scene.action_class in {"auth_action", "card_selection"}:
        return "split-right"
    if is_setup_scene(scene):
        return setup_layout_mode(scene, visual_analysis)
    if scene.scene_role == "result" and any(token in combined for token in ("select a course", "pick your", "choose a course", "dashboard")):
        return "screen-only"
    if scene.scene_role == "result" and any(token in combined for token in ("select a course", "dashboard", "course", "japanese", "level")):
        return "dashboard-wide"
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
    tracked = tracked_focus_box(
        visual_analysis,
        focus_start=beats.focus_start,
        focus_end=beats.focus_end,
        result_anchor=beats.result_anchor,
        fallback=existing,
    )
    if tracked is not None:
        return tracked
    if scene.scene_role == "action" and is_setup_scene(scene):
        return fallback_setup_focus_box()
    return None


def should_show_captions(scene: EditPlanScene, layout_mode: str) -> bool:
    if not scene.show_captions:
        return False
    if layout_mode == "split-right":
        return True
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
        return compact_caption_text(scene.spoken_line or scene.on_screen_text or scene.title)
    if scene.action_class == "card_selection":
        return compact_caption_text(scene.spoken_line or scene.on_screen_text or scene.title)
    if scene.scene_role == "result":
        return compact_caption_text(scene.purpose or scene.on_screen_text or scene.spoken_line)
    return compact_caption_text(scene.spoken_line or scene.purpose)


def polished_spoken_line(scene: EditPlanScene, layout_mode: str) -> str:
    if layout_mode == "screen-only":
        if is_account_picker_scene(scene):
            return "Pick the existing account to keep moving."
        if is_setup_scene(scene) and scene.on_screen_text:
            return compact_caption_text(f"{scene.on_screen_text} before you begin.") or scene.spoken_line
    return compact_caption_text(scene.spoken_line or scene.purpose) or scene.spoken_line


def calibrated_zooms(scene: EditPlanScene, layout_mode: str, beats: EditorialBeatPlan, focus_box: FocusBox | None) -> list[EditPlanZoom]:
    if layout_mode == "screen-only":
        return subtle_screen_zoom(scene, beats, focus_box, scene.zooms[:1])
    if not scene.zooms:
        return seeded_dynamic_zooms(scene, layout_mode, beats, focus_box)
    tuned: list[EditPlanZoom] = []
    action_peak_scale, settle_scale = zoom_profile(scene, layout_mode)
    for index, zoom in enumerate(scene.zooms[:2]):
        target_scale = action_peak_scale if index == 0 else settle_scale
        start = max(scene.start, beats.focus_start if zoom.start <= beats.focus_start else zoom.start)
        end = min(scene.end, max(zoom.end, beats.focus_end if index == 0 else beats.settle_end))
        tuned.append(
            zoom.model_copy(
                update={
                    "start": round(start, 2),
                    "end": round(end, 2),
                    "scale": softened_editorial_scale(zoom.scale, target_scale),
                    "smoothing": max(zoom.smoothing, 0.16 if scene.action_class == "card_selection" else 0.14),
                    "hold_ratio": max(zoom.hold_ratio, 0.76 if scene.action_class == "card_selection" else 0.72),
                    "focus_box": focus_box or zoom.focus_box,
                }
            )
        )
    return tuned


def softened_editorial_scale(current_scale: float, target_scale: float) -> float:
    if current_scale >= target_scale:
        return target_scale
    return round(min(target_scale, max(1.0, current_scale + 0.02)), 2)


def calibrated_highlights(scene: EditPlanScene, layout_mode: str, beats: EditorialBeatPlan, focus_box: FocusBox | None) -> list[EditPlanHighlight]:
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        return screen_only_highlights(scene, beats, focus_box)
    if scene.scene_role == "result":
        return []
    highlights = scene.highlights[:1]
    if not highlights and focus_box is not None:
        return [seeded_highlight(scene, beats, focus_box)]
    return [retimed_highlight(highlight, scene, beats, focus_box) for highlight in highlights]


def screen_only_highlights(scene: EditPlanScene, beats: EditorialBeatPlan, focus_box: FocusBox | None) -> list[EditPlanHighlight]:
    if scene.scene_role != "action" or not is_setup_scene(scene) or focus_box is None:
        return []
    return [build_highlight(scene, round(beats.focus_start, 2), round(min(beats.settle_end, beats.focus_start + 1.1), 2), 0.82, focus_box)]


def seeded_highlight(scene: EditPlanScene, beats: EditorialBeatPlan, focus_box: FocusBox) -> EditPlanHighlight:
    return build_highlight(
        scene,
        round(beats.focus_start, 2),
        round(min(beats.focus_end, beats.focus_start + highlight_duration_seconds(scene)), 2),
        0.84,
        focus_box,
    )


def retimed_highlight(
    highlight: EditPlanHighlight,
    scene: EditPlanScene,
    beats: EditorialBeatPlan,
    focus_box: FocusBox | None,
) -> EditPlanHighlight:
    start = round(max(highlight.start, beats.focus_start), 2)
    end = round(min(max(highlight.end, beats.focus_end), scene.end, start + highlight_duration_seconds(scene)), 2)
    return highlight.model_copy(update={"start": start, "end": end, "focus_box": focus_box or highlight.focus_box})


def build_highlight(
    scene: EditPlanScene,
    start: float,
    end: float,
    confidence: float,
    focus_box: FocusBox,
) -> EditPlanHighlight:
    return EditPlanHighlight(
        start=start,
        end=end,
        label=compact_caption_text(scene.on_screen_text or scene.title or scene.purpose),
        style="soft-glow",
        anchor_region="center",
        confidence=confidence,
        focus_box=focus_box,
        placement_preference="avoid-ui-cover",
        ui_label=scene.on_screen_text or scene.title,
    )


def prefer_static_camera(scene: EditPlanScene, layout_mode: str) -> bool:
    if layout_mode in SCREEN_ONLY_LAYOUTS:
        return not (scene.scene_role == "action" and is_setup_scene(scene))
    return scene.scene_role == "result" and not scene.highlights


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


def is_account_picker_scene(scene: EditPlanScene) -> bool:
    combined = normalized_text(scene.on_screen_text, scene.purpose)
    return any(marker in combined for marker in ("choose an account", "account picker", "account chooser"))


def is_setup_scene(scene: EditPlanScene) -> bool:
    combined = normalized_text(scene.title, scene.on_screen_text, scene.purpose)
    return scene.action_class in {"button_click", "focus"} and any(
        token in combined for token in ("level", "settings", "preferences", "plan", "workspace", "role", "template", "setup")
    )


def setup_layout_mode(
    scene: EditPlanScene,
    visual_analysis: VisualSceneAnalysisRecord | None,
) -> str:
    label_count = len(visual_analysis.visible_labels) if visual_analysis is not None else 0
    duration = max(scene.render_duration_seconds or (scene.end - scene.start), 0.8)
    if scene.scene_role == "result":
        return "screen-only"
    if label_count >= 4 or duration >= 3.0:
        return "screen-only"
    return "feature-center"


def highlight_duration_seconds(scene: EditPlanScene) -> float:
    if scene.action_class == "auth_action":
        return 1.45
    if scene.action_class == "card_selection":
        return 1.35
    return 1.15


def fallback_setup_focus_box() -> FocusBox:
    return FocusBox(x=0.24, y=0.2, width=0.52, height=0.32)


def zoom_profile(scene: EditPlanScene, layout_mode: str) -> tuple[float, float]:
    if layout_mode == "dashboard-wide":
        return 1.08, 1.05
    if scene.action_class == "auth_action":
        return 1.14, 1.08
    if scene.action_class == "card_selection":
        return 1.18, 1.1
    if is_setup_scene(scene):
        return 1.1, 1.06
    return 1.12, 1.07


def subtle_screen_zoom(
    scene: EditPlanScene,
    beats: EditorialBeatPlan,
    focus_box: FocusBox | None,
    existing: list[EditPlanZoom],
) -> list[EditPlanZoom]:
    if not (scene.scene_role == "action" and is_setup_scene(scene) and focus_box is not None):
        return []
    base = existing[0] if existing else EditPlanZoom(
        start=scene.start,
        end=scene.end,
        scale=1.0,
        focus_region="center",
        reason="editorial setup focus",
        confidence=0.78,
        focus_box=focus_box,
        x_offset=0.0,
        y_offset=0.0,
        hold_ratio=0.82,
        smoothing=0.18,
    )
    return [
        base.model_copy(
            update={
                "start": round(beats.focus_start, 2),
                "end": round(min(scene.end, max(beats.settle_end, beats.focus_start + 1.2)), 2),
                "scale": 1.07,
                "focus_box": focus_box,
                "hold_ratio": max(base.hold_ratio, 0.82),
                "smoothing": max(base.smoothing, 0.18),
            }
        )
    ]


def seeded_dynamic_zooms(
    scene: EditPlanScene,
    layout_mode: str,
    beats: EditorialBeatPlan,
    focus_box: FocusBox | None,
) -> list[EditPlanZoom]:
    if focus_box is None:
        return []
    action_peak_scale, settle_scale = zoom_profile(scene, layout_mode)
    return [
        EditPlanZoom(
            start=round(beats.focus_start, 2),
            end=round(beats.focus_end, 2),
            scale=action_peak_scale,
            focus_region="center",
            reason="editorial action focus",
            confidence=0.8,
            focus_box=focus_box,
            x_offset=0.0,
            y_offset=0.0,
            hold_ratio=0.76,
            smoothing=0.16,
        ),
        EditPlanZoom(
            start=round(beats.focus_end, 2),
            end=round(beats.settle_end, 2),
            scale=settle_scale,
            focus_region="center",
            reason="editorial settle hold",
            confidence=0.76,
            focus_box=focus_box,
            x_offset=0.0,
            y_offset=0.0,
            hold_ratio=0.8,
            smoothing=0.18,
        ),
    ]
