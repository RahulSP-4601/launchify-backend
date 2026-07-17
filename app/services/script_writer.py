from __future__ import annotations

import json
import re
from typing import Any, Sequence
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import LaunchScriptRecord, ProjectRecord, TranscriptSegment
from app.services.story_pacing import shape_launch_story

MIN_TRANSCRIPT_CHARACTERS = 40
MIN_SCENE_COUNT = 3
MAX_SCENE_COUNT = 6
FILLER_PHRASES = (
    "kind of",
    "sort of",
    "basically",
    "actually",
    "simply",
    "just",
    "you know",
    "i mean",
)


def generate_launch_script(project: ProjectRecord) -> LaunchScriptRecord:
    transcript_text = combine_transcript(project.transcript)
    if len(transcript_text.strip()) < MIN_TRANSCRIPT_CHARACTERS:
        raise RuntimeError("Transcript is too short to generate a launch script.")
    payload = request_openai_rewrite(project, transcript_text)
    try:
        return LaunchScriptRecord.model_validate(normalize_launch_script_payload(payload, transcript_text))
    except ValidationError as exc:
        raise RuntimeError("OpenAI returned a launch script with an invalid structure.") from exc


def combine_transcript(transcript: Sequence[TranscriptSegment]) -> str:
    return " ".join(segment.text.strip() for segment in transcript if segment.text.strip())


def request_openai_rewrite(project: ProjectRecord, transcript_text: str) -> dict[str, object]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise RuntimeError("OpenAI is not configured yet. Add OPENAI_API_KEY to enable script generation.")
    request_payload = {
        "model": settings.openai_script_model,
        "temperature": 0.4,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "launch_script",
                "strict": True,
                "schema": launch_script_schema(),
            },
        },
        "messages": [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_prompt(project, transcript_text)},
        ],
    }
    api_request = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers=openai_headers(settings.openai_api_key),
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI script generation failed: {detail}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI script generation failed: {describe_transport_error(exc)}") from exc
    return parse_openai_payload(payload)


def system_prompt() -> str:
    return (
        "You rewrite rough product walkthrough transcripts into strong launch-video scripts. "
        "Always respond as valid JSON with keys: hook, summary, title_options, scenes, cta, notes. "
        "Each scene must include scene_number, purpose, spoken_line, on_screen_text, "
        "source_excerpt, estimated_duration_seconds. Create 3 to 6 scenes only. Aim for a 30 to 35 second final video. "
        "Choose only the highest-signal product moments, skip dead time, and make the flow feel like a polished storyboard. "
        "Remove filler words, keep sentences crisp, preserve factual meaning, and make each scene feel like one clean product step. "
        "Write like a polished product marketer or customer educator, not a generic AI assistant. "
        "Hooks should be concrete and benefit-led. CTAs should feel professional, specific, and short. "
        "Keep every field plain JSON without markdown fences or commentary."
    )


def user_prompt(project: ProjectRecord, transcript_text: str) -> str:
    return (
        f"Project name: {project.project_name}\n"
        f"Product name: {project.product_name}\n"
        f"Product description: {project.product_description or 'Not provided'}\n"
        f"Target audience: {project.target_audience or 'Not provided'}\n"
        f"Video goal: {project.video_goal}\n\n"
        "Rewrite the transcript into a sharper launch video script. Keep it concise, clearer, "
        "and more persuasive than the raw narration. Preserve factual meaning. Create 3 to 6 scenes.\n"
        "Target a final runtime of roughly 30 to 35 seconds. Prioritize the strongest product actions, user wins, "
        "and visual moments. Remove setup chatter, hesitation, and repeated explanation.\n"
        "Each scene should cover exactly one idea, workflow step, or product benefit. Avoid generic hype.\n\n"
        f"Transcript:\n{transcript_text}"
    )


def openai_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def describe_transport_error(exc: error.URLError | TimeoutError) -> str:
    if isinstance(exc, error.URLError):
        reason = exc.reason
        if isinstance(reason, str) and reason.strip():
            return reason
        return str(reason or exc)
    return str(exc)


def parse_openai_payload(payload: dict[str, object]) -> dict[str, object]:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI did not return any script choices.")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = extract_message_content(message)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned an empty script response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI returned invalid script JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI returned an invalid script payload shape.")
    return parsed


def extract_message_content(message: object) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text_value = item.get("text", "")
        if isinstance(text_value, str) and text_value.strip():
            parts.append(text_value)
    return "".join(parts)


