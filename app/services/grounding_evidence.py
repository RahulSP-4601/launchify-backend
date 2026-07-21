from __future__ import annotations

from app.models.projects import SessionEventRecord, VisualSceneAnalysisRecord
from app.services.canonical_consistency import branch_family, event_branch_family
from app.services.inferred_recording_support import normalize_label


def annotate_event_grounding(
    events: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    return [event.model_copy(update={"metadata": {**event.metadata, **grounding_metadata(event, analyses_by_scene)}}) for event in events]


def validate_event_grounding(
    events: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[SessionEventRecord]:
    canonical_index = canonical_event_index(events)
    validated: list[SessionEventRecord] = []
    for event in sorted(events, key=lambda item: item.timestamp):
        if should_drop_weak_event(event, validated, analyses_by_scene):
            continue
        if should_drop_shadow_event(event, validated, canonical_index):
            continue
        validated.append(event)
    return validated


def grounding_payload(events: list[SessionEventRecord]) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for event in events:
        payload.append(
            {
                "timestamp": event.timestamp,
                "scene_number": int(event.metadata.get("scene_number", "0") or 0),
                "label": event.target.label,
                "grounding_score": safe_float(event.metadata.get("grounding_score", "0")),
                "action_evidence": safe_float(event.metadata.get("grounding_action_evidence", "0")),
                "result_evidence": safe_float(event.metadata.get("grounding_result_evidence", "0")),
                "branch_evidence": safe_float(event.metadata.get("grounding_branch_evidence", "0")),
                "transcript_evidence": safe_float(event.metadata.get("grounding_transcript_evidence", "0")),
                "window_start": safe_float(event.metadata.get("grounding_window_start", "0")),
                "window_end": safe_float(event.metadata.get("grounding_window_end", "0")),
                "scene_start": safe_float(event.metadata.get("grounding_scene_start", "0")),
                "scene_end": safe_float(event.metadata.get("grounding_scene_end", "0")),
                "status": event.metadata.get("grounding_status", "unknown"),
            }
        )
    return payload


def grounding_metadata(
    event: SessionEventRecord,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> dict[str, str]:
    analysis = analyses_by_scene.get(scene_number(event))
    action_score = action_evidence(event)
    result_score = result_evidence(event, analysis)
    branch_score = branch_evidence(event)
    transcript_score = transcript_evidence(event)
    overall = round((action_score * 0.34) + (result_score * 0.3) + (branch_score * 0.18) + (transcript_score * 0.18), 3)
    window_start, window_end = grounding_window(event, analysis)
    scene_start, scene_end = scene_grounding_window(event, analysis, overall)
    status = "strong" if overall >= 0.72 else "supported" if overall >= 0.56 else "weak"
    return {
        "grounding_action_evidence": f"{action_score:.2f}",
        "grounding_result_evidence": f"{result_score:.2f}",
        "grounding_branch_evidence": f"{branch_score:.2f}",
        "grounding_transcript_evidence": f"{transcript_score:.2f}",
        "grounding_score": f"{overall:.2f}",
        "grounding_window_start": f"{window_start:.2f}",
        "grounding_window_end": f"{window_end:.2f}",
        "grounding_scene_start": f"{scene_start:.2f}",
        "grounding_scene_end": f"{scene_end:.2f}",
        "grounding_status": status,
    }


def should_drop_weak_event(
    event: SessionEventRecord,
    validated: list[SessionEventRecord],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> bool:
    score = safe_float(event.metadata.get("grounding_score", "0"))
    if score >= 0.56:
        return False
    if event.metadata.get("grounding_source") != "transcript_fallback":
        return False
    scene_id = scene_number(event)
    if any(scene_number(existing) == scene_id for existing in validated):
        return True
    analysis = analyses_by_scene.get(scene_id)
    if analysis is None:
        return True
    return result_evidence(event, analysis) < 0.44


def should_drop_shadow_event(
    event: SessionEventRecord,
    validated: list[SessionEventRecord],
    canonical_index: dict[str, list[SessionEventRecord]],
) -> bool:
    if event.metadata.get("canonical_label", "").strip():
        return False
    label = normalize_label(event.target.label or event.target.text)
    if not label:
        return False
    if absorbed_account_picker_event(event, validated, canonical_index, label):
        return True
    if label in {"log in with google", "login with google", "continue with google"}:
        return has_nearby_canonical(validated, canonical_index, "continue with google", event.timestamp, 8.0)
    if label in {"open course", "select a course"}:
        return has_nearby_canonical(validated, canonical_index, "select a course", event.timestamp, 12.0)
    return False


def has_nearby_canonical(
    events: list[SessionEventRecord],
    canonical_index: dict[str, list[SessionEventRecord]],
    canonical_label: str,
    timestamp: float,
    max_gap_seconds: float,
) -> bool:
    for event in [*events, *canonical_index.get(canonical_label, [])]:
        if abs(event.timestamp - timestamp) <= max_gap_seconds:
            return True
    return False


def canonical_event_index(events: list[SessionEventRecord]) -> dict[str, list[SessionEventRecord]]:
    indexed: dict[str, list[SessionEventRecord]] = {}
    for event in events:
        canonical = normalize_label(event.metadata.get("canonical_label", ""))
        if not canonical:
            continue
        indexed.setdefault(canonical, []).append(event)
    return indexed


def absorbed_account_picker_event(
    event: SessionEventRecord,
    validated: list[SessionEventRecord],
    canonical_index: dict[str, list[SessionEventRecord]],
    label: str,
) -> bool:
    screen_before = event.metadata.get("screen_before", "").strip()
    screen_after = event.metadata.get("screen_after", "").strip()
    if screen_before != "account_picker" and screen_after != "account_picker":
        return False
    if not is_generic_account_picker_label(label):
        return False
    if not has_nearby_canonical(validated, canonical_index, "continue with google", event.timestamp, 8.0):
        return False
    return has_nearby_canonical(validated, canonical_index, "select a course", event.timestamp, 12.0)


def is_generic_account_picker_label(label: str) -> bool:
    if label in {"account list item", "choose an account"}:
        return True
    if "account" in label and "item" in label:
        return True
    tokens = label.split()
    return len(tokens) <= 4 and any(token in label for token in ("account", "profile", "gmail", "google"))


def action_evidence(event: SessionEventRecord) -> float:
    base = max(safe_float(event.metadata.get("score", "0")), 0.18)
    label = normalize_label(event.target.label or event.target.text)
    target_bonus = 0.18 if label else 0.0
    pointer_bonus = 0.12 if event.x is not None and event.y is not None else 0.0
    inferred_penalty = -0.1 if event.metadata.get("grounding_source") == "transcript_fallback" else 0.0
    return bounded(base + target_bonus + pointer_bonus + inferred_penalty)


def result_evidence(
    event: SessionEventRecord,
    analysis: VisualSceneAnalysisRecord | None,
) -> float:
    screen_after = event.metadata.get("screen_after", "").strip()
    if not screen_after:
        return 0.52 if event.type == "click" else 0.42
    if analysis is None:
        return 0.34
    visible = " ".join(normalize_label(label) for label in analysis.visible_labels)
    structure_bonus = 0.0
    if screen_after == "account_picker" and "account" in visible:
        structure_bonus = 0.42
    elif screen_after == "course_catalog" and any(token in visible for token in ("course", "japanese", "catalog")):
        structure_bonus = 0.42
    elif screen_after == "difficulty_picker" and any(token in visible for token in ("pick your", "level", "before you start")):
        structure_bonus = 0.46
    elif screen_after in {"auth_provider", "result_state"}:
        structure_bonus = 0.3
    frame_bonus = min(max(analysis.confidence, 0.0), 1.0) * 0.24
    return bounded(0.24 + structure_bonus + frame_bonus)


def branch_evidence(event: SessionEventRecord) -> float:
    branch = event_branch_family(event)
    if branch == "generic":
        return 0.58
    transcript_branch = branch_family(event.metadata.get("transcript_excerpt", ""))
    raw_branch = branch_family(event.metadata.get("raw_target_label", ""), event.target.text, event.target.label)
    if transcript_branch not in {"generic", branch}:
        return 0.28
    if raw_branch not in {"generic", branch}:
        return 0.22
    return 0.88


def transcript_evidence(event: SessionEventRecord) -> float:
    excerpt = normalize_label(event.metadata.get("transcript_excerpt", ""))
    if not excerpt:
        return 0.42
    label = normalize_label(event.target.label or event.target.text)
    if label and any(token in excerpt for token in label.split()):
        return 0.84
    return 0.62


def bounded(value: float) -> float:
    return round(max(min(value, 1.0), 0.0), 3)


def grounding_window(
    event: SessionEventRecord,
    analysis: VisualSceneAnalysisRecord | None,
) -> tuple[float, float]:
    timestamp = max(float(event.timestamp), 0.0)
    if analysis is None:
        return round(max(timestamp - 0.8, 0.0), 2), round(timestamp + 0.8, 2)
    start = max(analysis.start, timestamp - inferred_half_window(event))
    end = min(analysis.end, timestamp + inferred_half_window(event))
    if end <= start:
        start = max(min(timestamp, analysis.end) - 0.6, analysis.start, 0.0)
        end = min(max(timestamp, analysis.start) + 0.6, analysis.end)
    return round(start, 2), round(end, 2)


def inferred_half_window(event: SessionEventRecord) -> float:
    screen_after = event.metadata.get("screen_after", "").strip()
    if screen_after in {"account_picker", "course_catalog", "difficulty_picker"}:
        return 2.0
    if event.type == "focus":
        return 1.8
    return 1.25


def scene_grounding_window(
    event: SessionEventRecord,
    analysis: VisualSceneAnalysisRecord | None,
    grounding_score: float,
) -> tuple[float, float]:
    if analysis is None:
        timestamp = max(float(event.timestamp), 0.0)
        return round(max(timestamp - 1.25, 0.0), 2), round(timestamp + 1.25, 2)
    start = analysis.start
    end = analysis.end
    if grounding_score >= 0.72:
        return round(start, 2), round(end, 2)
    if grounding_score >= 0.56:
        midpoint = max(float(event.timestamp), start)
        padded_start = max(start, midpoint - max((midpoint - start) * 0.8, 1.2))
        padded_end = min(end, midpoint + max((end - midpoint) * 0.8, 1.2))
        return round(padded_start, 2), round(padded_end, 2)
    return grounding_window(event, analysis)


def scene_number(event: SessionEventRecord) -> int:
    return int(event.metadata.get("scene_number", "0") or 0)


def safe_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
