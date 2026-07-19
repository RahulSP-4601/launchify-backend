from __future__ import annotations

import math
import re
from dataclasses import dataclass

from app.models.projects import LaunchScriptRecord, LaunchScriptScene, ProjectRecord, TranscriptSegment
from app.services.inference_step_semantics import semantic_merge_steps

ACTION_HINTS = frozenset({
    "click",
    "continue",
    "course",
    "create",
    "dashboard",
    "enter",
    "explore",
    "launch",
    "learn",
    "log",
    "login",
    "open",
    "password",
    "profile",
    "search",
    "select",
    "sign",
    "start",
    "submit",
    "type",
})
FILLER_WORDS = frozenset({"actually", "basically", "just", "kind", "like", "really", "simply", "sort", "that", "then", "you"})
GAP_SPLIT_SECONDS = 1.1
MIN_STEP_SECONDS = 1.2
MAX_STEP_SECONDS = 4.8
MAX_STEPS = 12
FOCUSED_MAX_STEPS = 6
ABSOLUTE_MAX_STEP_SECONDS = 5.4
MAX_REBALANCED_STEP_SECONDS = 5.6


@dataclass(frozen=True)
class InferenceStep:
    start: float
    end: float
    text: str
def build_inference_script(
    project: ProjectRecord,
    transcript: list[TranscriptSegment],
) -> tuple[LaunchScriptRecord, list[tuple[float, float]]]:
    steps = focused_steps(transcript)
    scenes = [
        LaunchScriptScene(
            scene_number=index,
            purpose=f"Capture the product action around {step_title(step.text).lower()}.",
            spoken_line=step.text,
            on_screen_text=step_title(step.text),
            source_excerpt=step.text,
            estimated_duration_seconds=round(max(step.end - step.start, MIN_STEP_SECONDS), 2),
        )
        for index, step in enumerate(steps, start=1)
    ]
    script = LaunchScriptRecord(
        hook=f"{project.product_name} grounded walkthrough",
        summary=f"Step-by-step inferred walkthrough for {project.product_name}.",
        title_options=[f"{project.product_name} walkthrough"],
        scenes=scenes,
        cta="Turn rough recordings into polished product walkthroughs.",
        notes=["Manual-upload inference uses short transcript-aligned action steps before grounded guide synthesis."],
    )
    return script, [(step.start, step.end) for step in steps]

def focused_steps(transcript: list[TranscriptSegment]) -> list[InferenceStep]:
    steps = bounded_steps(normalize_step_durations(transcript_steps(transcript)))
    compacted = prune_noise_steps(steps)
    compacted = semantic_merge_steps(compacted)
    action_focused = prioritize_action_steps(compacted)
    if coarse_transcript(transcript) and len(compacted) > FOCUSED_MAX_STEPS:
        return coverage_bounded_steps(action_focused, FOCUSED_MAX_STEPS)
    return action_focused

def transcript_steps(transcript: list[TranscriptSegment]) -> list[InferenceStep]:
    if not transcript:
        return []
    steps: list[InferenceStep] = []
    current_start = transcript[0].start
    current_end = transcript[0].end
    parts = [clean_text(transcript[0].text)]
    for index, segment in enumerate(transcript[1:], start=1):
        previous = transcript[index - 1]
        gap = max(segment.start - previous.end, 0.0)
        candidate_end = segment.end
        candidate_parts = parts + [clean_text(segment.text)]
        candidate_text = " ".join(part for part in candidate_parts if part).strip()
        candidate_duration = candidate_end - current_start
        if should_split_step(gap, candidate_duration, previous.text, segment.text, candidate_text):
            steps.append(InferenceStep(start=round(current_start, 2), end=round(current_end, 2), text=joined_text(parts)))
            current_start = segment.start
            parts = [clean_text(segment.text)]
        else:
            parts = candidate_parts
        current_end = segment.end
    steps.append(InferenceStep(start=round(current_start, 2), end=round(current_end, 2), text=joined_text(parts)))
    return [step for step in steps if step.text]


def coarse_transcript(transcript: list[TranscriptSegment]) -> bool:
    average_duration = sum(max(segment.end - segment.start, 0.0) for segment in transcript) / max(len(transcript), 1)
    return len(transcript) <= 3 or average_duration >= 8.5


def prune_noise_steps(steps: list[InferenceStep]) -> list[InferenceStep]:
    compacted: list[InferenceStep] = []
    for step in steps:
        if should_absorb_step(step) and compacted:
            compacted[-1] = merge_group([compacted[-1], step])
            continue
        compacted.append(step)
    return [step for step in compacted if not low_signal_text(step.text)]


