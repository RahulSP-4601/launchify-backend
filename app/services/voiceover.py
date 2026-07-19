from __future__ import annotations

import http.client
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

from app.core.config import get_settings
from app.models.projects import (
    AssetRecord,
    GuideRecord,
    GuideStepRecord,
    LaunchScriptRecord,
    LaunchScriptScene,
    VoiceoverClipRecord,
    VoiceoverCueRecord,
    VoiceoverMode,
    VoiceoverRecord,
    VoiceoverStatus,
)
from app.services.storage import download_asset_to_file, upload_audio_file
from app.services.walkthrough_guardrails import guide_is_under_grounded

logger = logging.getLogger(__name__)
MAX_TTS_CHARACTERS = 1800


@dataclass(frozen=True)
class VoiceoverUnit:
    scene_number: int
    start: float
    end: float
    text: str


@dataclass
class GeneratedClip:
    clip: VoiceoverClipRecord
    local_audio_path: Path | None


def build_voiceover(
    user_id: str,
    project_id: str,
    mode: VoiceoverMode,
    *,
    source_duration_seconds: float = 0.0,
    guide: GuideRecord | None = None,
    launch_script: LaunchScriptRecord | None = None,
) -> VoiceoverRecord:
    units = voiceover_units(guide=guide, launch_script=launch_script)
    script = " ".join(unit.text for unit in units).strip()
    base_clips = clip_records(units)
    if mode == "original":
        return script_only_voiceover(mode, "disabled", script, base_clips)
    degraded = degraded_voiceover(guide, launch_script, mode, source_duration_seconds)
    if degraded is not None:
        return degraded
    if not script:
        return VoiceoverRecord(mode=mode, status="disabled", script="", cues=[], clips=[])
    if not get_settings().deepgram_api_key:
        return script_only_voiceover(mode, "script_only", script, base_clips)
    generated = synthesize_voiceover_clips(user_id, project_id, units)
    merged_audio_asset = merged_voiceover_asset(user_id, project_id, generated)
    clips = [item.clip for item in generated]
    cues = cue_track(clips)
    has_merged_audio = merged_audio_asset is not None and bool(merged_audio_asset.storage_path)
    has_clip_audio = any(clip.audio_storage_path for clip in clips)
    return VoiceoverRecord(
        provider="deepgram",
        model=get_settings().deepgram_tts_model,
        mode=mode,
        status="ready" if (has_merged_audio or has_clip_audio) else "script_only",
        script=script,
        cues=cues,
        clips=clips,
        audio_storage_path=merged_audio_asset.storage_path if merged_audio_asset is not None else "",
        duration_seconds=round(max((clip.start + clip.duration_seconds for clip in clips), default=0.0), 2),
    )


def degraded_voiceover(
    guide: GuideRecord | None,
    launch_script: LaunchScriptRecord | None,
    mode: VoiceoverMode,
    source_duration_seconds: float,
) -> VoiceoverRecord | None:
    if guide is None or not guide_is_under_grounded(guide, source_duration_seconds):
        return None
    units = voiceover_units(guide=guide, launch_script=launch_script)
    return script_only_voiceover(mode, "script_only", " ".join(unit.text for unit in units).strip(), clip_records(units))


def script_only_voiceover(
    mode: VoiceoverMode,
    status: VoiceoverStatus,
    script: str,
    clips: list[VoiceoverClipRecord],
) -> VoiceoverRecord:
    cues = cue_track(clips)
    return VoiceoverRecord(
        mode=mode,
        status=status,
        script=script,
        cues=cues,
        clips=clips,
        duration_seconds=round(cues[-1].end, 2) if cues else 0.0,
    )


def voiceover_units(
    *,
    guide: GuideRecord | None,
    launch_script: LaunchScriptRecord | None,
) -> list[VoiceoverUnit]:
    if guide is not None and guide.steps:
        return [unit_from_step(step) for step in guide.steps if normalized_voice_line(step.narration)]
    if launch_script is None:
        return []
    return units_from_script(launch_script)

def unit_from_step(step: GuideStepRecord) -> VoiceoverUnit:
    text = normalized_voice_line(step.narration)
    start = round(step.start, 2)
    end = round(max(step.end, start + 0.8), 2)
    return VoiceoverUnit(scene_number=step.step_index, start=start, end=end, text=text)


def units_from_script(launch_script: LaunchScriptRecord) -> list[VoiceoverUnit]:
    units: list[VoiceoverUnit] = []
    cursor = 0.0
    for scene in launch_script.scenes:
        text = normalized_voice_line(scene.spoken_line)
        if not text:
            continue
        duration = round(max(scene.estimated_duration_seconds, 0.8), 2)
        units.append(unit_from_scene(scene, cursor, duration))
        cursor = round(cursor + duration, 2)
    return units


