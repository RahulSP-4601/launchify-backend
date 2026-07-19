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
from app.services.lightweight_visual_hunt import lightweight_scene_analysis
from app.services.video_frames import ExtractedFrame
from app.services.video_frames import extract_frames_for_scene
from app.services.vision_analyzer import analyze_scene_frames

logger = logging.getLogger(__name__)


def analyze_video_scenes(
    video_path: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
    scene_ranges: list[tuple[float, float]] | None = None,
) -> list[VisualSceneAnalysisRecord]:
    settings = get_settings()
    ranges = scene_ranges or align_script_scenes(launch_script.scenes, transcript)
    prioritized = prioritized_scene_inputs(launch_script.scenes, ranges, transcript)
    analyses: list[VisualSceneAnalysisRecord] = []
    started_at = monotonic()
    total_scenes = len(launch_script.scenes)
    with TemporaryDirectory(prefix="launchify-vision-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        for index, (scene, scene_range) in enumerate(prioritized, start=1):
            elapsed = monotonic() - started_at
            if visual_analysis_budget_reached(elapsed, settings.visual_analysis_total_budget_seconds):
                analyses.append(lightweight_scene_analysis(video_path, temp_dir, scene, scene_range, settings))
                continue
            analysis = analyze_scene(video_path, temp_dir, scene, scene_range, elapsed, settings, scene_budget_frames(index, total_scenes, settings))
            if analysis is not None:
                analyses.append(analysis)
    return analyses


def prioritized_scene_inputs(
    scenes: list[LaunchScriptScene],
    ranges: list[tuple[float, float]],
    transcript: list[TranscriptSegment],
) -> list[tuple[LaunchScriptScene, tuple[float, float]]]:
    paired = list(zip(scenes, ranges, strict=True))
    return sorted(paired, key=lambda item: scene_priority(item[0], item[1], transcript), reverse=True)


def scene_priority(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
    transcript: list[TranscriptSegment],
) -> tuple[float, float, float]:
    transcript_text = " ".join(segment.text for segment in transcript if segment.end >= scene_range[0] and segment.start <= scene_range[1])
    action_keywords = ("click", "select", "open", "type", "enter", "continue", "login", "course")
    action_hits = sum(1 for keyword in action_keywords if keyword in transcript_text.lower() or keyword in scene.spoken_line.lower())
    duration = max(scene_range[1] - scene_range[0], 0.0)
    early_bonus = max(0.0, 1.0 - scene_range[0] / 60.0)
    return float(action_hits), early_bonus, duration


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
    frame_budget: int,
) -> VisualSceneAnalysisRecord | None:
    scene_output_dir = temp_dir / f"scene-{scene.scene_number}"
    scene_output_dir.mkdir(parents=True, exist_ok=True)
    extracted_frames: list[ExtractedFrame] = []
    ocr_labels: dict[float, OcrFrameResult] = {}
    scene_started_at = monotonic()
    logger.info("Visual analysis: scene %s started (elapsed %.2fs, budget %ss).", scene.scene_number, elapsed, settings.visual_analysis_total_budget_seconds)
    try:
        extracted_frames = extract_frames_for_scene(
            video_path,
            scene.scene_number,
            scene_range,
            scene_output_dir,
            frame_budget=frame_budget,
            frame_width=settings.visual_analysis_frame_width,
            jpeg_quality=settings.visual_analysis_jpeg_quality,
        )
        ocr_labels = extract_ocr_labels(extracted_frames)
        if should_fallback_after_extraction(scene_started_at, settings, scene_range, extracted_frames, ocr_labels):
            return local_fallback_analysis(scene, scene_range, extracted_frames, ocr_labels)
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


def scene_budget_frames(scene_index: int, total_scenes: int, settings: Settings) -> int:
    if total_scenes <= 0:
        return settings.visual_analysis_frames_per_scene
    adaptive_budget = max(settings.visual_analysis_frames_per_scene // 2, 2)
    if total_scenes <= 4 or scene_index <= 2:
        return settings.visual_analysis_frames_per_scene
    return adaptive_budget


def scene_took_too_long(scene_started_at: float, settings: Settings) -> bool:
    return monotonic() - scene_started_at >= max(settings.visual_analysis_scene_timeout_seconds * 0.45, 8.0)


def should_fallback_after_extraction(
    scene_started_at: float,
    settings: Settings,
    scene_range: tuple[float, float],
    extracted_frames: list[ExtractedFrame],
    ocr_labels: dict[float, OcrFrameResult],
) -> bool:
    if not scene_took_too_long(scene_started_at, settings):
        return False
    if not extracted_frames:
        return True
    populated_ocr = sum(1 for result in ocr_labels.values() if result.labels)
    return populated_ocr == 0


def budget_fallback_analysis(
    scene: LaunchScriptScene,
    scene_range: tuple[float, float],
) -> VisualSceneAnalysisRecord:
    return local_fallback_analysis(scene, scene_range, [], {})


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
