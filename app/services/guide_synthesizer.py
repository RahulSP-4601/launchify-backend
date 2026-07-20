from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence
from urllib import error, request

from pydantic import ValidationError

from app.core.config import get_settings
from app.models.projects import (
    ArticleStepRecord,
    GuideRecord,
    GuideStepRecord,
    LaunchScriptRecord,
    LaunchScriptScene,
    ProjectRecord,
    RecordingSessionRecord,
    SessionEventRecord,
    TranscriptSegment,
)
from app.services.action_classifier import event_action_class
from app.services.event_grounding import normalize_event_timestamp
from app.services.guide_compiler import compile_guide_from_clusters
from app.services.guide_event_normalizer import MEANINGFUL_EVENT_TYPES, normalize_events
from app.services.guide_recovery import needs_cluster_recovery, recovered_step_seeds
from app.services.guide_timing_ranges import contextual_step_ranges
from app.services.script_writer import describe_transport_error, extract_message_content, openai_headers
from app.services.walkthrough_guardrails import guide_is_under_grounded, recording_duration_seconds
CLUSTER_GAP_SECONDS = 2.5
MIN_STEP_DURATION_SECONDS = 0.8
MAX_STEP_COUNT = 12
LEAD_IN_SECONDS = 0.22
LEAD_OUT_SECONDS = 0.5


@dataclass(frozen=True)
class EventCluster:
    index: int
    start: float
    end: float
    event: SessionEventRecord
    transcript_excerpt: str


def synthesize_grounded_guide(
    project: ProjectRecord,
    transcript: Sequence[TranscriptSegment],
) -> tuple[GuideRecord, LaunchScriptRecord]:
    session = require_session(project.recording_session)
    normalized_events = normalize_events(session.events, transcript)
    if not normalized_events:
        raise RuntimeError("Grounded session capture did not include any actionable events.")
    clusters = cluster_events(normalized_events, transcript)
    if needs_cluster_recovery(normalized_events, transcript):
        recovered = recovered_clusters(normalized_events, transcript)
        if len(recovered) > len(clusters):
            clusters = recovered
    guide = request_grounded_guide(project, transcript, session, clusters)
    launch_script = launch_script_from_guide(guide)
    return guide, launch_script


def require_session(recording_session: RecordingSessionRecord | None) -> RecordingSessionRecord:
    if recording_session is None or not recording_session.events:
        raise RuntimeError("Recording session with captured events is required for grounded guide synthesis.")
    return recording_session


def cluster_events(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
) -> list[EventCluster]:
    if not events:
        return []
    actionable = [event for event in events if event.type in MEANINGFUL_EVENT_TYPES or event.type == "input"]
    clusters: list[EventCluster] = []
    for index, event in enumerate(actionable[:MAX_STEP_COUNT], start=1):
        next_event = actionable[index] if index < len(actionable) else None
        start = max(normalize_event_timestamp(event.timestamp) - LEAD_IN_SECONDS, 0.0)
        next_event_time = normalize_event_timestamp(next_event.timestamp) if next_event else None
        candidate_end = (next_event_time - 0.18) if next_event_time is not None else (start + CLUSTER_GAP_SECONDS)
        transcript_end = transcript_window_end(transcript, start, candidate_end)
        end = min(
            max(start + MIN_STEP_DURATION_SECONDS, transcript_end or candidate_end or start + CLUSTER_GAP_SECONDS),
            max(start + MIN_STEP_DURATION_SECONDS, start + CLUSTER_GAP_SECONDS),
        )
        transcript_excerpt = excerpt_for_window(transcript, max(0.0, start - 0.6), end + 0.6)
        clusters.append(
            EventCluster(
                index=index,
                start=round(start, 2),
                end=round(max(end, start + MIN_STEP_DURATION_SECONDS), 2),
                event=event,
                transcript_excerpt=transcript_excerpt,
            )
        )
    return clusters


def recovered_clusters(
    events: Sequence[SessionEventRecord],
    transcript: Sequence[TranscriptSegment],
) -> list[EventCluster]:
    seeds = recovered_step_seeds(transcript)[:MAX_STEP_COUNT]
    if not seeds or not events:
        return []
    available_events = list(events)
    clusters: list[EventCluster] = []
    for index, seed in enumerate(seeds, start=1):
        matched = nearest_event_for_seed(seed.start, seed.end, available_events)
        if matched is None:
            continue
        available_events.remove(matched)
        clusters.append(
            EventCluster(
                index=index,
                start=round(seed.start, 2),
                end=round(max(seed.end, seed.start + MIN_STEP_DURATION_SECONDS), 2),
                event=matched,
                transcript_excerpt=seed.transcript_excerpt,
            )
        )
    return clusters


def nearest_event_for_seed(
    start: float,
    end: float,
    events: Sequence[SessionEventRecord],
) -> SessionEventRecord | None:
    if not events:
        return None
    midpoint = (start + end) / 2
    return min(events, key=lambda event: event_seed_distance(event, midpoint))


