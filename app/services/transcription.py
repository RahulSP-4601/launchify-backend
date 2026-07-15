from __future__ import annotations

import http.client
import json
from pathlib import Path
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.models.projects import TranscriptSegment

DEEPGRAM_QUERY = "model=nova-3&smart_format=true&punctuate=true&paragraphs=true&utterances=true&detect_language=true"


def transcribe_media(file_bytes: bytes, content_type: str) -> list[TranscriptSegment]:
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise RuntimeError("Deepgram is not configured yet. Add DEEPGRAM_API_KEY to enable transcription.")

    endpoint = f"https://api.deepgram.com/v1/listen?{DEEPGRAM_QUERY}"
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
    endpoint = f"https://api.deepgram.com/v1/listen?{DEEPGRAM_QUERY}"
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
    alternative = primary_alternative(payload)
    if alternative is None:
        return []
    paragraph_segments = parse_paragraph_segments(alternative)
    if paragraph_segments:
        return paragraph_segments
    utterance_segments = parse_utterance_segments(payload)
    if utterance_segments:
        return utterance_segments
    word_segments = parse_word_groups(alternative)
    if word_segments:
        return word_segments
    transcript = str(alternative.get("transcript", "")).strip()
    duration = payload_duration_seconds(payload)
    return [TranscriptSegment(start=0.0, end=duration, text=transcript)] if transcript else []


def join_sentence_text(sentences: object) -> str:
    if not isinstance(sentences, list):
        return ""
    parts = [str(sentence.get("text", "")).strip() for sentence in sentences if isinstance(sentence, dict)]
    return " ".join(part for part in parts if part)


def primary_alternative(payload: dict[str, object]) -> dict[str, Any] | None:
    results = payload.get("results", {})
    channels = results.get("channels", []) if isinstance(results, dict) else []
    if not channels or not isinstance(channels[0], dict):
        return None
    alternatives = channels[0].get("alternatives", [])
    if not isinstance(alternatives, list) or not alternatives:
        return None
    return alternatives[0] if isinstance(alternatives[0], dict) else None


def parse_paragraph_segments(alternative: dict[str, Any]) -> list[TranscriptSegment]:
    paragraphs = alternative.get("paragraphs", {})
    para_list = paragraphs.get("paragraphs", []) if isinstance(paragraphs, dict) else []
    segments: list[TranscriptSegment] = []
    for paragraph in para_list:
        if not isinstance(paragraph, dict):
            continue
        text = (
            join_sentence_text(paragraph.get("sentences", []))
            or str(paragraph.get("text", "")).strip()
            or str(paragraph.get("paragraph", "")).strip()
        )
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=as_float(paragraph.get("start"), 0.0),
                end=as_float(paragraph.get("end"), 0.0),
                text=text,
            )
        )
    return segments


def parse_utterance_segments(payload: dict[str, object]) -> list[TranscriptSegment]:
    results = payload.get("results", {})
    utterances = results.get("utterances", []) if isinstance(results, dict) else []
    segments: list[TranscriptSegment] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        text = str(utterance.get("transcript", "")).strip()
        if not text:
            continue
        segments.append(
            TranscriptSegment(
                start=as_float(utterance.get("start"), 0.0),
                end=as_float(utterance.get("end"), 0.0),
                text=text,
            )
        )
    return segments


def parse_word_groups(alternative: dict[str, Any]) -> list[TranscriptSegment]:
    words = alternative.get("words", [])
    if not isinstance(words, list) or not words:
        return []
    groups: list[TranscriptSegment] = []
    current_words: list[str] = []
    current_start = 0.0
    current_end = 0.0
    for word in words:
        if not isinstance(word, dict):
            continue
        token = str(word.get("punctuated_word") or word.get("word") or "").strip()
        if not token:
            continue
        start = as_float(word.get("start"), current_end)
        end = as_float(word.get("end"), start)
        if not current_words:
            current_start = start
        current_words.append(token)
        current_end = end
        if len(current_words) >= 40 or token.endswith((".", "!", "?")):
            groups.append(TranscriptSegment(start=current_start, end=current_end, text=" ".join(current_words).strip()))
            current_words = []
    if current_words:
        groups.append(TranscriptSegment(start=current_start, end=current_end, text=" ".join(current_words).strip()))
    return groups


def payload_duration_seconds(payload: dict[str, object]) -> float:
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        return 0.0
    return as_float(metadata.get("duration"), 0.0)


def as_float(value: object, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback
