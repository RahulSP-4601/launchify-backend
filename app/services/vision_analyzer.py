from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import FocusBox, FrameSignalRecord, LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.frame_signal_analyzer import FrameDiffResult, frame_diff_scores
from app.services.ocr_pipeline import OcrFrameResult
from app.services.script_writer import describe_transport_error, openai_headers
from app.services.video_frames import ExtractedFrame

logger = logging.getLogger(__name__)


def analyze_scene_frames(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    video_path: Path,
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> VisualSceneAnalysisRecord:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OpenAI is not configured yet. Add OPENAI_API_KEY to enable visual analysis.")
    timestamps = [frame.timestamp for frame in extracted_frames]
    diff_result = frame_diff_scores(video_path, timestamps)
    payload = request_openai_vision(
        scene,
        scene_range,
        extracted_frames,
        diff_result,
        ocr_labels_by_timestamp,
    )
    try:
        return finalize_scene_analysis(
            VisualSceneAnalysisRecord.model_validate(normalize_visual_payload(payload)),
            diff_result,
            ocr_labels_by_timestamp,
        )
    except ValidationError as exc:
        raise RuntimeError("OpenAI returned an invalid visual analysis payload.") from exc


def request_openai_vision(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    diff_result: FrameDiffResult,
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> dict[str, object]:
    settings = get_settings()
    content = [
        vision_text_message(scene, scene_range, extracted_frames, diff_result, ocr_labels_by_timestamp),
        *vision_image_messages(extracted_frames),
    ]
    request_payload = {
        "model": settings.openai_vision_model,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": vision_system_prompt()},
            {"role": "user", "content": content},
        ],
    }
    api_request = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers=openai_headers(settings.openai_api_key),
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=get_settings().visual_analysis_scene_timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI visual analysis failed: {detail}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI visual analysis failed: {describe_transport_error(exc)}") from exc
    return parse_visual_payload(payload)


def vision_system_prompt() -> str:
    return (
        "You analyze product UI video frames for an AI video editor. "
        "Return valid JSON with keys: scene_number, start, end, summary, confidence, motion_score, "
        "click_detected, visible_labels, primary_focus_box, cursor_box, click_target_box, "
        "frame_diff_score, cursor_path_confidence, ocr_match_score, anchor_box, frames. "
        "The frames key must be an array with one item per sampled frame. "
        "Each frame item must contain: timestamp, summary, cursor_box, click_target_box, dominant_box, "
        "click_confidence, diff_score, importance_score, ui_elements, ocr_labels. "
        "Each ui_elements item must contain: label, role, confidence, box. "
        "Every box must be normalized with x, y, width, height between 0 and 1. "
        "Use null when a box cannot be identified confidently."
    )


def vision_text_message(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    diff_result: FrameDiffResult,
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> dict[str, object]:
    motion_evidence_text = (
        frame_context(extracted_frames, diff_result.scores)
        if diff_result.available
        else "Unavailable because FFmpeg could not decode one or more sampled grayscale frames."
    )
    return {
        "type": "text",
        "text": (
            f"Scene number: {scene.scene_number}\n"
            f"Scene time range: {scene_range[0]:.2f}s to {scene_range[1]:.2f}s\n"
            f"Purpose: {scene.purpose}\n"
            f"Spoken line: {scene.spoken_line}\n"
            f"On-screen text hint: {scene.on_screen_text}\n"
            f"Transcript excerpt: {scene.source_excerpt}\n\n"
            f"Frame timestamps and diff scores: {motion_evidence_text}\n"
            f"Local OCR hints: {ocr_context(extracted_frames, ocr_labels_by_timestamp)}\n\n"
            "Track the cursor across frames, identify likely click hotspots, read visible UI labels, "
            "and anchor the best UI focus box. Be conservative. If evidence is weak, keep boxes null "
            "and lower confidence. Favor stable UI elements that match the spoken step. If frame-diff "
            "motion evidence is unavailable, rely on the frame images and OCR hints instead of guessing motion."
        ),
    }


def vision_image_messages(extracted_frames: list[ExtractedFrame]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for frame in extracted_frames:
        if not frame.image_path.exists():
            logger.warning(
                "Skipping missing visual-analysis frame at %.2fs: %s",
                frame.timestamp,
                frame.image_path,
            )
            continue
        messages.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url(frame.image_path)},
            }
        )
    if not messages:
        raise RuntimeError("No usable visual-analysis frame images remained for OpenAI vision.")
    return messages


def data_url(frame_path: Path) -> str:
    encoded = base64.b64encode(frame_path.read_bytes()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def parse_visual_payload(payload: dict[str, object]) -> dict[str, object]:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI did not return any visual analysis choices.")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned an empty visual analysis response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI returned invalid visual analysis JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI returned an invalid visual analysis payload shape.")
    return parsed


