from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import FocusBox, FrameSignalRecord, LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.frame_signal_analyzer import frame_diff_scores
from app.services.ocr_pipeline import OcrFrameResult
from app.services.script_writer import describe_transport_error, openai_headers
from app.services.video_frames import ExtractedFrame


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
    payload = request_openai_vision(
        scene,
        scene_range,
        extracted_frames,
        frame_diff_scores(video_path, timestamps),
        ocr_labels_by_timestamp,
    )
    try:
        return enrich_scene_analysis(
            VisualSceneAnalysisRecord.model_validate(payload),
            ocr_labels_by_timestamp,
        )
    except ValidationError as exc:
        raise RuntimeError("OpenAI returned an invalid visual analysis payload.") from exc


def request_openai_vision(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    diff_scores: list[float],
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> dict[str, object]:
    settings = get_settings()
    content = [
        vision_text_message(scene, scene_range, extracted_frames, diff_scores, ocr_labels_by_timestamp),
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
        with request.urlopen(api_request, timeout=180) as response:
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
    diff_scores: list[float],
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
) -> dict[str, object]:
    return {
        "type": "text",
        "text": (
            f"Scene number: {scene.scene_number}\n"
            f"Scene time range: {scene_range[0]:.2f}s to {scene_range[1]:.2f}s\n"
            f"Purpose: {scene.purpose}\n"
            f"Spoken line: {scene.spoken_line}\n"
            f"On-screen text hint: {scene.on_screen_text}\n"
            f"Transcript excerpt: {scene.source_excerpt}\n\n"
            f"Frame timestamps and diff scores: {frame_context(extracted_frames, diff_scores)}\n"
            f"Local OCR hints: {ocr_context(extracted_frames, ocr_labels_by_timestamp)}\n\n"
            "Track the cursor across frames, identify likely click hotspots, read visible UI labels, "
            "and anchor the best UI focus box. Be conservative. If evidence is weak, keep boxes null "
            "and lower confidence. Favor stable UI elements that match the spoken step."
        ),
    }


def vision_image_messages(extracted_frames: list[ExtractedFrame]) -> list[dict[str, object]]:
    return [
        {
            "type": "image_url",
            "image_url": {"url": data_url(frame.image_path)},
        }
        for frame in extracted_frames
    ]


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
        for previous, current in zip(frames, frames[1:], strict=True)
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