def should_absorb_step(step: InferenceStep) -> bool:
    tokens = tokenized(step.text)
    return len(tokens) <= 2 or (action_score(step.text) == 0 and len(tokens) <= 5)


def low_signal_text(text: str) -> bool:
    normalized = " ".join(tokenized(text))
    return not normalized or normalized in {"first", "officially there are five", "there are five"}


def prioritize_action_steps(steps: list[InferenceStep]) -> list[InferenceStep]:
    action_steps = [step for step in steps if action_score(step.text) > 0]
    return action_steps if len(action_steps) >= 3 else steps

def normalize_step_durations(steps: list[InferenceStep]) -> list[InferenceStep]:
    normalized: list[InferenceStep] = []
    for step in steps:
        normalized.extend(split_step_by_duration(step))
    return normalized


def split_step_by_duration(step: InferenceStep) -> list[InferenceStep]:
    duration = max(step.end - step.start, 0.0)
    if duration <= MAX_STEP_SECONDS:
        return [step]
    chunk_count = max(2, math.ceil(duration / MAX_STEP_SECONDS))
    parts = split_step_text(step.text, chunk_count)
    chunk_duration = duration / len(parts)
    start = step.start
    chunks: list[InferenceStep] = []
    for index, part in enumerate(parts):
        end = step.end if index == len(parts) - 1 else round(start + chunk_duration, 2)
        chunks.append(InferenceStep(start=round(start, 2), end=round(end, 2), text=part))
        start = end
    return chunks