def normalize_visual_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    clamp_keys = {
        "confidence",
        "motion_score",
        "frame_diff_score",
        "cursor_path_confidence",
        "ocr_match_score",
        "ocr_confidence",
        "click_confidence",
        "diff_score",
        "importance_score",
    }
    box_keys = {
        "primary_focus_box",
        "cursor_box",
        "click_target_box",
        "anchor_box",
        "dominant_box",
        "box",
    }
    for key, value in list(normalized.items()):
        if key in clamp_keys:
            normalized[key] = clamp_unit_interval(value)
        elif key in box_keys:
            normalized[key] = normalize_box_value(value, key)
        elif key == "frames" and isinstance(value, list):
            normalized[key] = [normalize_visual_frame(frame, clamp_keys) for frame in value]
        elif key == "visible_labels":
            normalized[key] = normalize_string_list(value)
    return normalized


def normalize_visual_frame(frame: object, clamp_keys: set[str]) -> object:
    if not isinstance(frame, dict):
        return frame
    normalized = dict(frame)
    box_keys = {"cursor_box", "click_target_box", "dominant_box"}
    for key, value in list(normalized.items()):
        if key in clamp_keys:
            normalized[key] = clamp_unit_interval(value)
        elif key in box_keys:
            normalized[key] = normalize_box_value(value, key)
        elif key == "ui_elements" and isinstance(value, list):
            normalized[key] = [normalize_ui_element(item) for item in value]
        elif key == "ocr_labels":
            normalized[key] = normalize_string_list(value)
    return normalized


def normalize_ui_element(item: object) -> object:
    if not isinstance(item, dict):
        return item
    normalized = dict(item)
    if "confidence" in normalized:
        normalized["confidence"] = clamp_unit_interval(normalized["confidence"])
    if "box" in normalized:
        normalized["box"] = normalize_box_value(normalized["box"], "box")
    return normalized


def clamp_unit_interval(value: object) -> object:
    numeric = normalized_numeric(value)
    if numeric is None:
        return value
    return max(0.0, min(1.0, numeric))


def normalized_numeric(value: object) -> float | None:
    if isinstance(value, list):
        candidates = [normalized_numeric(item) for item in value]
        valid = [item for item in candidates if item is not None]
        return max(valid) if valid else None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def normalize_box_value(value: object, field_name: str) -> object:
    if isinstance(value, dict):
        return sanitize_box(value)
    if not isinstance(value, list):
        return None if value is None else value
    boxes = [sanitize_box(item) for item in value if isinstance(item, dict)]
    valid_boxes = [box for box in boxes if box is not None]
    if not valid_boxes:
        return None
    if field_name == "cursor_box":
        return valid_boxes[-1]
    return valid_boxes[0]


def sanitize_box(value: dict[str, object]) -> dict[str, float] | None:
    keys = ("x", "y", "width", "height")
    if any(key not in value for key in keys):
        return None
    normalized: dict[str, float] = {}
    for key in keys:
        numeric = normalized_numeric(value[key])
        if numeric is None:
            return None
        normalized[key] = max(0.0, min(1.0, numeric))
    if normalized["width"] <= 0 or normalized["height"] <= 0:
        return None
    return normalized


def normalize_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            normalized.append(item.strip())
    return normalized


