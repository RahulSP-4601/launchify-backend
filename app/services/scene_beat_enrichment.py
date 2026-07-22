from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.projects import EditPlanScene, EditPlanZoom


@dataclass(frozen=True)
class SceneBeatPlan:
    lines: tuple[str, ...]
    intents: tuple[str, ...]
    target_duration: float
    phase_count: int
    density_score: int


def build_scene_beat_plan(
    scene: EditPlanScene,
    transcript_excerpt: str,
    *,
    is_first: bool,
    available_duration: float,
    target_text: str,
    title_text: str,
) -> SceneBeatPlan | None:
    excerpt = clean_transcript_excerpt(transcript_excerpt)
    if not excerpt:
        return None
    sentences = split_sentences(excerpt)
    if not sentences:
        return None
    ranked = ranked_transcript_beats(scene, sentences, is_first, target_text, title_text)
    if not ranked or ranked[0][2] <= 0:
        return None
    selected = ordered_transcript_beats(scene, ranked, available_duration)
    lines = tuple(sentence for _index, sentence, _score, _intent in selected)
    intents = tuple(item[3] for item in selected)
    target_duration = recommended_scene_duration(scene, available_duration, lines, intents)
    if target_duration > available_duration:
        expanded = ordered_transcript_beats(scene, ranked, target_duration)
        expanded_lines = tuple(sentence for _index, sentence, _score, _intent in expanded)
        expanded_intents = tuple(item[3] for item in expanded)
        if len(expanded_lines) > len(lines):
            selected = expanded
            lines = expanded_lines
            intents = expanded_intents
            target_duration = recommended_scene_duration(scene, available_duration, lines, intents)
    return SceneBeatPlan(
        lines=lines,
        intents=intents,
        target_duration=target_duration,
        phase_count=recommended_phase_count(target_duration, lines, intents),
        density_score=scene_density_score(scene, lines, intents),
    )


def clean_transcript_excerpt(excerpt: str) -> str:
    cleaned = " ".join((excerpt or "").replace(" ,", ",").split()).strip()
    if not cleaned:
        return ""
    return re.sub(r"\b(you)\s+\1\b", r"\1", cleaned, flags=re.IGNORECASE)


