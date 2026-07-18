from __future__ import annotations

import http.client
import json
import logging
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from tempfile import TemporaryDirectory
from urllib.parse import quote, urlsplit

from app.core.config import get_settings
from app.models.projects import AssetRecord, LaunchScriptRecord, VoiceoverCueRecord, VoiceoverMode, VoiceoverRecord
from app.services.storage import upload_audio_file

logger = logging.getLogger(__name__)
MAX_TTS_CHARACTERS = 1800


def build_voiceover(
    user_id: str,
    project_id: str,
    launch_script: LaunchScriptRecord,
    mode: VoiceoverMode,
) -> VoiceoverRecord:
    cues = cue_track(launch_script)
    script = " ".join(cue.text for cue in cues).strip()
    if mode == "original":
        return VoiceoverRecord(
            mode=mode,
            status="disabled",
            script=script,
            cues=cues,
            duration_seconds=round(cues[-1].end, 2) if cues else 0.0,
        )
    if not script:
        return VoiceoverRecord(mode=mode, status="disabled", script="", cues=[])
    audio_asset = synthesize_voiceover(user_id, project_id, script)
    return VoiceoverRecord(
        provider="deepgram",
        model=get_settings().deepgram_tts_model,
        mode=mode,
        status="ready" if audio_asset is not None else "script_only",
        script=script,
        cues=cues,
        audio_storage_path=audio_asset.storage_path if audio_asset is not None else "",
        duration_seconds=round(cues[-1].end, 2) if cues else 0.0,
    )


def cue_track(launch_script: LaunchScriptRecord) -> list[VoiceoverCueRecord]:
    cues: list[VoiceoverCueRecord] = []
    cursor = 0.0
    for scene in launch_script.scenes:
        text = normalized_voice_line(scene.spoken_line)
        spoken_duration = max(scene.estimated_duration_seconds, estimated_voice_duration_seconds(text))
        pause_duration = pause_padding_seconds(text)
        total_duration = spoken_duration + pause_duration
        cues.append(
            VoiceoverCueRecord(
                scene_number=scene.scene_number,
                start=round(cursor, 2),
                end=round(cursor + total_duration, 2),
                text=text,
                duration_seconds=round(total_duration, 2),
            )
        )
        cursor += total_duration
    return [cue for cue in cues if cue.text]


def synthesize_voiceover(user_id: str, project_id: str, script: str) -> AssetRecord | None:
    settings = get_settings()
    if not settings.deepgram_api_key:
        return None
    audio_file = request_tts_audio(script, settings.deepgram_tts_model)
    if audio_file is None:
        return None
    try:
        return upload_audio_file(user_id, project_id, "voiceover.mp3", audio_file)
    finally:
        audio_file.unlink(missing_ok=True)


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


def normalized_voice_line(value: str) -> str:
    cleaned = " ".join(value.split()).strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.endswith((".", "!", "?")) else f"{cleaned}."


def estimated_voice_duration_seconds(text: str) -> float:
    words = max(1, len(text.split()))
    return round(max(2.8, min(10.0, words / 2.6)), 2)


def pause_padding_seconds(text: str) -> float:
    if text.endswith(("!", "?")):
        return 0.28
    return 0.18
