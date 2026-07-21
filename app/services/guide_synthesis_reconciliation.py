from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from app.models.projects import ArticleStepRecord, GuideRecord, GuideStepRecord, LaunchScriptRecord, LaunchScriptScene, RecordingSessionRecord, SessionEventRecord, VisualSceneAnalysisRecord
from app.services.action_classifier import event_action_class
from app.services.editorial_labels import (
    canonical_highlight_label as editorial_highlight_label,
    canonical_step_title as editorial_step_title,
    completed_selection_target as editorial_completed_selection_target,
    contextual_on_screen_text as editorial_contextual_on_screen_text,
    specific_target_label as editorial_specific_target_label,
    sentence_like as editorial_sentence_like,
)
from app.services.guide_reconciliation import finalized_guide_steps
from app.services.selection_disambiguation import disambiguated_guide_steps
from app.services.visual_analysis import analysis_map

if TYPE_CHECKING:
    from app.services.guide_synthesizer import EventCluster


def reconcile_grounded_guide(
    guide: GuideRecord,
    clusters: Sequence[EventCluster],
    ranges: list[tuple[float, float]],
    visual_analyses: Sequence[VisualSceneAnalysisRecord] | None = None,
) -> GuideRecord:
    if not clusters:
        return guide
    matched_steps = matched_guide_steps(guide, visual_analyses)
    chosen_branch = selected_branch(clusters)
    steps = [
        reconciled_step(cluster, step_start, step_end, matched_steps.get(cluster.index), chosen_branch)
        for cluster, (step_start, step_end) in zip(clusters, ranges, strict=False)
    ]
    notes = list(dict.fromkeys([*guide.generation_notes, "Grounded step timing was expanded into continuous walkthrough ranges anchored to captured actions."]))
    final_steps = finalized_guide_steps(disambiguated_guide_steps(steps, analysis_map(list(visual_analyses or []))) if visual_analyses else steps)
    final_article_steps = [ArticleStepRecord(step_index=step.step_index, title=step.title, body=step.instruction) for step in final_steps]
    return guide.model_copy(update={"steps": final_steps, "article_steps": final_article_steps, "generation_notes": notes})


