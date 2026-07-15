from __future__ import annotations

import json
from typing import Sequence
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import LaunchScriptRecord, ProjectRecord, TranscriptSegment

MIN_TRANSCRIPT_CHARACTERS = 40


def generate_launch_script(project: ProjectRecord) -> LaunchScriptRecord:
    transcript_text = combine_transcript(project.transcript)
    if len(transcript_text.strip()) < MIN_TRANSCRIPT_CHARACTERS:
        raise RuntimeError("Transcript is too short to generate a launch script.")
    payload = request_openai_rewrite(project, transcript_text)
    try:
        return LaunchScriptRecord.model_validate(payload)
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
        "response_format": {"type": "json_object"},
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
        "source_excerpt, estimated_duration_seconds."
    )


def user_prompt(project: ProjectRecord, transcript_text: str) -> str:
    return (
        f"Project name: {project.project_name}\n"
        f"Product name: {project.product_name}\n"
        f"Product description: {project.product_description or 'Not provided'}\n"
        f"Target audience: {project.target_audience or 'Not provided'}\n"
        f"Video goal: {project.video_goal}\n\n"
        "Rewrite the transcript into a sharper launch video script. Keep it concise, clearer, "
        "and more persuasive than the raw narration. Preserve factual meaning. Create 3 to 6 scenes.\n\n"
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
    content = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned an empty script response.")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenAI returned invalid script JSON.") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI returned an invalid script payload shape.")
    return parsed