def launch_script_schema() -> dict[str, object]:
    scene_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "scene_number": {"type": "integer"},
            "purpose": {"type": "string"},
            "spoken_line": {"type": "string"},
            "on_screen_text": {"type": "string"},
            "source_excerpt": {"type": "string"},
            "estimated_duration_seconds": {"type": "number"},
        },
        "required": [
            "scene_number",
            "purpose",
            "spoken_line",
            "on_screen_text",
            "source_excerpt",
            "estimated_duration_seconds",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "hook": {"type": "string"},
            "summary": {"type": "string"},
            "title_options": {"type": "array", "items": {"type": "string"}},
            "scenes": {
                "type": "array",
                "minItems": MIN_SCENE_COUNT,
                "maxItems": MAX_SCENE_COUNT,
                "items": scene_schema,
            },
            "cta": {"type": "string"},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["hook", "summary", "title_options", "scenes", "cta", "notes"],
    }


def normalize_launch_script_payload(payload: dict[str, object], transcript_text: str) -> dict[str, object]:
    normalized: dict[str, object] = {
        "hook": polished_line(as_text(payload.get("hook")) or fallback_hook(payload)),
        "summary": polished_line(as_text(payload.get("summary")) or fallback_summary(transcript_text)),
        "title_options": polished_titles(as_text_list(payload.get("title_options")) or build_title_options(payload, transcript_text)),
        "cta": polished_cta(as_text(payload.get("cta")) or "See how this workflow looks in your product."),
        "notes": as_text_list(payload.get("notes")),
    }
    normalized["scenes"] = shape_launch_story(normalize_scenes(payload.get("scenes"), transcript_text))
    return normalized


def normalize_scenes(value: object, transcript_text: str) -> list[dict[str, object]]:
    raw_scenes = value if isinstance(value, list) else []
    normalized = [normalize_scene_item(index, item) for index, item in enumerate(raw_scenes, start=1)]
    valid_scenes = [scene for scene in normalized if scene is not None]
    if len(valid_scenes) >= MIN_SCENE_COUNT:
        return valid_scenes[:MAX_SCENE_COUNT]
    return fallback_scenes(transcript_text)


