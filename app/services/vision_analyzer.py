from __future__ import annotations

import base64
import json
from pathlib import Path
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.script_writer import describe_transport_error, openai_headers


def analyze_scene_frames(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    frame_paths: list[Path],
) -> VisualSceneAnalysisRecord:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OpenAI is not configured yet. Add OPENAI_API_KEY to enable visual analysis.")
    payload = request_openai_vision(scene, scene_range, frame_paths)
    try:
        return VisualSceneAnalysisRecord.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError("OpenAI returned an invalid visual analysis payload.") from exc


def request_openai_vision(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    frame_paths: list[Path],
) -> dict[str, object]:
    settings = get_settings()
    content = [vision_text_message(scene, scene_range), *vision_image_messages(frame_paths)]
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
        "click_detected, visible_labels, primary_focus_box, cursor_box, click_target_box. "
        "Every box must be normalized with x, y, width, height between 0 and 1. "
        "Use null when a box cannot be identified confidently."
    )


def vision_text_message(scene: LaunchScriptScene, scene_range: tuple[float, float]) -> dict[str, object]:
    return {
        "type": "text",
        "text": (
            f"Scene number: {scene.scene_number}\n"
            f"Scene time range: {scene_range[0]:.2f}s to {scene_range[1]:.2f}s\n"
            f"Purpose: {scene.purpose}\n"
            f"Spoken line: {scene.spoken_line}\n"
            f"On-screen text hint: {scene.on_screen_text}\n"
            f"Transcript excerpt: {scene.source_excerpt}\n\n"
            "Find the likely cursor area, click target, and most important UI focus region. "
            "Be conservative. If evidence is weak, keep boxes null and lower confidence."
        ),
    }


def vision_image_messages(frame_paths: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "type": "image_url",
            "image_url": {"url": data_url(frame_path)},
        }
        for frame_path in frame_paths
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
