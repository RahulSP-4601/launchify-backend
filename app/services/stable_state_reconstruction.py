from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox, FrameSignalRecord, VisualSceneAnalysisRecord
from app.services.element_tracker import best_tracked_label
from app.services.inferred_recording_support import normalize_label
from app.services.interaction_episode_builder import InteractionEpisode
from app.services.ui_structure_insights import collected_labels, frame_structure, structure_state_label


@dataclass(frozen=True)
class StateFingerprint:
    scene_number: int
    timestamp: float
    structure: str
    friendly_label: str
    headings: tuple[str, ...]
    labels: tuple[str, ...]
    target_label: str
    focus_box: FocusBox | None
    stability_score: float


@dataclass(frozen=True)
class EpisodeStateBundle:
    episode: InteractionEpisode
    before_state: StateFingerprint | None
    action_state: StateFingerprint | None
    result_state: StateFingerprint | None
    immediate_state: StateFingerprint | None


def reconstruct_episode_states(
    episodes: list[InteractionEpisode],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[EpisodeStateBundle]:
    bundles: list[EpisodeStateBundle] = []
    for episode in episodes:
        analysis = analyses_by_scene.get(episode.scene_number)
        if analysis is None or not analysis.frames:
            continue
        bundles.append(
            EpisodeStateBundle(
                episode=episode,
                before_state=frame_fingerprint(analysis, before_frame(analysis, episode.anchor_timestamp)),
                action_state=frame_fingerprint(analysis, action_frame(analysis, episode.anchor_timestamp)),
                result_state=frame_fingerprint(analysis, settled_result_frame(analysis, episode.anchor_timestamp)),
                immediate_state=frame_fingerprint(analysis, immediate_frame(analysis, episode.anchor_timestamp)),
            )
        )
    return bundles


def before_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    frames = [frame for frame in analysis.frames if frame.timestamp <= anchor]
    return frames[0] if frames else first_frame(analysis)


def action_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    nearby = [frame for frame in analysis.frames if abs(frame.timestamp - anchor) <= 1.0]
    if not nearby:
        nearby = analysis.frames[:]
    return max(nearby, key=action_rank)


def immediate_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    later = [frame for frame in analysis.frames if frame.timestamp >= anchor]
    return later[0] if later else last_frame(analysis)


def settled_result_frame(analysis: VisualSceneAnalysisRecord, anchor: float) -> FrameSignalRecord | None:
    later = [frame for frame in analysis.frames if frame.timestamp >= anchor]
    frames = later or analysis.frames
    return max(frames, key=lambda item: settled_rank(item, analysis))


def first_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    return analysis.frames[0] if analysis.frames else None


def last_frame(analysis: VisualSceneAnalysisRecord) -> FrameSignalRecord | None:
    return analysis.frames[-1] if analysis.frames else None


def action_rank(frame: FrameSignalRecord) -> tuple[float, float, float]:
    return (frame.click_confidence, frame.importance_score, frame.diff_score)


def settled_rank(frame: FrameSignalRecord, analysis: VisualSceneAnalysisRecord) -> tuple[float, float, float, float]:
    structure = frame_structure(frame, visible_labels(analysis, frame))
    structure_bonus = 0.25 if structure in {"dashboard", "picker", "result"} else 0.0
    stability = max(0.0, 1.0 - frame.diff_score)
    return (structure_bonus + stability, frame.importance_score, frame.ocr_confidence, frame.timestamp)


def frame_fingerprint(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord | None,
) -> StateFingerprint | None:
    if frame is None:
        return None
    labels = visible_labels(analysis, frame)
    structure = frame_structure(frame, labels)
    friendly = structure_state_label(frame, labels) or best_heading(labels) or best_target_label(frame, labels)
    return StateFingerprint(
        scene_number=analysis.scene_number,
        timestamp=round(frame.timestamp, 2),
        structure=structure,
        friendly_label=friendly,
        headings=tuple(extract_headings(labels)),
        labels=tuple(labels[:8]),
        target_label=tracked_target_label(analysis, frame, labels, structure),
        focus_box=frame.click_target_box or frame.dominant_box or analysis.primary_focus_box or analysis.anchor_box,
        stability_score=round(state_stability_score(frame, structure), 3),
    )


def visible_labels(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
) -> list[str]:
    labels = [item.label.strip() for item in frame.ui_elements if item.label.strip()]
    labels.extend(label.strip() for label in frame.ocr_labels if label.strip())
    if len(labels) < 3:
        labels.extend(label.strip() for label in analysis.visible_labels if label.strip())
    return list(dict.fromkeys(labels))


def best_heading(labels: list[str]) -> str:
    return next((label for label in labels if heading_like(label)), "")


def extract_headings(labels: list[str]) -> list[str]:
    return [label for label in labels if heading_like(label)][:3]


def heading_like(label: str) -> bool:
    normalized = normalize_label(label)
    return any(phrase in normalized for phrase in ("choose", "pick your", "select a course", "before you start"))


def best_target_label(frame: FrameSignalRecord, labels: list[str]) -> str:
    candidates = [item.label.strip() for item in frame.ui_elements if item.label.strip()]
    if candidates:
        return max(candidates, key=target_rank)
    return labels[0] if labels else ""


def tracked_target_label(
    analysis: VisualSceneAnalysisRecord,
    frame: FrameSignalRecord,
    labels: list[str],
    structure: str,
) -> str:
    preferred_terms = target_terms(frame, labels)
    tracked = best_tracked_label(
        analysis,
        preferred_terms=preferred_terms,
        focus_box=frame.click_target_box or frame.dominant_box,
        state_family=state_family(structure, labels),
    )
    return tracked or best_target_label(frame, labels)


def target_rank(label: str) -> tuple[float, float]:
    normalized = normalize_label(label)
    compact = 1.0 if len(normalized.split()) <= 4 else 0.6
    action_bonus = 1.0 if any(token in normalized for token in ("google", "course", "account", "japanese", "login", "continue")) else 0.0
    return (action_bonus, compact)


def state_stability_score(frame: FrameSignalRecord, structure: str) -> float:
    bonus = 0.14 if structure in {"dashboard", "picker", "result"} else 0.0
    return min(0.3 + frame.importance_score * 0.35 + (1.0 - frame.diff_score) * 0.25 + bonus, 1.0)


def target_terms(frame: FrameSignalRecord, labels: list[str]) -> set[str]:
    terms = set()
    for label in [*(item.label for item in frame.ui_elements), *labels]:
        terms.update(normalize_label(label).split())
    return {term for term in terms if len(term) >= 4}


def state_family(structure: str, labels: list[str]) -> str:
    label_text = " ".join(normalize_label(label) for label in labels)
    if structure == "dashboard":
        return "course_catalog"
    if structure == "picker":
        return "account_picker" if "account" in label_text else "difficulty_picker"
    if structure == "result":
        return "result_state"
    if any(token in label_text for token in ("google", "login", "sign up", "log in", "account")):
        return "auth"
    return "generic"