def unit_from_scene(scene: LaunchScriptScene, start: float, duration: float) -> VoiceoverUnit:
    text = normalized_voice_line(scene.spoken_line)
    end = round(start + duration, 2)
    return VoiceoverUnit(scene_number=scene.scene_number, start=start, end=end, text=text)


def clip_records(units: list[VoiceoverUnit]) -> list[VoiceoverClipRecord]:
    return [
        VoiceoverClipRecord(
            scene_number=unit.scene_number,
            start=round(unit.start, 2),
            end=round(unit.end, 2),
            text=unit.text,
            duration_seconds=round(max(unit.end - unit.start, estimated_voice_duration_seconds(unit.text)), 2),
        )
        for unit in units
    ]


def cue_track(clips: list[VoiceoverClipRecord]) -> list[VoiceoverCueRecord]:
    cues: list[VoiceoverCueRecord] = []
    cursor = 0.0
    for clip in clips:
        duration = max(clip.duration_seconds, 0.4)
        cues.append(
            VoiceoverCueRecord(
                scene_number=clip.scene_number,
                start=round(cursor, 2),
                end=round(cursor + duration, 2),
                text=clip.text,
                duration_seconds=round(duration, 2),
            )
        )
        cursor += duration
    return cues


def synthesize_voiceover_clips(
    user_id: str,
    project_id: str,
    units: list[VoiceoverUnit],
) -> list[GeneratedClip]:
    generated: list[GeneratedClip] = []
    for unit in units:
        audio_file = request_tts_audio(unit.text, get_settings().deepgram_tts_model)
        if audio_file is None:
            generated.append(
                GeneratedClip(
                    clip=VoiceoverClipRecord(
                        scene_number=unit.scene_number,
                        start=unit.start,
                        end=unit.end,
                        text=unit.text,
                        duration_seconds=round(max(unit.end - unit.start, estimated_voice_duration_seconds(unit.text)), 2),
                    ),
                    local_audio_path=None,
                )
            )
            continue
        duration = audio_duration_seconds(audio_file, fallback=max(unit.end - unit.start, estimated_voice_duration_seconds(unit.text)))
        asset = upload_audio_file(user_id, project_id, f"voiceover-scene-{unit.scene_number}.mp3", audio_file)
        generated.append(
            GeneratedClip(
                clip=VoiceoverClipRecord(
                    scene_number=unit.scene_number,
                    start=round(unit.start, 2),
                    end=round(unit.end, 2),
                    text=unit.text,
                    duration_seconds=duration,
                    audio_storage_path=asset.storage_path,
                ),
                local_audio_path=audio_file,
            )
        )
    return generated


def merged_voiceover_asset(
    user_id: str,
    project_id: str,
    generated: list[GeneratedClip],
) -> AssetRecord | None:
    timed_clips = [(item.clip, item.local_audio_path) for item in generated if item.local_audio_path is not None]
    if not timed_clips:
        return None
    merged = scheduled_voiceover_track(timed_clips)
    if merged is None:
        return None
    try:
        return upload_audio_file(user_id, project_id, "voiceover.mp3", merged)
    finally:
        merged.unlink(missing_ok=True)


def refresh_voiceover_asset(
    user_id: str,
    project_id: str,
    voiceover: VoiceoverRecord,
) -> VoiceoverRecord:
    timed_clips = downloaded_voiceover_clips(voiceover)
    if not timed_clips:
        return voiceover.model_copy(update={"status": "script_only", "audio_storage_path": ""})
    merged = scheduled_voiceover_track(timed_clips)
    if merged is None:
        return voiceover.model_copy(update={"status": "ready", "audio_storage_path": ""})
    try:
        asset = upload_audio_file(user_id, project_id, "voiceover.mp3", merged)
    finally:
        merged.unlink(missing_ok=True)
    return voiceover.model_copy(update={"status": "ready", "audio_storage_path": asset.storage_path})


def downloaded_voiceover_clips(
    voiceover: VoiceoverRecord,
) -> list[tuple[VoiceoverClipRecord, Path]]:
    timed_clips: list[tuple[VoiceoverClipRecord, Path]] = []
    for clip in voiceover.clips:
        if not clip.audio_storage_path:
            continue
        audio_file = download_asset_to_file(clip.audio_storage_path)
        timed_clips.append((clip, audio_file))
    return timed_clips


def downloadable_voiceover_audio(voiceover: VoiceoverRecord) -> Path | None:
    if voiceover.audio_storage_path:
        return download_asset_to_file(voiceover.audio_storage_path)
    timed_clips = downloaded_voiceover_clips(voiceover)
    if not timed_clips:
        return None
    return scheduled_voiceover_track(timed_clips)


