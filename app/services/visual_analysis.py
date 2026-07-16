from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import get_settings
from app.models.projects import LaunchScriptRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.ocr_pipeline import extract_ocr_labels
from app.services.scene_alignment import align_script_scenes
from app.services.video_frames import extract_scene_frames
from app.services.vision_analyzer import analyze_scene_frames
def analyze_video_scenes(
    video_path: Path,
    launch_script: LaunchScriptRecord,
    transcript: list[TranscriptSegment],
) -> list[VisualSceneAnalysisRecord]:
    scene_ranges = align_script_scenes(launch_script.scenes, transcript)
    scene_numbers = [scene.scene_number for scene in launch_script.scenes]
    max_workers = min(max(get_settings().visual_analysis_concurrency, 1), max(len(scene_numbers), 1))
    with TemporaryDirectory(prefix="launchify-vision-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        frames_by_scene = extract_scene_frames(video_path, scene_numbers, scene_ranges, temp_dir)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            analyses = executor.map(
                lambda scene_inputs: analyze_scene_frames(
                    scene_inputs[0],
                    scene_inputs[1],
                    frames_by_scene[scene_inputs[0].scene_number],
                    video_path,
                    extract_ocr_labels(frames_by_scene[scene_inputs[0].scene_number]),
                ),
                zip(launch_script.scenes, scene_ranges, strict=True),
            )
            return list(analyses)
def analysis_map(analyses: list[VisualSceneAnalysisRecord]) -> dict[int, VisualSceneAnalysisRecord]:
    return {analysis.scene_number: analysis for analysis in analyses}
def visual_analysis_available() -> bool:
    settings = get_settings()
    if not settings.openai_api_key:
        return False
    return ffmpeg_available(settings.ffmpeg_binary)
def ffmpeg_available(ffmpeg_binary: str) -> bool:
    if "/" in ffmpeg_binary:
        return Path(ffmpeg_binary).exists()
    return shutil.which(ffmpeg_binary) is not None
