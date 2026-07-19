from __future__ import annotations

from app.services.ocr_pipeline import OcrFrameResult
from app.services.video_frames import ExtractedFrame


def candidate_hint_text(
    extracted_frames: list[ExtractedFrame],
    ocr_labels_by_timestamp: dict[float, OcrFrameResult],
    *,
    reduced: bool = False,
) -> str:
    candidates: list[str] = []
    for frame in extracted_frames:
        labels = ocr_labels_by_timestamp.get(frame.timestamp, OcrFrameResult([], 0.0)).labels
        if not labels:
            continue
        candidate = candidate_phrase(labels)
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    if reduced:
        candidates = candidates[:3]
    return "; ".join(candidates) if candidates else "none"


def candidate_phrase(labels: list[str]) -> str:
    visible = [label.strip() for label in labels if label.strip()]
    if not visible:
        return ""
    joined = " ".join(visible[:4])
    normalized = joined.lower()
    if "coming soon" in normalized and len(visible) > 1:
        return " / ".join(visible[:2])
    if any(keyword in normalized for keyword in ("open course", "log in", "sign up", "create account", "google login")):
        return joined
    return visible[0]
