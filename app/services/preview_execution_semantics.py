from __future__ import annotations

from app.models.projects import EditPlanRecord, EditPlanScene


def repair_execution_edit_plan(edit_plan: EditPlanRecord) -> EditPlanRecord:
    ordered = sorted(edit_plan.scenes, key=lambda scene: (scene.start, scene.scene_number))
    repaired: list[EditPlanScene] = []
    previous: EditPlanScene | None = None
    for scene in ordered:
        updated = repaired_scene(scene, previous)
        repaired.append(updated)
        previous = updated
    total_duration = round(sum(scene.render_duration_seconds or (scene.end - scene.start) for scene in repaired), 2)
    return edit_plan.model_copy(
        update={
            "scenes": repaired,
            "total_duration_seconds": total_duration,
            "render_spec": edit_plan.render_spec.model_copy(update={"total_duration_seconds": total_duration}),
        }
    )


def semantic_consistency_score(scenes: list[EditPlanScene]) -> float:
    if not scenes:
        return 1.0
    ordered = sorted(scenes, key=lambda scene: (scene.start, scene.scene_number))
    scores = [scene_consistency_score(scene) for scene in ordered]
    scores.extend(adjacent_scene_score(left, right) for left, right in zip(ordered, ordered[1:]))
    return round(sum(scores) / max(len(scores), 1), 3)


def repaired_scene(scene: EditPlanScene, previous: EditPlanScene | None) -> EditPlanScene:
    target = resolved_target_label(scene)
    before = repaired_before_label(scene, previous)
    after = repaired_after_label(scene, target, before)
    final_destination = repaired_destination_label(scene, after)
    confidence = repaired_transition_confidence(scene, before, after, final_destination, previous)
    return scene.model_copy(
        update={
            "specific_target_label": specific_target_label(scene, target),
            "action_target_label": target,
            "before_state_label": before,
            "after_state_label": after,
            "final_destination_label": final_destination,
            "transition_confidence": confidence,
        }
    )


def scene_consistency_score(scene: EditPlanScene) -> float:
    score = 0.62
    target = normalized(scene.action_target_label or scene.specific_target_label)
    after = normalized(scene.after_state_label or scene.final_destination_label)
    before = normalized(scene.before_state_label)
    if target:
        score += 0.12
    if after:
        score += 0.1
    if scene.response_state_kind == "response":
        score += 0.08
    if before and after and before != after:
        score += 0.08
    if scene.action_class == "card_selection" and any(token in after for token in ("login", "account", "sign in")):
        score -= 0.32
    if scene.action_class == "auth_action" and any(token in after for token in ("german course", "japanese level")):
        score -= 0.2
    if scene.action_class == "button_click" and scene.transition_confidence < 0.42:
        score -= 0.12
    return max(0.0, min(round(score, 3), 1.0))


def adjacent_scene_score(left: EditPlanScene, right: EditPlanScene) -> float:
    score = 0.68
    left_destination = normalized(left.final_destination_label or left.after_state_label)
    right_before = normalized(right.before_state_label)
    right_after = normalized(right.after_state_label or right.final_destination_label)
    if left_destination and right_before and left_destination == right_before:
        score += 0.18
    elif left_destination and not right_before:
        score += 0.06
    elif left_destination and right_before and left_destination != right_before:
        score -= 0.2
    if left_destination and right_after and left_destination == right_after and right.transition_confidence < 0.55:
        score -= 0.12
    if normalized(left.spoken_line) == normalized(right.spoken_line) and left.spoken_line.strip():
        score -= 0.14
    return max(0.0, min(round(score, 3), 1.0))


def repaired_before_label(scene: EditPlanScene, previous: EditPlanScene | None) -> str:
    before = cleaned(scene.before_state_label)
    previous_destination = cleaned(previous.final_destination_label if previous is not None else "") or cleaned(previous.after_state_label if previous is not None else "")
    if not before and previous_destination:
        return previous_destination
    if previous_destination and before and normalized(previous_destination) != normalized(before) and scene.transition_confidence < 0.72:
        return previous_destination
    return before


def repaired_after_label(scene: EditPlanScene, target: str, before: str) -> str:
    after = cleaned(scene.after_state_label)
    destination = cleaned(scene.final_destination_label)
    candidate = destination or after
    if destination_conflicts(scene, candidate, target):
        candidate = ""
    if normalized(candidate) == normalized(before) and scene.transition_confidence < 0.55:
        return ""
    return candidate or after


def repaired_destination_label(scene: EditPlanScene, after: str) -> str:
    destination = cleaned(scene.final_destination_label)
    if destination and not destination_conflicts(scene, destination, resolved_target_label(scene)):
        return destination
    if after and not destination_conflicts(scene, after, resolved_target_label(scene)):
        return after
    if scene.scene_role == "result":
        return cleaned(scene.on_screen_text) or cleaned(scene.title)
    return ""


def repaired_transition_confidence(
    scene: EditPlanScene,
    before: str,
    after: str,
    final_destination: str,
    previous: EditPlanScene | None,
) -> float:
    confidence = scene.transition_confidence
    if before and after and normalized(before) != normalized(after):
        confidence = max(confidence, 0.58)
    if final_destination:
        confidence = max(confidence, 0.62)
    previous_destination = cleaned(previous.final_destination_label if previous is not None else "")
    if previous_destination and before and normalized(previous_destination) == normalized(before):
        confidence = max(confidence, 0.64)
    if scene.action_class == "button_click" and not final_destination:
        confidence = min(confidence, 0.48)
    return round(max(0.0, min(confidence, 1.0)), 3)


def resolved_target_label(scene: EditPlanScene) -> str:
    candidates = [
        cleaned(scene.action_target_label),
        cleaned(scene.specific_target_label),
        concise_screen_label(scene.on_screen_text),
        concise_screen_label(scene.title),
        concise_screen_label(scene.source_excerpt),
    ]
    if scene.action_class == "card_selection":
        for candidate in candidates[:3]:
            if candidate:
                return candidate
    return next((candidate for candidate in candidates if candidate), "")


def specific_target_label(scene: EditPlanScene, target: str) -> str:
    existing = cleaned(scene.specific_target_label)
    if existing:
        return existing
    return target if scene.action_class in {"card_selection", "auth_action"} else existing


def destination_conflicts(scene: EditPlanScene, label: str, target: str) -> bool:
    normalized_label = normalized(label)
    normalized_target = normalized(target)
    scene_text = normalized(" ".join(part for part in (scene.title, scene.on_screen_text, scene.purpose, target) if part))
    if not normalized_label:
        return False
    if scene.action_class == "card_selection" and any(token in normalized_label for token in ("account", "login", "sign in")):
        return True
    if "level" in scene_text and any(token in normalized_label for token in ("account", "login", "sign in")):
        return True
    if normalized_target and normalized_target != normalized_label and normalized_target in normalized_label and len(normalized_label.split()) > 7:
        return True
    return False


def concise_screen_label(value: str) -> str:
    cleaned_value = cleaned(value)
    if len(cleaned_value) > 48 or len(cleaned_value.split()) > 7:
        return ""
    return cleaned_value


def cleaned(value: str) -> str:
    return " ".join((value or "").split()).strip().rstrip(".")


def normalized(value: str) -> str:
    return cleaned(value).lower()
