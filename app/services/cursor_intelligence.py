from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.projects import FocusBox, FrameSignalRecord, VisualSceneAnalysisRecord

CursorIntentState = Literal["navigation", "approach", "action", "abandon"]

APPROACH_LOOKBACK_SECONDS = 1.25
FOLLOWTHROUGH_SECONDS = 0.72
APPROACH_MAX_DISTANCE = 0.18
ACTION_MAX_DISTANCE = 0.1


@dataclass(frozen=True)
class CursorIntentMoment:
    timestamp: float
    state: CursorIntentState
    score: float
    proximity: float


@dataclass(frozen=True)
class CursorJourney:
    navigation_timestamp: float | None
    approach_timestamp: float | None
    commit_timestamp: float | None
    action_timestamp: float | None
    abandon_timestamp: float | None
    settle_timestamp: float | None
    confidence: float


def classify_cursor_journey(
    analysis: VisualSceneAnalysisRecord | None,
    target_box: FocusBox | None,
    action_time: float | None,
    result_time: float | None = None,
) -> CursorJourney | None:
    frames = cursor_frames(analysis, action_time, result_time)
    if target_box is None or action_time is None or not frames:
        return None
    approach = best_moment(frames, target_box, action_time, "approach")
    action = best_moment(frames, target_box, action_time, "action")
    navigation = navigation_moment(frames, target_box, action_time, approach)
    abandon = abandon_moment(frames, target_box, action_time, result_time)
    settle = settle_timestamp(action, result_time, abandon)
    confidence = journey_confidence(approach, action, abandon)
    return CursorJourney(
        navigation_timestamp=rounded_timestamp(navigation),
        approach_timestamp=rounded_timestamp(approach),
        commit_timestamp=rounded_timestamp(action or approach),
        action_timestamp=round(action_time, 2),
        abandon_timestamp=rounded_timestamp(abandon),
        settle_timestamp=settle,
        confidence=confidence,
    )


def cursor_approach_timestamp(
    analysis: VisualSceneAnalysisRecord | None,
    target_box: FocusBox | None,
    action_time: float | None,
) -> float | None:
    journey = classify_cursor_journey(analysis, target_box, action_time)
    return journey.approach_timestamp if journey is not None else None


def cursor_frames(
    analysis: VisualSceneAnalysisRecord | None,
    action_time: float | None,
    result_time: float | None,
) -> list[FrameSignalRecord]:
    if analysis is None or action_time is None or not analysis.frames:
        return []
    end = result_time if result_time is not None else action_time + FOLLOWTHROUGH_SECONDS
    return [
        frame
        for frame in analysis.frames
        if frame.cursor_box is not None and action_time - APPROACH_LOOKBACK_SECONDS <= frame.timestamp <= end
    ]


def best_moment(
    frames: list[FrameSignalRecord],
    target_box: FocusBox,
    action_time: float,
    state: CursorIntentState,
) -> CursorIntentMoment | None:
    candidates = scored_moments(frames, target_box, action_time, state)
    if not candidates:
        return None
    best = max(candidates, key=lambda item: item.score)
    threshold = 0.2 if state == "approach" else 0.26
    return best if best.score >= threshold else None


def scored_moments(
    frames: list[FrameSignalRecord],
    target_box: FocusBox,
    action_time: float,
    state: CursorIntentState,
) -> list[CursorIntentMoment]:
    moments: list[CursorIntentMoment] = []
    for index, frame in enumerate(frames):
        proximity = cursor_target_proximity(frame.cursor_box, target_box, action_cap_for_state(state))
        if proximity <= 0.0:
            continue
        score = cursor_state_score(frames, index, target_box, action_time, state, proximity)
        moments.append(CursorIntentMoment(timestamp=frame.timestamp, state=state, score=score, proximity=proximity))
    return moments


def navigation_moment(
    frames: list[FrameSignalRecord],
    target_box: FocusBox,
    action_time: float,
    approach: CursorIntentMoment | None,
) -> CursorIntentMoment | None:
    if approach is None:
        return None
    candidates = [
        CursorIntentMoment(
            timestamp=frame.timestamp,
            state="navigation",
            score=navigation_score(frame, target_box, action_time, approach),
            proximity=cursor_target_proximity(frame.cursor_box, target_box),
        )
        for frame in frames
        if frame.timestamp < approach.timestamp
    ]
    ranked = [candidate for candidate in candidates if candidate.score >= 0.1]
    return max(ranked, key=lambda item: item.score) if ranked else None


