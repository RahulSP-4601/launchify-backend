from __future__ import annotations

import http.client
import json
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.models.projects import TranscriptSegment


def transcribe_media(file_bytes: bytes, content_type: str) -> list[TranscriptSegment]:
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise RuntimeError("Deepgram is not configured yet. Add DEEPGRAM_API_KEY to enable transcription.")

    endpoint = "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&punctuate=true"
    transcription_request = request.Request(
        endpoint,
        data=file_bytes,
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": content_type,
        },
        method="POST",
    )
    try:
        with request.urlopen(transcription_request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Deepgram transcription failed: {detail}") from exc

    return parse_segments(payload)


def transcribe_media_file(source_path: Path, content_type: str) -> list[TranscriptSegment]:
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise RuntimeError("Deepgram is not configured yet. Add DEEPGRAM_API_KEY to enable transcription.")
    endpoint = "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&punctuate=true"
    parsed = urlsplit(endpoint)
    if not parsed.hostname:
        raise RuntimeError("Deepgram URL is missing a hostname.")
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=300)
    try:
        connection.putrequest("POST", parsed.path + ("?" + parsed.query if parsed.query else ""))
        connection.putheader("Authorization", f"Token {settings.deepgram_api_key}")
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(source_path.stat().st_size))
        connection.endheaders()
        with source_path.open("rb") as file_pointer:
            while chunk := file_pointer.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        payload = response.read().decode("utf-8", errors="ignore")
        if response.status >= 400:
            raise RuntimeError(f"Deepgram transcription failed: {payload}")
        return parse_segments(json.loads(payload))
    finally:
        connection.close()


def parse_segments(payload: dict[str, object]) -> list[TranscriptSegment]:
    results = payload.get("results", {})
    channels = results.get("channels", []) if isinstance(results, dict) else []
    if not channels:
        return []
    alternatives = channels[0].get("alternatives", {}) if isinstance(channels[0], dict) else []
    alt_list = alternatives if isinstance(alternatives, list) else []
    if not alt_list:
        return []
    paragraphs = alt_list[0].get("paragraphs", {}) if isinstance(alt_list[0], dict) else {}
    para_list = paragraphs.get("paragraphs", []) if isinstance(paragraphs, dict) else []
    if para_list:
        return [
            TranscriptSegment(
                start=float(paragraph.get("start", 0.0)),
                end=float(paragraph.get("end", 0.0)),
                text=join_sentence_text(paragraph.get("sentences", [])),
            )
            for paragraph in para_list
            if join_sentence_text(paragraph.get("sentences", []))
        ]
    transcript = str(alt_list[0].get("transcript", "")).strip()
    return [TranscriptSegment(start=0.0, end=0.0, text=transcript)] if transcript else []


def join_sentence_text(sentences: object) -> str:
    if not isinstance(sentences, list):
        return ""
    parts = [str(sentence.get("text", "")).strip() for sentence in sentences if isinstance(sentence, dict)]
    return " ".join(part for part in parts if part)
