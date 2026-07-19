from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.models.projects import FrameSignalRecord, LaunchScriptScene, VisualSceneAnalysisRecord
from app.services.ocr_pipeline import OcrFrameResult, extract_ocr_labels
from app.services.video_frames import extract_frames_for_scene


def lightweight_scene_analysis(
    video_path: Path,
    temp_dir: Path,
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    settings: Settings,
) -> VisualSceneAnalysisRecord:
    scene_output_dir = temp_dir / f"scene-{scene.scene_number}-lite"
    scene_output_dir.mkdir(parents=True, exist_ok=True)
    extracted_frames = extract_frames_for_scene(
        video_path,
        scene.scene_number,
        scene_range,
        scene_output_dir,
        frame_budget=max(settings.visual_analysis_frames_per_scene // 3, 3),
        frame_width=max(settings.visual_analysis_frame_width // 2, 320),
        jpeg_quality=settings.visual_analysis_jpeg_quality,
    )
    ocr_labels = extract_ocr_labels(extracted_frames)
    visible_labels = list(
        dict.fromkeys(label for frame in extracted_frames for label in ocr_labels.get(frame.timestamp, OcrFrameResult([], 0.0)).labels[:4])
    )[:8]
    frames = [
        FrameSignalRecord(
            timestamp=frame.timestamp,
            summary=scene.on_screen_text or scene.purpose,
            ocr_labels=ocr_labels.get(frame.timestamp, OcrFrameResult([], 0.0)).labels[:6],
            ocr_confidence=ocr_labels.get(frame.timestamp, OcrFrameResult([], 0.0)).confidence,
        )
        for frame in extracted_frames
    ]
    return VisualSceneAnalysisRecord(
        scene_number=scene.scene_number,
        start=scene_range[0],
        end=scene_range[1],
        summary=scene.on_screen_text or scene.purpose,
        confidence=0.22,
        motion_score=0.16,
        visible_labels=visible_labels,
        frames=frames,
        frame_diff_available=False,
        frame_diff_score=0.0,
        ocr_confidence=max((frame.ocr_confidence for frame in frames), default=0.0),
    )