def split_sentences(excerpt: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", excerpt)
    clauses: list[str] = []
    for part in parts:
        clauses.extend(split_dense_clause(part))
    return [part.strip(" ,") for part in clauses if part.strip(" ,")]


def split_dense_clause(excerpt: str) -> list[str]:
    cleaned = excerpt.strip(" ,")
    if not cleaned:
        return []
    parts = re.split(
        r",\s+|(?<=\w)\s+(?:and once|and then|and now|right now|once you|once the|after you|after the|so under)\s+",
        cleaned,
        flags=re.IGNORECASE,
    )
    clauses = [part.strip(" ,") for part in parts if part.strip(" ,")]
    return clauses if len(clauses) > 1 else [cleaned]


def launches_product(sentence: str) -> bool:
    lowered = sentence.lower()
    return any(token in lowered for token in ("we are launching", "we're launching", "ai powered", "ai-powered"))


def ranked_transcript_beats(
    scene: EditPlanScene,
    sentences: list[str],
    is_first: bool,
    target_text: str,
    title_text: str,
) -> list[tuple[int, str, int, str]]:
    ranked = [
        (
            index,
            sentence,
            transcript_match_score(scene, sentence, is_first, target_text, title_text),
            transcript_intent(scene, sentence, is_first),
        )
        for index, sentence in enumerate(sentences)
    ]
    return sorted(ranked, key=lambda item: item[2], reverse=True)


def ordered_transcript_beats(
    scene: EditPlanScene,
    ranked: list[tuple[int, str, int, str]],
    available_duration: float,
) -> list[tuple[int, str, int, str]]:
    beat_limit = intent_beat_limit(available_duration)
    selected: list[tuple[int, str, int, str]] = []
    selected_ids: set[int] = set()
    seen_intents: set[str] = set()
    for intent in preferred_intents(scene):
        candidate = next((item for item in ranked if item[3] == intent and item[2] > 0), None)
        if candidate is None or candidate[0] in selected_ids:
            continue
        selected.append(candidate)
        selected_ids.add(candidate[0])
        seen_intents.add(intent)
        if len(selected) >= beat_limit:
            return sorted(selected, key=lambda item: item[0])
    for item in ranked:
        if len(selected) >= beat_limit:
            break
        if item[0] in selected_ids:
            continue
        if item[3] in seen_intents and item[2] < 3:
            continue
        selected.append(item)
        selected_ids.add(item[0])
        seen_intents.add(item[3])
    return sorted(selected or [ranked[0]], key=lambda item: item[0])


def intent_beat_limit(duration: float) -> int:
    if duration < 3.2:
        return 1
    if duration < 5.6:
        return 2
    if duration < 8.2:
        return 3
    return 4


def preferred_intents(scene: EditPlanScene) -> tuple[str, ...]:
    if scene.action_class == "auth_action":
        return ("intro", "action", "choice", "result")
    if scene.action_class == "card_selection":
        return ("context", "action", "result")
    if "level" in normalized_scene_title(scene):
        return ("choice", "action", "result")
    return ("action", "result", "context")


def transcript_match_score(
    scene: EditPlanScene,
    sentence: str,
    is_first: bool,
    target_text: str,
    title_text: str,
) -> int:
    lowered = sentence.lower()
    target = target_text.lower()
    score = 0
    intent = transcript_intent(scene, sentence, is_first)
    if is_first and intent == "intro":
        score += 3
    if scene.action_class == "auth_action":
        score += sum(term in lowered for term in ("google", "login", "log in", "sign in", "account", "existing one"))
        if "continue with google" in target and ("existing" in lowered or "log in" in lowered):
            score += 2
    if scene.action_class == "card_selection":
        score += sum(term in lowered for term in ("course", "courses", "open"))
        if any(term in lowered for term in ("coming soon", "ready", "live")):
            score += 2
    if "level" in title_text:
        score += sum(term in lowered for term in ("level", "start", "begin"))
    if any(term in lowered for term in ("you'll see", "you can see", "right now")):
        score += 1
    score += target_overlap_score(target, lowered)
    if intent in preferred_intents(scene):
        score += 1
    return score


def transcript_intent(scene: EditPlanScene, sentence: str, is_first: bool) -> str:
    lowered = sentence.lower()
    if is_first and launches_product(sentence):
        return "intro"
    if any(token in lowered for token in ("click", "choose", "open", "continue with", "log in", "sign in", "go to")):
        return "action"
    if any(token in lowered for token in ("create a new account", "create an account", "existing account", "existing one", "coming soon", "only one", "first")):
        return "choice"
    if any(token in lowered for token in ("you'll see", "you can see", "ready", "live", "opens", "opened")):
        return "result"
    return "context" if scene.action_class == "card_selection" else "result"


def target_overlap_score(target: str, sentence: str) -> int:
    tokens = [token for token in target.split() if token not in {"the", "a", "an", "course"}]
    return sum(token in sentence for token in tokens[:2])


def recommended_scene_duration(
    scene: EditPlanScene,
    duration: float,
    lines: tuple[str, ...],
    intents: tuple[str, ...],
) -> float:
    bonus = 0.0
    if len(lines) >= 2:
        bonus += 1.4
    if len(lines) >= 3:
        bonus += 1.8
    if len(lines) >= 4:
        bonus += 1.2
    if "result" in intents:
        bonus += 0.7
    if "choice" in intents:
        bonus += 0.9
    if "context" in intents:
        bonus += 0.6
    if scene.action_class == "auth_action" and "intro" in intents:
        bonus += 1.0
    if scene.action_class == "card_selection" and {"context", "action", "result"} <= set(intents):
        bonus += 1.2
    if scene.scene_role == "result":
        bonus += 0.8
    cap = duration_cap(scene)
    return round(min(max(duration + bonus, duration), cap), 2)


def recommended_phase_count(duration: float, lines: tuple[str, ...], intents: tuple[str, ...]) -> int:
    density = len(lines) + len(set(intents))
    if density >= 6 and duration >= 6.4:
        return 4
    if len(lines) >= 3 or ("result" in intents and "action" in intents and duration >= 5.0):
        return 3
    if duration >= 2.7:
        return 2
    if len(intents) >= 2 or duration >= 3.2:
        return 2
    return 1


def duration_cap(scene: EditPlanScene) -> float:
    if scene.action_class == "auth_action":
        return 10.8 if scene.scene_number == 1 else 6.6
    if scene.action_class == "card_selection":
        return 11.4
    if "level" in normalized_scene_title(scene):
        return 7.1
    return 7.4


def enrich_scene_motion(scene: EditPlanScene, phase_count: int) -> EditPlanScene:
    if phase_count < 2 or not scene.zooms:
        return scene
    base_zoom = strongest_zoom(scene)
    windows = motion_windows(scene, phase_count)
    zooms = build_motion_segments(base_zoom, windows)
    hold = max(scene.readable_hold_seconds, 1.45 if phase_count >= 4 else 1.2 if phase_count >= 3 else 0.95)
    return scene.model_copy(update={"zooms": zooms, "readable_hold_seconds": round(hold, 2)})


def scene_density_score(scene: EditPlanScene, lines: tuple[str, ...], intents: tuple[str, ...]) -> int:
    score = len(lines) + len(set(intents))
    if scene.action_class in {"auth_action", "card_selection"}:
        score += 1
    if scene.scene_role == "result":
        score += 1
    return score


def strongest_zoom(scene: EditPlanScene) -> EditPlanZoom:
    return max(scene.zooms, key=lambda zoom: (zoom.scale, zoom.confidence, zoom.end - zoom.start))


def motion_windows(scene: EditPlanScene, phase_count: int) -> list[tuple[float, float]]:
    start = round(scene.start, 2)
    duration = max(scene.end - scene.start, 0.8)
    focus_start = round(scene.focus_start_timestamp or start, 2)
    focus_end = round(scene.focus_end_timestamp or min(scene.end, focus_start + 0.9), 2)
    settle_end = round(scene.settle_end_timestamp or min(scene.end, focus_end + 0.78), 2)
    result_anchor = round(scene.result_anchor_timestamp or settle_end, 2)
    end = round(scene.end, 2)
    if phase_count >= 3 and focus_start - start < 0.34:
        synthetic_focus_start = max(focus_start, start + max(min(duration * 0.18, 0.8), 0.46))
        if synthetic_focus_start - start >= 0.32:
            focus_start = round(synthetic_focus_start, 2)
    if phase_count >= 3 and focus_end - focus_start < 0.34:
        synthetic_focus_end = min(end - 0.64, focus_start + max(min(duration * 0.12, 0.82), 0.46))
        if synthetic_focus_end - focus_start >= 0.32:
            focus_end = round(synthetic_focus_end, 2)
    if settle_end - focus_end < 0.32:
        synthetic_settle_end = min(end - 0.32, focus_end + max(min(duration * 0.11, 0.76), 0.36))
        if synthetic_settle_end - focus_end >= 0.28:
            settle_end = round(synthetic_settle_end, 2)
    if phase_count >= 3 and result_anchor <= focus_end + 0.24:
        synthetic_result = max(
            focus_end + max(min(duration * 0.16, 1.0), 0.62),
            end - max(min(duration * 0.18, 1.0), 0.68),
        )
        if end - synthetic_result >= 0.32:
            result_anchor = round(synthetic_result, 2)
    if phase_count >= 4 and end - result_anchor < 0.34:
        synthetic_result = max(focus_end + 0.36, end - max(min(duration * 0.14, 0.9), 0.5))
        if end - synthetic_result >= 0.32:
            result_anchor = round(synthetic_result, 2)
    if phase_count >= 4 and end - result_anchor >= 0.34 and focus_start - start >= 0.34:
        return compact_windows([(start, focus_start), (focus_start, focus_end), (focus_end, result_anchor), (result_anchor, end)])
    if phase_count >= 3 and focus_start - start >= 0.34:
        return compact_windows([(start, focus_start), (focus_start, focus_end), (focus_end, max(settle_end, end))])
    return compact_windows([(focus_start, focus_end), (focus_end, max(settle_end, end))])


def compact_windows(windows: list[tuple[float, float]]) -> list[tuple[float, float]]:
    compact: list[tuple[float, float]] = []
    for start, end in windows:
        if end - start >= 0.32:
            compact.append((round(start, 2), round(end, 2)))
    return compact


def build_motion_segments(base: EditPlanZoom, windows: list[tuple[float, float]]) -> list[EditPlanZoom]:
    if len(windows) < 2:
        return [base]
    phases = motion_phase_specs(base.scale, len(windows))
    return [
        base.model_copy(
            update={
                "start": start,
                "end": end,
                "scale": scale,
                "reason": reason,
                "easing": "ease-in-out",
                "smoothing": smoothing,
                "hold_ratio": hold_ratio,
                "x_offset": round(base.x_offset * offset_ratio, 4),
                "y_offset": round(base.y_offset * offset_ratio, 4),
            }
        )
        for (start, end), (scale, reason, smoothing, hold_ratio, offset_ratio) in zip(windows, phases)
    ]


def motion_phase_specs(base_scale: float, count: int) -> list[tuple[float, str, float, float, float]]:
    focus_scale = max(1.12, round(base_scale + 0.02, 2))
    establish_scale = max(1.02, round(focus_scale - 0.14, 2))
    settle_scale = max(1.05, round(focus_scale - 0.08, 2))
    result_scale = max(1.03, round(settle_scale - 0.02, 2))
    if count >= 4:
        return [
            (establish_scale, "Editorial establish move into the active UI state.", 0.1, 0.42, 0.34),
            (focus_scale, "Editorial push toward the grounded action target.", 0.14, 0.74, 1.12),
            (settle_scale, "Editorial settle hold after the action lands.", 0.18, 0.86, 0.78),
            (result_scale, "Editorial result hold to reveal the next product state.", 0.22, 0.94, 0.42),
        ]
    if count == 3:
        return [
            (establish_scale, "Editorial establish move into the active UI state.", 0.1, 0.42, 0.34),
            (focus_scale, "Editorial push toward the grounded action target.", 0.14, 0.74, 1.1),
            (settle_scale, "Editorial settle hold after the action lands.", 0.18, 0.9, 0.74),
        ]
    return [
        (focus_scale, "Editorial push toward the grounded action target.", 0.14, 0.74, 1.08),
        (settle_scale, "Editorial settle hold after the action lands.", 0.18, 0.9, 0.72),
    ]

def normalized_scene_title(scene: EditPlanScene) -> str:
    return " ".join((scene.title, scene.on_screen_text, scene.purpose)).lower()
