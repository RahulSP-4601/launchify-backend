from __future__ import annotations


def visual_response_format() -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "visual_scene_analysis",
            "schema": visual_scene_schema(),
            "strict": True,
        },
    }


def visual_scene_schema() -> dict[str, object]:
    box = box_schema()
    frame = frame_schema(box)
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scene_number": {"type": "integer"},
            "start": {"type": "number"},
            "end": {"type": "number"},
            "summary": {"type": "string"},
            "confidence": {"type": "number"},
            "motion_score": {"type": "number"},
            "click_detected": {"type": "boolean"},
            "visible_labels": {"type": "array", "items": {"type": "string"}},
            "primary_focus_box": {"anyOf": [box, {"type": "null"}]},
            "cursor_box": {"anyOf": [box, {"type": "null"}]},
            "click_target_box": {"anyOf": [box, {"type": "null"}]},
            "frame_diff_score": {"type": "number"},
            "cursor_path_confidence": {"type": "number"},
            "ocr_match_score": {"type": "number"},
            "anchor_box": {"anyOf": [box, {"type": "null"}]},
            "frames": {"type": "array", "items": frame},
        },
        "required": ["scene_number", "start", "end", "summary", "confidence", "motion_score", "click_detected", "visible_labels", "primary_focus_box", "cursor_box", "click_target_box", "frame_diff_score", "cursor_path_confidence", "ocr_match_score", "anchor_box", "frames"],
    }


def frame_schema(box: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": frame_properties(box),
        "required": frame_required_fields(),
    }


def frame_properties(box: dict[str, object]) -> dict[str, object]:
    return {
        "timestamp": {"type": "number"},
        "summary": {"type": "string"},
        "cursor_box": nullable_box_schema(box),
        "click_target_box": nullable_box_schema(box),
        "dominant_box": nullable_box_schema(box),
        "click_confidence": {"type": "number"},
        "diff_score": {"type": "number"},
        "importance_score": {"type": "number"},
        "ui_elements": {"type": "array", "items": ui_element_schema(box)},
        "ocr_labels": {"type": "array", "items": {"type": "string"}},
    }


def frame_required_fields() -> list[str]:
    return [
        "timestamp",
        "summary",
        "cursor_box",
        "click_target_box",
        "dominant_box",
        "click_confidence",
        "diff_score",
        "importance_score",
        "ui_elements",
        "ocr_labels",
    ]


def ui_element_schema(box: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "label": {"type": "string"},
            "role": {"type": "string"},
            "confidence": {"type": "number"},
            "box": nullable_box_schema(box),
        },
        "required": ["label", "role", "confidence", "box"],
    }


def nullable_box_schema(box: dict[str, object]) -> dict[str, object]:
    return {"anyOf": [box, {"type": "null"}]}


def box_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "width": {"type": "number"},
            "height": {"type": "number"},
        },
        "required": ["x", "y", "width", "height"],
    }