def audio_duration_seconds(audio_file: Path, fallback: float) -> float:
    settings = get_settings()
    command = [
        settings.ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(audio_file),
    ]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=settings.ffmpeg_timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        logger.warning("Voiceover duration probe failed for %s: %s", audio_file.name, exc)
        return round(fallback, 2)
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return round(fallback, 2)
    return round(duration, 2) if duration > 0 else round(fallback, 2)


def request_tts_audio(script: str, model: str) -> Path | None:
    chunks = script_chunks(script)
    if len(chunks) == 1:
        return request_single_tts_audio(chunks[0], model)
    audio_files = [audio_file for chunk in chunks if (audio_file := request_single_tts_audio(chunk, model)) is not None]
    if len(audio_files) != len(chunks):
        for audio_file in audio_files:
            audio_file.unlink(missing_ok=True)
        return None
    return concatenate_audio_files(audio_files)


def script_chunks(script: str) -> list[str]:
    words = script.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word]).strip()
        if current and len(candidate) > MAX_TTS_CHARACTERS:
            chunks.append(" ".join(current).strip())
            current = [word]
            continue
        current.append(word)
    if current:
        chunks.append(" ".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def request_single_tts_audio(script: str, model: str) -> Path | None:
    endpoint = f"https://api.deepgram.com/v1/speak?model={quote(model)}&encoding=mp3"
    parsed = urlsplit(endpoint)
    if not parsed.hostname:
        return None
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=180)
    temp_file = NamedTemporaryFile(delete=False, suffix=".mp3")
    try:
        body = json.dumps({"text": script})
        connection.putrequest("POST", parsed.path + ("?" + parsed.query if parsed.query else ""))
        connection.putheader("Authorization", f"Token {get_settings().deepgram_api_key}")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(len(body.encode("utf-8"))))
        connection.endheaders()
        connection.send(body.encode("utf-8"))
        response = connection.getresponse()
        if response.status >= 400:
            detail = response.read().decode("utf-8", errors="ignore")
            Path(temp_file.name).unlink(missing_ok=True)
            logger.warning("Deepgram TTS request failed with status %s: %s", response.status, detail)
            return None
        while chunk := response.read(1024 * 1024):
            temp_file.write(chunk)
    finally:
        temp_file.close()
        connection.close()
    saved_file = Path(temp_file.name)
    return saved_file if saved_file.stat().st_size > 0 else None


def concatenate_audio_files(audio_files: list[Path]) -> Path | None:
    settings = get_settings()
    with TemporaryDirectory(prefix="launchify-voiceover-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        concat_list = temp_dir / "concat.txt"
        output_file = temp_dir / "voiceover.mp3"
        concat_list.write_text("".join(f"file '{audio_file.as_posix()}'\n" for audio_file in audio_files), encoding="utf-8")
        command = [
            settings.ffmpeg_binary,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:a",
            "libmp3lame",
            str(output_file),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=settings.ffmpeg_timeout_seconds)
            final_file = NamedTemporaryFile(delete=False, suffix=".mp3")
            final_file.close()
            Path(final_file.name).write_bytes(output_file.read_bytes())
            return Path(final_file.name)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            logger.warning("Voiceover audio concat failed: %s", exc)
            return None
        finally:
            for audio_file in audio_files:
                audio_file.unlink(missing_ok=True)


def scheduled_voiceover_track(
    timed_clips: list[tuple[VoiceoverClipRecord, Path]],
) -> Path | None:
    settings = get_settings()
    with TemporaryDirectory(prefix="launchify-voiceover-scheduled-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        output_file = temp_dir / "voiceover.mp3"
        command = [settings.ffmpeg_binary, "-y"]
        for _clip, audio_file in timed_clips:
            command.extend(["-i", str(audio_file)])
        filter_parts: list[str] = []
        mix_inputs: list[str] = []
        for index, (clip, _audio_file) in enumerate(timed_clips):
            input_index = index
            delay_ms = max(0, round(clip.start * 1000))
            filter_parts.append(f"[{input_index}:a]adelay={delay_ms}|{delay_ms}[a{input_index}]")
            mix_inputs.append(f"[a{input_index}]")
        filter_parts.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0[outa]")
        command.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            "[outa]",
            "-c:a",
            "libmp3lame",
            str(output_file),
        ])
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=settings.ffmpeg_timeout_seconds)
            final_file = NamedTemporaryFile(delete=False, suffix=".mp3")
            final_file.close()
            Path(final_file.name).write_bytes(output_file.read_bytes())
            return Path(final_file.name)
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            logger.warning("Voiceover scheduled mix failed: %s", exc)
            return None
        finally:
            for _clip, audio_file in timed_clips:
                audio_file.unlink(missing_ok=True)


def normalized_voice_line(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."


def estimated_voice_duration_seconds(text: str) -> float:
    words = max(1, len(text.split()))
    return round(max(2.8, min(10.0, words / 2.6)), 2)