def finalize_scene_analysis(
    analysis: VisualSceneAnalysisRecord,
    diff_result: FrameDiffResult,
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> VisualSceneAnalysisRecord:
    enriched = enrich_scene_analysis(analysis, ocr_labels_by_timestamp)
    frame_diff_score = enriched.frame_diff_score if diff_result.available else strongest_diff_score(enriched.frames)
    return enriched.model_copy(
        update={
            "frame_diff_available": diff_result.available,
            "frame_diff_score": frame_diff_score,
            "summary": summary_with_motion_evidence(enriched.summary, diff_result.available),
        }
    )


def enrich_scene_analysis(
    analysis: VisualSceneAnalysisRecord,
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> VisualSceneAnalysisRecord:
    frames = merged_frames(analysis.frames, ocr_labels_by_timestamp)
    return analysis.model_copy(
        update={
            "anchor_box": analysis.anchor_box or inferred_anchor_box(analysis),
            "click_target_box": analysis.click_target_box or strongest_click_box(frames),
            "cursor_box": analysis.cursor_box or latest_cursor_box(frames),
            "primary_focus_box": analysis.primary_focus_box or strongest_focus_box(frames),
            "visible_labels": analysis.visible_labels or visible_labels(frames),
            "frame_diff_score": max(analysis.frame_diff_score, strongest_diff_score(frames)),
            "cursor_path_confidence": max(analysis.cursor_path_confidence, cursor_path_confidence(frames)),
            "ocr_confidence": max(analysis.ocr_confidence, ocr_confidence(frames)),
            "frames": frames,
        }
    )


def summary_with_motion_evidence(summary: str, available: bool) -> str:
    if available:
        return summary
    return f"{summary} Motion diff evidence was unavailable, so motion confidence relies on image and OCR signals only."


def merged_frames(
    frames: list[FrameSignalRecord],
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> list[FrameSignalRecord]:
    return [
        frame.model_copy(
            update={
                "ocr_labels": frame.ocr_labels or ocr_labels_by_timestamp.get(frame.timestamp, OcrFrameResult([], 0.0)).labels,
                "ocr_confidence": max(frame.ocr_confidence, ocr_labels_by_timestamp.get(frame.timestamp, OcrFrameResult([], 0.0)).confidence),
            }
        )
        for frame in frames
    ]


def inferred_anchor_box(analysis: VisualSceneAnalysisRecord) -> FocusBox | None:
    return analysis.click_target_box or strongest_element_box(analysis.frames) or analysis.primary_focus_box


def strongest_click_box(frames: list[FrameSignalRecord]) -> FocusBox | None:
    ranked = sorted((frame for frame in frames if frame.click_target_box is not None), key=lambda frame: frame.click_confidence, reverse=True)
    return ranked[0].click_target_box if ranked else None


def latest_cursor_box(frames: list[FrameSignalRecord]) -> FocusBox | None:
    cursor_frames = [frame for frame in frames if frame.cursor_box is not None]
    return cursor_frames[-1].cursor_box if cursor_frames else None


def strongest_element_box(frames: list[FrameSignalRecord]) -> FocusBox | None:
    elements = [element for frame in frames for element in frame.ui_elements]
    ranked = sorted(elements, key=lambda element: element.confidence, reverse=True)
    return ranked[0].box if ranked else None


def strongest_focus_box(frames: list[FrameSignalRecord]) -> FocusBox | None:
    ranked = sorted(
        (frame for frame in frames if frame.dominant_box is not None),
        key=lambda frame: frame.importance_score,
        reverse=True,
    )
    return ranked[0].dominant_box if ranked else None


def visible_labels(frames: list[FrameSignalRecord]) -> list[str]:
    labels = []
    for frame in frames:
        labels.extend(frame.ocr_labels)
        labels.extend(element.label for element in frame.ui_elements)
    return unique_labels(labels)[:8]


def unique_labels(labels: list[str]) -> list[str]:
    unique: list[str] = []
    for label in labels:
        normalized = label.strip()
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def strongest_diff_score(frames: list[FrameSignalRecord]) -> float:
    return max((frame.diff_score for frame in frames), default=0.0)


def cursor_path_confidence(frames: list[FrameSignalRecord]) -> float:
    cursor_frames = [frame for frame in frames if frame.cursor_box is not None]
    if len(cursor_frames) < 2:
        return 0.0
    return min(1.0, len(cursor_frames) / max(len(frames), 1) + movement_consistency(cursor_frames))


def movement_consistency(frames: list[FrameSignalRecord]) -> float:
    deltas = [
        center_delta(previous.cursor_box, current.cursor_box)
        for previous, current in zip(frames, frames[1:])
        if previous.cursor_box is not None and current.cursor_box is not None
    ]
    if not deltas:
        return 0.0
    average_delta = sum(deltas) / len(deltas)
    return max(0.0, 0.4 - average_delta)


def center_delta(left: FocusBox, right: FocusBox) -> float:
    left_center = (left.x + left.width / 2, left.y + left.height / 2)
    right_center = (right.x + right.width / 2, right.y + right.height / 2)
    return float(abs(left_center[0] - right_center[0]) + abs(left_center[1] - right_center[1]))


def frame_context(extracted_frames: list[ExtractedFrame], diff_scores: list[float]) -> str:
    pairs = zip(extracted_frames, diff_scores, strict=False)
    return ", ".join(f"{frame.timestamp:.2f}s(diff={diff:.2f})" for frame, diff in pairs)


def ocr_context(
    extracted_frames: list[ExtractedFrame],
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> str:
    parts = []
    for frame in extracted_frames:
        result = ocr_labels_by_timestamp.get(frame.timestamp)
        if result and result.labels:
            parts.append(f"{frame.timestamp:.2f}s(c={result.confidence:.2f}): {' | '.join(result.labels[:4])}")
    return "; ".join(parts) if parts else "none"


def ocr_confidence(frames: list[FrameSignalRecord]) -> float:
    if not frames:
        return 0.0
    return round(sum(frame.ocr_confidence for frame in frames) / len(frames), 3)