def event_seed_distance(event: SessionEventRecord, midpoint: float) -> float:
    return abs(normalize_event_timestamp(event.timestamp) - midpoint)
def excerpt_for_window(transcript: Sequence[TranscriptSegment], start: float, end: float) -> str:
    parts = [segment.text.strip() for segment in transcript if segment.end >= start and segment.start <= end and segment.text.strip()]
    return " ".join(parts)

def transcript_window_end(
    transcript: Sequence[TranscriptSegment],
    start: float,
    candidate_end: float | None,
) -> float | None:
    overlapping = [
        segment
        for segment in transcript
        if segment.end >= start and (candidate_end is None or segment.start <= candidate_end)
    ]
    if not overlapping:
        return candidate_end
    return min(overlapping[-1].end + LEAD_OUT_SECONDS, (candidate_end or overlapping[-1].end + LEAD_OUT_SECONDS))

def request_grounded_guide(
    project: ProjectRecord,
    transcript: Sequence[TranscriptSegment],
    session: RecordingSessionRecord,
    clusters: Sequence[EventCluster],
) -> GuideRecord:
    settings = get_settings()
    if not settings.openai_api_key:
        return fallback_guide(project, clusters, session)
    request_payload = {
        "model": settings.openai_script_model,
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "grounded_guide",
                "strict": True,
                "schema": guide_schema(),
            },
        },
        "messages": [
            {"role": "system", "content": grounded_system_prompt()},
            {"role": "user", "content": grounded_user_prompt(project, transcript, session, clusters)},
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
        raise RuntimeError(f"OpenAI grounded guide generation failed: {detail}") from exc
    except (error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI grounded guide generation failed: {describe_transport_error(exc)}") from exc
    try:
        guide = GuideRecord.model_validate(parse_openai_guide_payload(payload))
    except ValidationError as exc:
        raise RuntimeError("OpenAI returned an invalid grounded guide structure.") from exc
    reconciled = reconcile_grounded_guide(guide, clusters, session)
    if guide_is_under_grounded(reconciled, recording_duration_seconds(session, transcript)):
        return fallback_guide(project, clusters, session)
    return reconciled

def grounded_system_prompt() -> str:
    return (
        "You convert captured product walkthrough sessions into grounded launch-video guides. "
        "The event log is truth. The transcript is supporting context. "
        "Never invent steps, clicks, or UI elements that are not present in the event log. "
        "Merge repetitive adjacent actions into one clean step when appropriate. "
        "Write concise, polished narration and launch-ready on-screen copy. "
        "Return only valid JSON matching the schema."
    )


def grounded_user_prompt(
    project: ProjectRecord,
    transcript: Sequence[TranscriptSegment],
    session: RecordingSessionRecord,
    clusters: Sequence[EventCluster],
) -> str:
    transcript_text = "\n".join(
        f"- {segment.start:.2f}s to {segment.end:.2f}s: {segment.text.strip()}"
        for segment in transcript
        if segment.text.strip()
    )
    cluster_text = "\n".join(
        (
            f"{cluster.index}. {cluster.start:.2f}s to {cluster.end:.2f}s | "
            f"type={cluster.event.type} | selector={cluster.event.target.selector or 'n/a'} | "
            f"label={cluster.event.target.label or cluster.event.target.text or 'n/a'} | "
            f"value={cluster.event.value or 'n/a'} | transcript={cluster.transcript_excerpt or 'n/a'}"
        )
        for cluster in clusters
    )
    return (
        f"Project name: {project.project_name}\n"
        f"Product name: {project.product_name}\n"
        f"Product description: {project.product_description or 'Not provided'}\n"
        f"Target audience: {project.target_audience or 'Not provided'}\n"
        f"Video goal: {project.video_goal}\n"
        f"Viewport: {session.viewport_width}x{session.viewport_height}\n"
        f"Page title: {session.page_title or 'Not provided'}\n"
        f"Page url: {session.page_url or 'Not provided'}\n\n"
        "Create a grounded guide and launch-ready narration from these captured action clusters.\n"
        "Use 4 to 12 steps. Keep the order faithful to the events.\n"
        "Every step must keep the supplied start and end timestamps. Do not reorder them.\n"
        "Highlight labels should be short. On-screen text should feel premium and concise.\n\n"
        f"Transcript:\n{transcript_text or 'No speech captured.'}\n\n"
        f"Captured action clusters:\n{cluster_text}"
    )


def parse_openai_guide_payload(payload: dict[str, object]) -> dict[str, object]:
    choices = payload.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("OpenAI did not return any grounded guide choices.")
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    content = extract_message_content(message)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("OpenAI returned an empty grounded guide response.")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI returned an invalid grounded guide payload shape.")
    return parsed


def guide_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "source": {"type": "string"},
            "steps": {"type": "array", "minItems": 1, "maxItems": MAX_STEP_COUNT, "items": guide_step_schema()},
            "article_steps": {"type": "array", "items": article_step_schema()},
            "generation_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "summary", "source", "steps", "article_steps", "generation_notes"],
    }


def guide_step_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": guide_step_properties(),
        "required": list(guide_step_properties().keys()),
    }