def split_step_text(text: str, chunk_count: int) -> list[str]:
    clauses = [part.strip(" ,.") for part in re.split(r"[.!?]+|,\s+|\bthen\b|\bnext\b|\bafter that\b|\bonce\b", text, flags=re.IGNORECASE) if part.strip(" ,.")]
    if len(clauses) >= chunk_count:
        return rebalance_parts(clauses, chunk_count)
    words = text.split()
    if len(words) <= chunk_count:
        return sparse_text_chunks(text, chunk_count)
    word_chunks: list[list[str]] = [[] for _ in range(chunk_count)]
    for index, word in enumerate(words):
        word_chunks[min(index * chunk_count // len(words), chunk_count - 1)].append(word)
    return [" ".join(chunk).strip() for chunk in word_chunks if chunk]


def rebalance_parts(parts: list[str], chunk_count: int) -> list[str]:
    grouped: list[list[str]] = [[] for _ in range(chunk_count)]
    for index, part in enumerate(parts):
        grouped[min(index * chunk_count // len(parts), chunk_count - 1)].append(part)
    return [" ".join(group).strip() for group in grouped if group]

def sparse_text_chunks(text: str, chunk_count: int) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    if chunk_count == 1:
        return [clean]
    return [clean] + [fallback_chunk_label(clean) for _ in range(chunk_count - 1)]


def fallback_chunk_label(text: str) -> str:
    title = step_title(text)
    return f"Continue {title.lower()}".strip() if title != "Product Step" else "Continue walkthrough"


def should_split_step(
    gap: float,
    duration: float,
    previous_text: str,
    current_text: str,
    combined_text: str,
) -> bool:
    if gap >= GAP_SPLIT_SECONDS:
        return True
    if duration >= MAX_STEP_SECONDS:
        return True
    if duration < MIN_STEP_SECONDS:
        return False
    if action_score(current_text) > 0 and action_score(combined_text) > action_score(previous_text):
        return True
    return sentence_boundary(previous_text)


def bounded_steps(steps: list[InferenceStep]) -> list[InferenceStep]:
    if len(steps) <= MAX_STEPS:
        return steps
    while len(steps) > MAX_STEPS:
        merge_index = mergeable_step_index(steps)
        if merge_index is None:
            return rebalance_timeline_steps(steps, MAX_STEPS)
        steps = merge_adjacent_steps(steps, merge_index)
    return steps


def lowest_signal_index(steps: list[InferenceStep]) -> int:
    ranked = [
        (index, signal_score(steps[index]) + signal_score(steps[index + 1]))
        for index in range(len(steps) - 1)
    ]
    return min(ranked, key=lambda item: item[1])[0]


def mergeable_step_index(steps: list[InferenceStep]) -> int | None:
    ranked = sorted(
        (
            (index, signal_score(steps[index]) + signal_score(steps[index + 1]))
            for index in range(len(steps) - 1)
        ),
        key=lambda item: item[1],
    )
    for index, _score in ranked:
        if merged_duration(steps[index], steps[index + 1]) <= ABSOLUTE_MAX_STEP_SECONDS:
            return index
    return None


def merge_adjacent_steps(steps: list[InferenceStep], merge_index: int) -> list[InferenceStep]:
    left = steps[merge_index]
    right = steps[merge_index + 1]
    merged = InferenceStep(start=left.start, end=right.end, text=f"{left.text} {right.text}".strip())
    return steps[:merge_index] + [merged] + steps[merge_index + 2 :]


def merged_duration(left: InferenceStep, right: InferenceStep) -> float:
    return max(right.end - left.start, 0.0)


def rebalance_timeline_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    if len(steps) <= chunk_count:
        return steps
    total_duration = max(steps[-1].end - steps[0].start, MIN_STEP_SECONDS)
    bucket_duration = max(total_duration / chunk_count, MIN_STEP_SECONDS)
    groups: list[list[InferenceStep]] = [[] for _ in range(chunk_count)]
    for step in steps:
        midpoint = step.start + max(step.end - step.start, 0.0) / 2
        bucket_index = min(int((midpoint - steps[0].start) / bucket_duration), chunk_count - 1)
        groups[bucket_index].append(step)
    groups = compact_empty_groups(groups)
    groups = split_groups_on_gaps(groups)
    rebalanced: list[InferenceStep] = []
    for group in groups:
        if not group:
            continue
        rebalanced.extend(group_to_steps(group))
    return compress_rebalanced_steps(rebalanced, chunk_count)


def compact_empty_groups(groups: list[list[InferenceStep]]) -> list[list[InferenceStep]]:
    compacted = [group for group in groups if group]
    if compacted:
        return compacted
    return groups


def split_groups_on_gaps(groups: list[list[InferenceStep]]) -> list[list[InferenceStep]]:
    split_groups: list[list[InferenceStep]] = []
    for group in groups:
        current: list[InferenceStep] = []
        for step in group:
            previous = current[-1] if current else None
            if previous is not None and should_break_group(previous, step, current[0].start):
                split_groups.append(current)
                current = [step]
                continue
            current.append(step)
        if current:
            split_groups.append(current)
    return split_groups


def should_break_group(previous: InferenceStep, current: InferenceStep, group_start: float) -> bool:
    gap = max(current.start - previous.end, 0.0)
    merged_span = max(current.end - group_start, 0.0)
    return gap >= GAP_SPLIT_SECONDS or merged_span > MAX_REBALANCED_STEP_SECONDS


def group_to_steps(group: list[InferenceStep]) -> list[InferenceStep]:
    if not group:
        return []
    merged_text = " ".join(step.text for step in group).strip()
    merged = InferenceStep(start=group[0].start, end=group[-1].end, text=merged_text)
    if merged.end - merged.start <= MAX_REBALANCED_STEP_SECONDS:
        return [merged]
    return normalize_step_durations([merged])


def compress_rebalanced_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    if len(steps) <= chunk_count:
        return steps
    compressed = prune_redundant_steps(steps, chunk_count)
    if len(compressed) <= chunk_count:
        return compressed
    while len(compressed) > chunk_count:
        merge_index = mergeable_step_index(compressed)
        if merge_index is None:
            return repartition_steps(compressed, chunk_count)
        compressed = merge_adjacent_steps(compressed, merge_index)
    return compressed


def prune_redundant_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    pruned = steps[:]
    while len(pruned) > chunk_count:
        removable = lowest_redundant_index(pruned)
        if removable is None:
            break
        pruned.pop(removable)
    return pruned


def lowest_redundant_index(steps: list[InferenceStep]) -> int | None:
    candidates = [
        (index, signal_score(step))
        for index, step in enumerate(steps)
        if is_redundant_step(step)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1])[0]


def is_redundant_step(step: InferenceStep) -> bool:
    normalized = step.text.strip().lower()
    return normalized == "continue walkthrough" or normalized.startswith("continue ")


def repartition_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    if len(steps) <= chunk_count:
        return steps
    total_duration = max(steps[-1].end - steps[0].start, MIN_STEP_SECONDS)
    target_duration = max(total_duration / chunk_count, MIN_STEP_SECONDS)
    groups: list[list[InferenceStep]] = []
    current: list[InferenceStep] = []
    remaining_steps = len(steps)
    remaining_groups = chunk_count
    for step in steps:
        previous = current[-1] if current else None
        if previous is not None and should_break_group(previous, step, current[0].start):
            groups.append(current)
            current = []
            remaining_groups -= 1
        current.append(step)
        remaining_steps -= 1
        current_span = max(current[-1].end - current[0].start, 0.0)
        must_close = remaining_steps < remaining_groups
        should_close = current_span >= target_duration and remaining_groups > 1
        if must_close or should_close:
            groups.append(current)
            current = []
            remaining_groups -= 1
            if remaining_steps <= 0:
                break
    if current:
        groups.append(current)
    repartitioned = [group_to_steps(group) for group in groups if group]
    flattened = [step for group in repartitioned for step in group]
    if len(flattened) <= chunk_count:
        return flattened
    compressed = prune_redundant_steps(flattened, chunk_count)
    return merge_repartitioned_steps(compressed, chunk_count)


def merge_repartitioned_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    merged = steps[:]
    while len(merged) > chunk_count:
        merge_index = repartition_merge_index(merged)
        if merge_index is None:
            return coverage_bounded_steps(merged, chunk_count)
        merged = merge_adjacent_steps(merged, merge_index)
    return merged


def repartition_merge_index(steps: list[InferenceStep]) -> int | None:
    ranked = sorted(
        (
            (index, signal_score(steps[index]) + signal_score(steps[index + 1]))
            for index in range(len(steps) - 1)
        ),
        key=lambda item: item[1],
    )
    for index, _score in ranked:
        left = steps[index]
        right = steps[index + 1]
        gap = max(right.start - left.end, 0.0)
        if gap >= GAP_SPLIT_SECONDS:
            continue
        if merged_duration(left, right) <= MAX_REBALANCED_STEP_SECONDS:
            return index
    return None


def coverage_bounded_steps(steps: list[InferenceStep], chunk_count: int) -> list[InferenceStep]:
    if len(steps) <= chunk_count:
        return steps
    if chunk_count <= 1:
        return [max(steps, key=signal_score)]
    groups = representative_step_groups(steps, chunk_count)
    selected = [highest_signal_step(group) for group in groups if group]
    if len(selected) >= chunk_count:
        return selected[:chunk_count]
    return fill_representative_gaps(steps, selected, chunk_count)


def representative_step_groups(steps: list[InferenceStep], chunk_count: int) -> list[list[InferenceStep]]:
    total_duration = max(steps[-1].end - steps[0].start, MIN_STEP_SECONDS)
    bucket_duration = max(total_duration / chunk_count, MIN_STEP_SECONDS)
    groups: list[list[InferenceStep]] = [[] for _ in range(chunk_count)]
    for step in steps:
        midpoint = step.start + max(step.end - step.start, 0.0) / 2
        bucket_index = min(int((midpoint - steps[0].start) / bucket_duration), chunk_count - 1)
        groups[bucket_index].append(step)
    return [group for group in groups if group]


def highest_signal_step(steps: list[InferenceStep]) -> InferenceStep:
    return max(steps, key=signal_score)


def fill_representative_gaps(
    steps: list[InferenceStep],
    selected: list[InferenceStep],
    chunk_count: int,
) -> list[InferenceStep]:
    if len(selected) >= chunk_count:
        return selected[:chunk_count]
    chosen_keys = {(step.start, step.end, step.text) for step in selected}
    remaining = [step for step in steps if (step.start, step.end, step.text) not in chosen_keys]
    ranked = sorted(remaining, key=signal_score, reverse=True)
    filled = selected[:]
    for step in ranked:
        if len(filled) >= chunk_count:
            break
        filled.append(step)
    return sorted(filled, key=lambda step: (step.start, step.end))


def merge_group(group: list[InferenceStep]) -> InferenceStep:
    return InferenceStep(
        start=group[0].start,
        end=group[-1].end,
        text=" ".join(step.text for step in group).strip(),
    )


def signal_score(step: InferenceStep) -> float:
    return action_score(step.text) + max(step.end - step.start, MIN_STEP_SECONDS) * 0.1


def action_score(text: str) -> int:
    tokens = tokenized(text)
    return sum(1 for token in tokens if token in ACTION_HINTS)


def step_title(text: str) -> str:
    words = [word for word in tokenized(text) if word not in FILLER_WORDS]
    phrase = " ".join(words[:5]).strip()
    return phrase.title() if phrase else "Product Step"


def tokenized(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def sentence_boundary(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith((".", "!", "?")) or "," in stripped


def joined_text(parts: list[str]) -> str:
    return " ".join(part for part in parts if part).strip()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
