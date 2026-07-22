from __future__ import annotations

from app.models.projects import EditPlanScene


def prune_redundant_render_scenes(scenes: list[EditPlanScene]) -> list[EditPlanScene]:
    pruned: list[EditPlanScene] = []
    for scene in scenes:
        if pruned and redundant_followup(pruned[-1], scene):
            pruned[-1] = merge_redundant_scene_pair(pruned[-1], scene)
            continue
        pruned.append(scene)
    return pruned


def redundant_followup(left: EditPlanScene, right: EditPlanScene) -> bool:
    if right.start - left.end > 3.6:
        return False
    if left.action_class != right.action_class or left.scene_role != right.scene_role:
        return False
    if left.action_class not in {"card_selection", "auth_action"}:
        return False
    if normalized_scene_target(left) != normalized_scene_target(right):
        return False
    return continuation_scene(left, right)


def merge_redundant_scene_pair(left: EditPlanScene, right: EditPlanScene) -> EditPlanScene:
    merged_end = max(left.end, right.end)
    merged_duration = round(
        max(
            (left.render_duration_seconds or (left.end - left.start))
            + (right.render_duration_seconds or (right.end - right.start)) * 0.35,
            merged_end - left.start,
        ),
        2,
    )
    spoken_line = left.spoken_line if left.spoken_line and not generic_continuation_line(right.spoken_line) else right.spoken_line
    preferred = preferred_editorial_scene(left, right)
    return left.model_copy(
        update={
            "end": round(merged_end, 2),
            "render_duration_seconds": merged_duration,
            "action_timestamp": preferred.action_timestamp,
            "establish_end_timestamp": preferred.establish_end_timestamp,
            "focus_start_timestamp": preferred.focus_start_timestamp,
            "focus_end_timestamp": max(left.focus_end_timestamp or left.end, right.focus_end_timestamp or right.end),
            "settle_end_timestamp": max(left.settle_end_timestamp or left.end, right.settle_end_timestamp or right.end),
            "result_anchor_timestamp": max(left.result_anchor_timestamp or left.end, right.result_anchor_timestamp or right.end),
            "readable_hold_seconds": max(left.readable_hold_seconds, right.readable_hold_seconds),
            "spoken_line": spoken_line,
            "source_excerpt": left.source_excerpt or right.source_excerpt,
            "camera_mode": preferred.camera_mode,
            "decision_summary": preferred.decision_summary,
            "visual_summary": preferred.visual_summary,
            "on_screen_text": preferred.on_screen_text,
            "specific_target_label": preferred.specific_target_label,
            "layout_mode": preferred.layout_mode,
            "show_captions": preferred.show_captions,
            "transition_style": preferred.transition_style,
            "transition_duration_seconds": preferred.transition_duration_seconds,
            "captions": preferred.captions,
            "zooms": preferred.zooms,
            "highlights": preferred.highlights,
        }
    )


def normalized_scene_target(scene: EditPlanScene) -> str:
    source = " ".join((scene.specific_target_label or scene.on_screen_text or scene.title).lower().split()).strip()
    if source.startswith("the "):
        source = source[4:]
    if source.endswith(" course"):
        source = source[:-7].strip()
    return source


def continuation_scene(left: EditPlanScene, right: EditPlanScene) -> bool:
    if left.scene_number == right.scene_number:
        return True
    if generic_continuation_line(right.spoken_line):
        return True
    return same_editorial_copy(left, right)


def generic_continuation_line(line: str) -> bool:
    normalized = " ".join(line.lower().split())
    return normalized.startswith("that keeps the walkthrough moving")


def same_editorial_copy(left: EditPlanScene, right: EditPlanScene) -> bool:
    left_copy = normalized_copy_signature(left)
    right_copy = normalized_copy_signature(right)
    return bool(left_copy and right_copy and left_copy == right_copy)


def normalized_copy_signature(scene: EditPlanScene) -> str:
    parts = (
        scene.title,
        scene.on_screen_text,
        scene.spoken_line,
    )
    return " | ".join(" ".join(part.lower().split()) for part in parts if part)


def preferred_editorial_scene(left: EditPlanScene, right: EditPlanScene) -> EditPlanScene:
    left_score = editorial_signal_score(left)
    right_score = editorial_signal_score(right)
    if right_score > left_score:
        return right
    if right_score == left_score and right.result_anchor_timestamp and (left.result_anchor_timestamp or 0.0) < right.result_anchor_timestamp:
        return right
    return left


def editorial_signal_score(scene: EditPlanScene) -> int:
    return (
        len(scene.zooms) * 3
        + len(scene.highlights) * 3
        + len(scene.captions)
        + (1 if scene.camera_mode == "focus" else 0)
        + (1 if scene.focus_start_timestamp is not None else 0)
        + (1 if scene.focus_end_timestamp is not None else 0)
        + (1 if scene.result_anchor_timestamp is not None else 0)
    )