def guide_step_properties() -> dict[str, object]:
    return {
        "step_index": {"type": "integer"},
        "title": {"type": "string"},
        "instruction": {"type": "string"},
        "narration": {"type": "string"},
        "on_screen_text": {"type": "string"},
        "start": {"type": "number"},
        "end": {"type": "number"},
        "event_type": {"type": "string"},
        "focus_selector": {"type": "string"},
        "focus_label": {"type": "string"},
        "highlight_label": {"type": "string"},
        "source_excerpt": {"type": "string"},
    }


def article_step_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"step_index": {"type": "integer"}, "title": {"type": "string"}, "body": {"type": "string"}},
        "required": ["step_index", "title", "body"],
    }


def fallback_guide(
    project: ProjectRecord,
    clusters: Sequence[EventCluster],
    session: RecordingSessionRecord | None = None,
) -> GuideRecord:
    return compile_guide_from_clusters(project, clusters, session, "Fallback guide used because OpenAI grounded synthesis was unavailable.")

def reconcile_grounded_guide(
    guide: GuideRecord,
    clusters: Sequence[EventCluster],
    session: RecordingSessionRecord | None = None,
) -> GuideRecord:
    if not clusters:
        return guide
    matched_steps = {step.step_index: step for step in guide.steps}
    ranges = contextual_cluster_ranges(clusters, session)
    steps: list[GuideStepRecord] = []
    article_steps: list[ArticleStepRecord] = []
    for cluster, (step_start, step_end) in zip(clusters, ranges, strict=False):
        model_step = matched_steps.get(cluster.index)
        label = cluster.event.target.label or cluster.event.target.text or readable_selector(cluster.event.target.selector)
        instruction = (model_step.instruction if model_step is not None and model_step.instruction.strip() else build_instruction(cluster.event, label)).strip()
        narration = (model_step.narration if model_step is not None and model_step.narration.strip() else cluster.transcript_excerpt or instruction).strip()
        on_screen_text = (model_step.on_screen_text if model_step is not None and model_step.on_screen_text.strip() else label or instruction).strip()
        title = (model_step.title if model_step is not None and model_step.title.strip() else label or f"Step {cluster.index}").strip()
        highlight = (model_step.highlight_label if model_step is not None and model_step.highlight_label.strip() else label[:48]).strip()
        steps.append(GuideStepRecord(
            step_index=cluster.index,
            title=title,
            instruction=instruction,
            narration=narration,
            on_screen_text=on_screen_text,
            start=step_start,
            end=step_end,
            event_type=cluster.event.type,
            focus_selector=cluster.event.target.selector,
            focus_label=label,
            highlight_label=highlight,
            source_excerpt=cluster.transcript_excerpt or label,
            action_class=event_action_class(cluster.event),
        ))
        article_steps.append(ArticleStepRecord(
            step_index=cluster.index,
            title=title,
            body=instruction,
        ))
    notes = list(
        dict.fromkeys(
            [
                *guide.generation_notes,
                "Grounded step timing was expanded into continuous walkthrough ranges anchored to captured actions.",
            ]
        )
    )
    return guide.model_copy(update={"steps": steps, "article_steps": article_steps, "generation_notes": notes})


def contextual_cluster_ranges(
    clusters: Sequence[EventCluster],
    session: RecordingSessionRecord | None,
) -> list[tuple[float, float]]:
    return contextual_step_ranges(clusters, session)


def build_instruction(event: SessionEventRecord, label: str) -> str:
    if event.type == "input":
        entered = f" and enter '{event.value}'" if event.value else ""
        return f"Focus on {label or 'the input'}{entered}."
    if event.type in {"keypress", "keydown"}:
        return f"Confirm the action on {label or 'the active control'}."
    if event.type == "navigation":
        return f"Navigate to {label or 'the next view'}."
    if event.type == "focus":
        return f"Move attention to {label or 'the active field'}."
    return f"Click {label or 'the highlighted control'}."


def readable_selector(selector: str) -> str:
    clean = selector.replace("#", " ").replace(".", " ").replace(">", " ").strip()
    return " ".join(part for part in clean.split() if part)[:60]


def launch_script_from_guide(guide: GuideRecord) -> LaunchScriptRecord:
    scenes = [
        LaunchScriptScene(
            scene_number=step.step_index,
            purpose=step.instruction,
            spoken_line=step.narration,
            on_screen_text=step.on_screen_text,
            source_excerpt=step.source_excerpt or step.focus_label or step.title,
            estimated_duration_seconds=max(round(step.end - step.start, 2), MIN_STEP_DURATION_SECONDS),
        )
        for step in guide.steps
    ]
    return LaunchScriptRecord(
        hook=guide.title,
        summary=guide.summary,
        title_options=[guide.title, f"{guide.title} in minutes", f"How {guide.title.lower()}"][:3],
        scenes=scenes,
        cta="Turn rough recordings into polished launch videos.",
        notes=guide.generation_notes,
    )
