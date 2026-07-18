from __future__ import annotations

import logging
from time import monotonic
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import Settings, get_settings
from app.models.projects import FrameSignalRecord, LaunchScriptRecord, LaunchScriptScene, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.ocr_pipeline import OcrFrameResult, extract_ocr_labels
from app.services.scene_alignment import align_script_scenes
from app.services.video_frames import ExtractedFrame
from app.services.video_frames import extract_frames_for_scene
from app.services.vision_analyzer import analyze_scene_frames

logger = logging.getLogger(__name__)


def analyze_video_scenes(
    video_path: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
) -> list[VisualSceneAnalysisRecord]:
    settings = get_settings()
    scene_ranges = align_script_scenes(launch_script.scenes, transcript)
    analyses: list[VisualSceneAnalysisRecord] = []
    started_at = monotonic()
    with TemporaryDirectory(prefix="launchify-vision-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for scene, scene_range in zip(launch_script.scenes, scene_ranges, strict=True):
            elapsed = monotonic() - started_at
            if visual_analysis_budget_reached(elapsed, settings.visual_analysis_total_budget_seconds):
                break
            analysis = analyze_scene(video_path, temp_dir, scene, scene_range, elapsed, settings)
            if analysis is not None:
                analyses.append(analysis)
    return analyses


def analysis_map(analyses: list[VisualSceneAnalysisRecord]) -> dict[int, VisualSceneAnalysisRecord]:
    return {analysis.scene_number: analysis for analysis in analyses}


def visual_analysis_budget_reached(elapsed: float, total_budget_seconds: int) -> bool:
    if elapsed < total_budget_seconds:
        return False
    logger.warning(
        "Visual analysis budget reached after %.2fs. Continuing planning without scene analyses for remaining scenes.",
        elapsed,
    )
    return True


def analyze_scene(
    video_path: Path,
    temp_dir: Path,
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    elapsed: float,
    settings: Settings,
) -> VisualSceneAnalysisRecord | None:
    scene_output_dir = temp_dir / f"scene-{scene.scene_number}"
    scene_output_dir.mkdir(parents=True, exist_ok=True)
    extracted_frames: list[ExtractedFrame] = []
    ocr_labels: dict[float, OcrFrameResult] = {}
    logger.info(
        "Visual analysis: scene %s started (elapsed %.2fs, budget %ss).",
        scene.scene_number,
        elapsed,
        settings.visual_analysis_total_budget_seconds,
    )
    try:
        extracted_frames = extract_frames_for_scene(
            video_path,
            scene.scene_number,
            scene_range,
            scene_output_dir,
            frame_budget=settings.visual_analysis_frames_per_scene,
            frame_width=settings.visual_analysis_frame_width,
            jpeg_quality=settings.visual_analysis_jpeg_quality,
        )
        ocr_labels = extract_ocr_labels(extracted_frames)
        analysis = analyze_scene_frames(
            scene,
            scene_range,
            extracted_frames,
            video_path,
            ocr_labels,
        )
        logger.info("Visual analysis: scene %s completed.", scene.scene_number)
        return analysis
    except Exception as exc:
        logger.warning(
            "Visual analysis: scene %s fell back to local heuristics after: %s",
            scene.scene_number,
            exc,
        )
        return local_fallback_analysis(scene, scene_range, extracted_frames, ocr_labels)


def visual_analysis_available() -> bool:
    settings = get_settings()
    if not settings.openai_api_key:
        return False
    return ffmpeg_available(settings.ffmpeg_binary)


def ffmpeg_available(ffmpeg_binary: str) -> bool:
    if "/" in ffmpeg_binary:
        return Path(ffmpeg_binary).exists()
    return shutil.which(ffmpeg_binary) is not None


def local_fallback_analysis(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    ocr_labels: dict[float, OcrFrameResult],
) -> VisualSceneAnalysisRecord:
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
        confidence=0.18,
        motion_score=0.12,
        visible_labels=visible_labels,
        frames=frames,
        frame_diff_available=False,
        frame_diff_score=0.0,
        ocr_confidence=max((frame.ocr_confidence for frame in frames), default=0.0),
    )
