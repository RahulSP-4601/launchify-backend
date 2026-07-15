from __future__ import annotations

import re

from app.models.projects import EditPlanCaption, TemplateConfigRecord, TranscriptSegment

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
CAPTION_MAX_CHARACTERS = 64


def build_caption_track(
    transcript: list[TranscriptSegment],
    start: float,
    end: float,
    template_config: TemplateConfigRecord | None,
) -> list[EditPlanCaption]:
    if not transcript:
        return [EditPlanCaption(start=start, end=end, text="Narration begins here.", emphasis_words=[], variant="hero")]
    captions: list[EditPlanCaption] = []
    current_text: list[str] = []
    current_start = transcript[0].start
    current_end = transcript[0].end
    for segment in transcript:
        current_start, current_end, current_text = append_segment(
            captions,
            current_start,
            current_end,
            current_text,
            segment,
            template_config,
        )
    if current_text:
        captions.append(caption_record(current_start, current_end, current_text, template_config))
    return captions


def append_segment(
    captions: list[EditPlanCaption],
    current_start: float,
    current_end: float,
    current_text: list[str],
    segment: TranscriptSegment,
    template_config: TemplateConfigRecord | None,
) -> tuple[float, float, list[str]]:
    segment_text = segment.text.strip()
    candidate = " ".join([*current_text, segment_text]).strip()
    if current_text and len(candidate) > CAPTION_MAX_CHARACTERS:
        captions.append(caption_record(current_start, current_end, current_text, template_config))
        return segment.start, segment.end, [segment_text]
    return current_start, segment.end, [*current_text, segment_text]


def caption_record(
    start: float,
    end: float,
    text_parts: list[str],
    template_config: TemplateConfigRecord | None,
) -> EditPlanCaption:
    text = caption_text(" ".join(text_parts), template_config)
    return EditPlanCaption(
        start=round(start, 2),
        end=round(end, 2),
        text=text,
        emphasis_words=emphasis_words(text),
        variant=caption_variant(template_config),
    )


def emphasis_words(text: str) -> list[str]:
    tokens = [token for token in TOKEN_PATTERN.findall(text) if len(token) > 4]
    return tokens[:3]


def caption_variant(template_config: TemplateConfigRecord | None) -> str:
    if template_config is None:
        return "body"
    if template_config.caption_profile == "cinematic":
        return "hero"
    if template_config.caption_profile == "minimal":
        return "minimal"
    return "body"


def caption_text(text: str, template_config: TemplateConfigRecord | None) -> str:
    normalized = " ".join(text.split())
    if template_config is None or template_config.caption_profile == "minimal":
        return normalized
    return balanced_break(normalized)


def balanced_break(text: str) -> str:
    if len(text) <= 36:
        return text
    midpoint = len(text) // 2
    split_index = nearest_space(text, midpoint)
    if split_index <= 0 or split_index >= len(text) - 1:
        return text
    first = text[:split_index].strip()
    second = text[split_index + 1 :].strip()
    if len(first) < 10 or len(second) < 10:
        return text
    return f"{first}\n{second}"


def nearest_space(text: str, midpoint: int) -> int:
    distances = sorted(
        (abs(index - midpoint), index) for index, character in enumerate(text) if character == " "
    )
    return distances[0][1] if distances else -1