def abandon_moment(
    frames: list[FrameSignalRecord],
    target_box: FocusBox,
    action_time: float,
    result_time: float | None,
) -> CursorIntentMoment | None:
    candidates = [
        frame
        for frame in frames
        if frame.timestamp > action_time and (result_time is None or frame.timestamp <= result_time)
    ]
    best: CursorIntentMoment | None = None
    for frame in candidates:
        proximity = cursor_target_proximity(frame.cursor_box, target_box)
        score = round(max(0.0, box_distance(frame.cursor_box, target_box) - 0.12) + max(0.0, 0.42 - proximity), 3)
        if score >= 0.36:
            best = CursorIntentMoment(timestamp=frame.timestamp, state="abandon", score=score, proximity=proximity)
    return best


def settle_timestamp(
    action: CursorIntentMoment | None,
    result_time: float | None,
    abandon: CursorIntentMoment | None,
) -> float | None:
    if abandon is not None:
        return round(abandon.timestamp, 2)
    if result_time is not None:
        return round(result_time, 2)
    if action is None:
        return None
    return round(action.timestamp + 0.42, 2)


def cursor_state_score(
    frames: list[FrameSignalRecord],
    index: int,
    target_box: FocusBox,
    action_time: float,
    state: CursorIntentState,
    proximity: float,
) -> float:
    frame = frames[index]
    prev_frame = frames[index - 1] if index > 0 else None
    next_frame = frames[index + 1] if index + 1 < len(frames) else None
    freshness = freshness_score(action_time - frame.timestamp, state)
    trajectory = trajectory_score(prev_frame, frame, next_frame, target_box, state)
    click_bonus = 0.14 if frame.click_target_box is not None and state == "action" else 0.0
    return round(proximity * 0.58 + freshness + trajectory + click_bonus, 3)


def freshness_score(age_seconds: float, state: CursorIntentState) -> float:
    clamped_age = min(max(age_seconds, 0.0), APPROACH_LOOKBACK_SECONDS)
    if state == "approach":
        return max(0.0, 0.28 - clamped_age * 0.12)
    return max(0.0, 0.34 - clamped_age * 0.16)


def trajectory_score(
    previous: FrameSignalRecord | None,
    current: FrameSignalRecord,
    following: FrameSignalRecord | None,
    target_box: FocusBox,
    state: CursorIntentState,
) -> float:
    current_distance = box_distance(current.cursor_box, target_box)
    previous_distance = box_distance(previous.cursor_box, target_box) if previous is not None else current_distance + 0.03
    following_distance = box_distance(following.cursor_box, target_box) if following is not None else current_distance
    improving = max(0.0, previous_distance - current_distance)
    staying = max(0.0, 0.05 - abs(following_distance - current_distance))
    if state == "approach":
        return round(improving * 0.8 + staying * 0.3, 3)
    return round(improving * 0.35 + staying * 0.95, 3)


def navigation_score(
    frame: FrameSignalRecord,
    target_box: FocusBox,
    action_time: float,
    approach: CursorIntentMoment,
) -> float:
    age = max(0.0, approach.timestamp - frame.timestamp)
    proximity = cursor_target_proximity(frame.cursor_box, target_box)
    return round(max(0.0, 0.24 - age * 0.14) + proximity * 0.18 + max(0.0, 0.12 - abs(action_time - frame.timestamp) * 0.06), 3)


def journey_confidence(
    approach: CursorIntentMoment | None,
    action: CursorIntentMoment | None,
    abandon: CursorIntentMoment | None,
) -> float:
    base = 0.18
    if approach is not None:
        base += approach.score * 0.44
    if action is not None:
        base += action.score * 0.52
    if abandon is not None:
        base -= min(abandon.score, 0.28)
    return round(min(max(base, 0.0), 1.0), 3)


def rounded_timestamp(moment: CursorIntentMoment | None) -> float | None:
    return round(moment.timestamp, 2) if moment is not None else None


def cursor_target_proximity(
    cursor_box: FocusBox | None,
    target_box: FocusBox | None,
    max_distance: float = APPROACH_MAX_DISTANCE,
) -> float:
    if cursor_box is None or target_box is None:
        return 0.0
    distance = box_distance(cursor_box, target_box)
    if distance >= max_distance:
        return 0.0
    return round(max(0.0, 1.0 - (distance / max_distance)), 3)


def action_cap_for_state(state: CursorIntentState) -> float:
    return ACTION_MAX_DISTANCE if state == "action" else APPROACH_MAX_DISTANCE


def box_distance(left: FocusBox | None, right: FocusBox | None) -> float:
    if left is None or right is None:
        return 1.0
    left_center_x = left.x + left.width / 2
    left_center_y = left.y + left.height / 2
    right_center_x = right.x + right.width / 2
    right_center_y = right.y + right.height / 2
    return abs(left_center_x - right_center_x) + abs(left_center_y - right_center_y)