def launch_script_from_guide(guide: GuideRecord, min_step_duration_seconds: float) -> LaunchScriptRecord:
    scenes = [
        LaunchScriptScene(
            scene_number=step.step_index,
            purpose=step.instruction,
            spoken_line=step.narration,
            on_screen_text=step.on_screen_text,
            specific_target_label=step.specific_target_label,
            source_excerpt=step.source_excerpt or step.focus_label or step.title,
            estimated_duration_seconds=max(round(step.end - step.start, 2), min_step_duration_seconds),
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


def matched_guide_steps(
    guide: GuideRecord,
    visual_analyses: Sequence[VisualSceneAnalysisRecord] | None,
) -> dict[int, GuideStepRecord]:
    if not visual_analyses:
        return {step.step_index: step for step in guide.steps}
    enriched_steps = disambiguated_guide_steps(guide.steps, analysis_map(list(visual_analyses)))
    return {step.step_index: step for step in enriched_steps}


def reconciled_step(
    cluster: EventCluster,
    step_start: float,
    step_end: float,
    model_step: GuideStepRecord | None,
    chosen_branch: str,
) -> GuideStepRecord:
    label = canonical_step_label(cluster.event)
    action_class = event_action_class(cluster.event)
    specific_target = (model_step.specific_target_label.strip() if model_step is not None else "") or editorial_specific_target_label(
        label=label,
        action_class=action_class,
        transcript_excerpt=cluster.transcript_excerpt,
    )
    title = canonical_guide_title(cluster.event, label) or (model_step.title if model_step is not None and model_step.title.strip() else label or f"Step {cluster.index}").strip()
    return GuideStepRecord(
        step_index=cluster.index,
        title=title,
        instruction=canonical_instruction(cluster.event, label, chosen_branch, cluster.transcript_excerpt, specific_target),
        narration=canonical_narration(cluster.event, label, chosen_branch, cluster.transcript_excerpt, specific_target),
        on_screen_text=specific_target or (model_step.on_screen_text.strip() if model_step is not None else "") or canonical_on_screen_text(cluster.event, label, cluster.transcript_excerpt, action_class),
        specific_target_label=specific_target,
        start=step_start,
        end=step_end,
        event_type=cluster.event.type,
        focus_selector=cluster.event.target.selector,
        focus_label=label,
        highlight_label=specific_target or (model_step.highlight_label.strip() if model_step is not None else "") or canonical_highlight_label(cluster.event, label, cluster.transcript_excerpt, action_class),
        source_excerpt=cluster.transcript_excerpt or label,
        action_class=action_class,
    )


def build_instruction(event: SessionEventRecord, label: str) -> str:
    screen_after = event.metadata.get("screen_after", "").strip()
    canonical = event.metadata.get("canonical_label", "").strip()
    if canonical == "Continue With Google":
        return "Continue with Google to keep the existing sign-in flow moving."
    if canonical == "Select A Course":
        return "Open the course catalog and choose the learning path to continue."
    if screen_after == "difficulty_picker":
        return f"Select {label or 'the course'} to open the level picker."
    if screen_after == "course_catalog":
        return f"Complete {label or 'the sign-in step'} to reach the course catalog."
    if screen_after == "account_picker":
        return f"Continue on {label or 'the auth option'} to reach the account chooser."
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


def selected_branch(clusters: Sequence[EventCluster]) -> str:
    combined = " ".join(canonical_step_label(cluster.event).lower() for cluster in clusters)
    return "existing_account" if "continue with google" in combined or "choose an account" in combined else "generic"


def canonical_guide_title(event: SessionEventRecord, label: str) -> str:
    canonical = event.metadata.get("canonical_label", "").strip()
    if canonical and not editorial_sentence_like(canonical):
        return canonical
    return editorial_step_title(label=canonical or label, action_class=event_action_class(event), event_type=event.type, screen_after=event.metadata.get("screen_after", "").strip())


def canonical_instruction(
    event: SessionEventRecord,
    label: str,
    branch: str,
    transcript_excerpt: str,
    specific_target: str,
) -> str:
    canonical = event.metadata.get("canonical_label", "").strip()
    screen_after = event.metadata.get("screen_after", "").strip()
    if canonical == "Google Login":
        return "Click Google Login on the landing page to start authentication."
    if canonical == "Continue With Google":
        return "Continue with Google to move forward with the existing account sign-in flow." if branch == "existing_account" else "Continue with Google to move through authentication."
    if canonical == "Select A Course":
        target = editorial_completed_selection_target(specific_target_label=specific_target, canonical_label=label, transcript_excerpt=transcript_excerpt)
        return f"Choose {target} to continue into setup."
    if screen_after == "difficulty_picker":
        return "Choose the starting level before the lesson begins."
    return build_instruction(event, label)


def canonical_narration(event: SessionEventRecord, label: str, branch: str, transcript_excerpt: str, specific_target: str) -> str:
    canonical = event.metadata.get("canonical_label", "").strip()
    screen_after = event.metadata.get("screen_after", "").strip()
    if canonical == "Google Login":
        return "Click the Google login action to begin from the landing page."
    if canonical == "Continue With Google":
        return "Continue with Google to sign in with the existing account." if branch == "existing_account" else "Continue with Google to keep the sign-in flow moving."
    if canonical == "Select A Course":
        target = editorial_completed_selection_target(specific_target_label=specific_target, canonical_label=label, transcript_excerpt=transcript_excerpt)
        return f"Open {target} to continue into setup."
    if screen_after == "difficulty_picker":
        return "Choose the level that matches the learner's starting point."
    if event.type == "focus":
        return f"Review {label or 'the active screen'} before continuing."
    return build_instruction(event, label)


def canonical_on_screen_text(event: SessionEventRecord, label: str, transcript_excerpt: str, action_class: str) -> str:
    canonical = event.metadata.get("canonical_label", "").strip()
    if canonical and not editorial_sentence_like(canonical) and not generic_card_selection_label(canonical, action_class):
        return canonical
    return editorial_contextual_on_screen_text(
        label=canonical or label,
        title=canonical_guide_title(event, label),
        action_class=action_class,
        transcript_excerpt=transcript_excerpt,
    )


def canonical_highlight_label(event: SessionEventRecord, label: str, transcript_excerpt: str, action_class: str) -> str:
    canonical = event.metadata.get("canonical_label", "").strip()
    source = editorial_specific_target_label(label=canonical or label, action_class=action_class, transcript_excerpt=transcript_excerpt) or canonical or label
    return editorial_highlight_label(label=source, title=canonical_guide_title(event, label))[:48]


def generic_card_selection_label(label: str, action_class: str) -> bool:
    if action_class != "card_selection":
        return False
    normalized = " ".join(label.lower().split()).strip()
    return normalized in {"select a course", "choose a course", "select course", "choose course", "select an option", "choose an option"}


def readable_selector(selector: str) -> str:
    return " ".join(part for part in selector.replace("#", " ").replace(".", " ").replace(">", " ").strip().split() if part)[:60]


def canonical_step_label(event: SessionEventRecord) -> str:
    return event.metadata.get("canonical_label", "").strip() or event.target.label or event.target.text or readable_selector(event.target.selector)