def normalize_scene_item(index: int, value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    spoken_line = as_text(
        value.get("spoken_line")
        or value.get("voiceover")
        or value.get("narration")
        or value.get("script")
        or value.get("copy")
    )
    source_excerpt = as_text(value.get("source_excerpt") or value.get("excerpt")) or spoken_line
    on_screen_text = as_text(value.get("on_screen_text") or value.get("screen_text") or value.get("title")) or spoken_line
    purpose = as_text(value.get("purpose") or value.get("goal") or value.get("intent")) or "Guide the viewer through the product value."
    if not spoken_line:
        return None
    return {
        "scene_number": as_int(value.get("scene_number"), index),
        "purpose": polished_line(purpose),
        "spoken_line": polished_line(spoken_line),
        "on_screen_text": polished_screen_text(on_screen_text),
        "source_excerpt": polished_excerpt(source_excerpt),
        "estimated_duration_seconds": as_float(
            value.get("estimated_duration_seconds"),
            estimate_scene_duration_seconds(spoken_line),
        ),
    }


def fallback_scenes(transcript_text: str) -> list[dict[str, object]]:
    chunks = split_transcript_into_scene_chunks(transcript_text, MIN_SCENE_COUNT)
    scenes: list[dict[str, object]] = []
    for index, chunk in enumerate(chunks[:MAX_SCENE_COUNT], start=1):
        scenes.append(
            {
                "scene_number": index,
                "purpose": fallback_scene_purpose(index, len(chunks)),
                "spoken_line": polished_line(chunk),
                "on_screen_text": polished_screen_text(chunk[:120]),
                "source_excerpt": polished_excerpt(chunk[:220]),
                "estimated_duration_seconds": estimate_scene_duration_seconds(chunk),
            }
        )
    return scenes


def split_transcript_into_scene_chunks(transcript_text: str, target_chunks: int) -> list[str]:
    sentences = split_sentences(transcript_text)
    if not sentences:
        cleaned = transcript_text.strip()
        return split_text_evenly(cleaned, target_chunks) if cleaned else []
    if len(sentences) < target_chunks:
        return split_text_evenly(" ".join(sentences).strip(), target_chunks)
    chunk_size = max(1, -(-len(sentences) // target_chunks))
    chunks: list[str] = []
    for start in range(0, len(sentences), chunk_size):
        chunk = " ".join(sentences[start : start + chunk_size]).strip()
        if chunk:
            chunks.append(chunk)
    if len(chunks) > MAX_SCENE_COUNT:
        overflow = " ".join(chunks[MAX_SCENE_COUNT - 1 :]).strip()
        chunks = chunks[: MAX_SCENE_COUNT - 1] + ([overflow] if overflow else [])
    if len(chunks) < target_chunks:
        return split_text_evenly(" ".join(chunks).strip(), target_chunks)
    return chunks[:MAX_SCENE_COUNT]


def split_sentences(transcript_text: str) -> list[str]:
    parts = [part.strip() for part in transcript_text.replace("\n", " ").split(".")]
    return [f"{part}." if not part.endswith((".", "!", "?")) else part for part in parts if part]


def split_text_evenly(text: str, target_chunks: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunk_size = max(1, -(-len(words) // target_chunks))
    chunks = [" ".join(words[start : start + chunk_size]).strip() for start in range(0, len(words), chunk_size)]
    chunks = [chunk for chunk in chunks if chunk]
    while len(chunks) < target_chunks and len(chunks) < MAX_SCENE_COUNT:
        source = chunks[-1] if chunks else text.strip()
        chunks.append(source)
    if len(chunks) > MAX_SCENE_COUNT:
        overflow = " ".join(chunks[MAX_SCENE_COUNT - 1 :]).strip()
        chunks = chunks[: MAX_SCENE_COUNT - 1] + ([overflow] if overflow else [])
    return chunks[:MAX_SCENE_COUNT]


def fallback_scene_purpose(index: int, total_scenes: int) -> str:
    if index == 1:
        return "Hook the viewer with the core product problem and promise."
    if index == total_scenes:
        return "Close with the value summary and a clear call to action."
    return "Advance the walkthrough with the next product benefit."


def build_title_options(payload: dict[str, object], transcript_text: str) -> list[str]:
    candidates = [
        as_text(payload.get("hook")),
        first_sentence(transcript_text),
        "Launch-ready product walkthrough",
    ]
    titles = [candidate[:80] for candidate in candidates if candidate]
    unique_titles: list[str] = []
    for title in titles:
        if title not in unique_titles:
            unique_titles.append(title)
    return unique_titles[:3]


def fallback_hook(payload: dict[str, object]) -> str:
    return as_text(payload.get("summary")) or "Show the product value clearly from the very first seconds."


def fallback_summary(transcript_text: str) -> str:
    return first_sentence(transcript_text) or "A concise launch script generated from the uploaded walkthrough."


def first_sentence(text: str) -> str:
    sentences = split_sentences(text)
    return sentences[0] if sentences else ""


def estimate_scene_duration_seconds(text: str) -> float:
    words = max(1, len(text.split()))
    return round(max(3.5, min(14.0, words / 3.1)), 2)


def polished_titles(titles: list[str]) -> list[str]:
    polished: list[str] = []
    for title in titles:
        candidate = polished_line(title)[:80]
        if candidate and candidate not in polished:
            polished.append(candidate)
    return polished[:3]


def polished_cta(value: str) -> str:
    candidate = polished_line(value)
    if not candidate:
        return "See how this workflow looks in your product."
    if len(candidate.split()) > 12:
        candidate = " ".join(candidate.split()[:12]).strip()
    if not candidate.endswith((".", "!", "?")):
        candidate = f"{candidate}."
    return candidate


def polished_screen_text(value: str) -> str:
    return trimmed_sentence(polished_line(value), 90)


def polished_excerpt(value: str) -> str:
    return trimmed_sentence(collapse_whitespace(value), 180)


def polished_line(value: str) -> str:
    cleaned = collapse_whitespace(value)
    cleaned = remove_fillers(cleaned)
    cleaned = dedupe_phrase_runs(cleaned)
    cleaned = cleaned.strip(" ,.;:-")
    if not cleaned:
        return ""
    cleaned = cleaned[0].upper() + cleaned[1:]
    return trimmed_sentence(cleaned, 220)


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def remove_fillers(value: str) -> str:
    cleaned = value
    for filler in FILLER_PHRASES:
        cleaned = re.sub(rf"\b{re.escape(filler)}\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    return collapse_whitespace(cleaned)


def dedupe_phrase_runs(value: str) -> str:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]
    deduped: list[str] = []
    seen_normalized: set[str] = set()
    for sentence in sentences:
        normalized = sentence.lower().strip(".!? ")
        if normalized and normalized not in seen_normalized:
            deduped.append(sentence)
            seen_normalized.add(normalized)
    return " ".join(deduped) if deduped else value


def trimmed_sentence(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    truncated = value[:limit].rsplit(" ", 1)[0].strip()
    return truncated or value[:limit].strip()


def as_text(value: object) -> str:
    return str(value).strip() if isinstance(value, str) else ""


def as_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def as_int(value: object, fallback: int) -> int:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return fallback
    return fallback


def as_float(value: object, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback
