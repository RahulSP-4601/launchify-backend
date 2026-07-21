from __future__ import annotations

from dataclasses import dataclass

from app.models.projects import FocusBox, FrameSignalRecord, UiElementRecord, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import box_center_delta, normalize_label

TRACKABLE_ROLES = ("button", "card", "control", "text", "heading")
AUTH_TERMS = frozenset({"google", "login", "log", "sign", "account", "continue", "create"})
COURSE_TERMS = frozenset({"course", "courses", "japanese", "english", "german", "spanish", "french", "open"})
LEVEL_TERMS = frozenset({"level", "jlpt", "pick", "start", "learning", "beginner", "intermediate", "advanced"})


@dataclass(frozen=True)
class TrackedElement:
    stable_label: str
    labels: tuple[str, ...]
    role: str
    first_seen: float
    last_seen: float
    occurrences: int
    best_confidence: float
    best_box: FocusBox | None


def tracked_elements(analysis: VisualSceneAnalysisRecord) -> list[TrackedElement]:
    tracks: list[TrackedElement] = []
    for frame in analysis.frames:
        for element in frame.ui_elements:
            if not trackable_element(element):
                continue
            index = matching_track_index(tracks, element)
            if index is None:
                tracks.append(new_track(frame, element))
                continue
            tracks[index] = merged_track(tracks[index], frame, element)
    return sorted(tracks, key=track_rank, reverse=True)


def best_tracked_label(
    analysis: VisualSceneAnalysisRecord,
    *,
    preferred_terms: set[str] | None = None,
    focus_box: FocusBox | None = None,
    state_family: str = "generic",
) -> str:
    ranked = ranked_tracks(
        analysis,
        preferred_terms=preferred_terms,
        focus_box=focus_box,
        state_family=state_family,
    )
    return ranked[0].stable_label if ranked else ""


def ranked_tracks(
    analysis: VisualSceneAnalysisRecord,
    *,
    preferred_terms: set[str] | None = None,
    focus_box: FocusBox | None = None,
    state_family: str = "generic",
) -> list[TrackedElement]:
    tracks = compatible_tracks(tracked_elements(analysis), state_family)
    if preferred_terms is None and focus_box is None:
        return tracks
    return sorted(
        tracks,
        key=lambda item: contextual_track_rank(item, preferred_terms or set(), focus_box),
        reverse=True,
    )


def compatible_tracks(
    tracks: list[TrackedElement],
    state_family: str,
) -> list[TrackedElement]:
    if state_family == "generic":
        return tracks
    filtered = [track for track in tracks if track_matches_family(track, state_family)]
    return filtered or tracks


def track_matches_family(track: TrackedElement, state_family: str) -> bool:
    tokens = set(normalize_label(track.stable_label).split())
    if state_family == "auth":
        return bool(tokens & AUTH_TERMS)
    if state_family == "course_catalog":
        return bool(tokens & COURSE_TERMS)
    if state_family in {"difficulty_picker", "result_state"}:
        return bool(tokens & LEVEL_TERMS) or "japanese" in tokens
    return True


def trackable_element(element: UiElementRecord) -> bool:
    normalized = normalize_label(element.label)
    if not normalized:
        return False
    if len(normalized.split()) > 8:
        return False
    role = element.role.lower()
    return any(kind in role for kind in TRACKABLE_ROLES) or element.confidence >= 0.72


def matching_track_index(tracks: list[TrackedElement], element: UiElementRecord) -> int | None:
    for index, track in enumerate(tracks):
        if same_track(track, element):
            return index
    return None


def same_track(track: TrackedElement, element: UiElementRecord) -> bool:
    label_key = normalize_label(element.label)
    track_keys = {normalize_label(label) for label in track.labels}
    same_label = label_key in track_keys
    same_family = label_family_match(label_key, track_keys)
    same_position = track.best_box is not None and box_center_delta(track.best_box, element.box) <= 0.14
    return (same_label or same_family) and same_position


def label_family_match(label_key: str, track_keys: set[str]) -> bool:
    if not label_key:
        return False
    tokens = set(label_key.split())
    for track_key in track_keys:
        if tokens and len(tokens & set(track_key.split())) >= max(1, min(len(tokens), 2)):
            return True
    return False


def new_track(frame: FrameSignalRecord, element: UiElementRecord) -> TrackedElement:
    return TrackedElement(
        stable_label=element.label.strip(),
        labels=(element.label.strip(),),
        role=element.role,
        first_seen=frame.timestamp,
        last_seen=frame.timestamp,
        occurrences=1,
        best_confidence=element.confidence,
        best_box=element.box,
    )


def merged_track(
    track: TrackedElement,
    frame: FrameSignalRecord,
    element: UiElementRecord,
) -> TrackedElement:
    labels = tuple(dict.fromkeys([*track.labels, element.label.strip()]))
    stable = best_label(labels, track.stable_label, element.label.strip())
    confidence = max(track.best_confidence, element.confidence)
    best_box = track.best_box if track.best_confidence >= element.confidence else element.box
    return TrackedElement(
        stable_label=stable,
        labels=labels,
        role=track.role,
        first_seen=track.first_seen,
        last_seen=frame.timestamp,
        occurrences=track.occurrences + 1,
        best_confidence=confidence,
        best_box=best_box,
    )


def best_label(labels: tuple[str, ...], current: str, candidate: str) -> str:
    options = [label for label in labels if label.strip()]
    ranked = sorted(options, key=label_rank, reverse=True)
    if ranked:
        return ranked[0]
    return candidate or current


def label_rank(label: str) -> tuple[float, float]:
    normalized = normalize_label(label)
    compact = 1.0 if len(normalized.split()) <= 4 else 0.6
    semantic = 1.0 if any(token in normalized for token in ("google", "course", "japanese", "account", "level")) else 0.4
    return (semantic, compact)


def track_rank(track: TrackedElement) -> tuple[float, float, float]:
    duration = max(track.last_seen - track.first_seen, 0.0)
    return (track.occurrences / 3.0, track.best_confidence, duration)


def contextual_track_rank(
    track: TrackedElement,
    preferred_terms: set[str],
    focus_box: FocusBox | None,
) -> tuple[float, float, float, float]:
    label_tokens = set(normalize_label(track.stable_label).split())
    preferred_overlap = len(label_tokens & preferred_terms) / max(len(preferred_terms), 1) if preferred_terms else 0.0
    focus_score = 0.0
    if focus_box is not None and track.best_box is not None:
        focus_score = max(0.0, 1.0 - box_center_delta(focus_box, track.best_box) / 0.2)
    return (preferred_overlap, focus_score, track.best_confidence, track.occurrences / 3.0)
