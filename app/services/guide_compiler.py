from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.models.projects import ArticleStepRecord, GuideRecord, GuideStepRecord, ProjectRecord, RecordingSessionRecord, SessionEventRecord
from app.services.action_classifier import event_action_class
from app.services.editorial_labels import canonical_highlight_label, canonical_on_screen_text, canonical_step_title, specific_target_label
from app.services.event_grounding import normalize_event_timestamp
from app.services.walkthrough_text_normalizer import normalized_step

MIN_STEP_DURATION_SECONDS = 0.8


class EventClusterLike(Protocol):
    index: int
    start: float
    end: float
    event: SessionEventRecord
    transcript_excerpt: str


def compile_guide_from_clusters(
    project: ProjectRecord,
    clusters: Sequence[Any],
    session: RecordingSessionRecord | None,
    note: str,
) -> GuideRecord:
    ranges = contextual_cluster_ranges(clusters, session)
    steps: list[GuideStepRecord] = []
    article_steps: list[ArticleStepRecord] = []
    for cluster, (step_start, step_end) in zip(clusters, ranges, strict=False):
        label = cluster.event.target.label or cluster.event.target.text or readable_selector(cluster.event.target.selector)
        action_class = event_action_class(cluster.event)
        specific_target = specific_target_label(label=label, action_class=action_class, transcript_excerpt=cluster.transcript_excerpt)
        instruction = build_instruction(cluster.event, label)
        narration = compiler_narration(cluster.transcript_excerpt, instruction)
        title = compiler_title(label, cluster.index, action_class, cluster.event.type)
        on_screen_text = specific_target or canonical_on_screen_text(label=label, title=title)
        steps.append(
            normalized_step(GuideStepRecord(
                step_index=cluster.index,
                title=title,
                instruction=instruction,
                narration=narration,
                on_screen_text=on_screen_text,
                specific_target_label=specific_target,
                start=step_start,
                end=step_end,
                event_type=cluster.event.type,
                focus_selector=cluster.event.target.selector,
                focus_label=label,
                highlight_label=(specific_target or canonical_highlight_label(label=label, title=title))[:48],
                source_excerpt=cluster.transcript_excerpt or label,
                action_class=action_class,
            ))
        )
        article_steps.append(ArticleStepRecord(step_index=cluster.index, title=title, body=instruction))
    return GuideRecord(
        title=f"{project.product_name}: grounded walkthrough",
        summary=f"A grounded walkthrough for {project.product_name} generated from recovered user actions.",
        steps=steps,
        article_steps=article_steps,
        generation_notes=[note],
    )


def compiler_title(label: str, index: int, action_class: str, event_type: str) -> str:
    return canonical_step_title(label=label, action_class=action_class, event_type=event_type) or f"Step {index}"


def compiler_narration(transcript_excerpt: str, instruction: str) -> str:
    clean = transcript_excerpt.strip()
    if len(clean) >= 24:
        return clean
    return instruction


def contextual_cluster_ranges(
    clusters: Sequence[EventClusterLike],
    session: RecordingSessionRecord | None,
) -> list[tuple[float, float]]:
    if not clusters:
        return []
    source_start, source_end = session_bounds(session, clusters)
    anchors = [cluster_anchor(cluster) for cluster in clusters]
    boundaries = [source_start]
    for index in range(len(anchors) - 1):
        boundaries.append(round((anchors[index] + anchors[index + 1]) / 2, 2))
    boundaries.append(source_end)
    ranges: list[tuple[float, float]] = []
    previous_end = source_start
    for index, cluster in enumerate(clusters):
        step_start = max(previous_end, min(boundaries[index], cluster.start))
        target_end = max(boundaries[index + 1], step_start + MIN_STEP_DURATION_SECONDS)
        step_end = min(max(target_end, step_start), source_end)
        ranges.append((round(step_start, 2), round(step_end, 2)))
        previous_end = step_end
    return ranges


def session_bounds(
    session: RecordingSessionRecord | None,
    clusters: Sequence[EventClusterLike],
) -> tuple[float, float]:
    source_start = parse_session_time(session.started_at) if session is not None else 0.0
    source_end = parse_session_time(session.ended_at) if session is not None else 0.0
    fallback_end = max(cluster.end for cluster in clusters)
    if source_end <= source_start:
        source_end = fallback_end
    source_start = min(source_start, min(cluster.start for cluster in clusters))
    return round(max(source_start, 0.0), 2), round(max(source_end, fallback_end), 2)


def cluster_anchor(cluster: EventClusterLike) -> float:
    timestamp = normalize_event_timestamp(cluster.event.timestamp)
    return round(min(max(timestamp, cluster.start), cluster.end), 2)


def parse_session_time(value: str) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


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
