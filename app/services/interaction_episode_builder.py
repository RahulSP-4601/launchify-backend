from __future__ import annotations

from dataclasses import dataclass, field

from app.services.evidence_timeline import EvidenceSignal

EPISODE_MERGE_GAP_SECONDS = 1.2
MIN_EPISODE_SCORE = 0.52


@dataclass(frozen=True)
class InteractionEpisode:
    scene_number: int
    start: float
    end: float
    anchor_timestamp: float
    evidence: list[EvidenceSignal] = field(default_factory=list)


def build_interaction_episodes(signals: list[EvidenceSignal]) -> list[InteractionEpisode]:
    proposals = actionable_signals(signals)
    if not proposals:
        return []
    episodes: list[InteractionEpisode] = []
    bucket: list[EvidenceSignal] = [proposals[0]]
    for signal in proposals[1:]:
        if should_merge(bucket[-1], signal):
            bucket.append(signal)
            continue
        maybe_append_episode(episodes, bucket)
        bucket = [signal]
    maybe_append_episode(episodes, bucket)
    return episodes


def actionable_signals(signals: list[EvidenceSignal]) -> list[EvidenceSignal]:
    return [
        signal
        for signal in signals
        if signal.signal_type in {"click", "transition", "state_hint", "transcript_action"}
        and signal.score >= 0.34
    ]


def should_merge(left: EvidenceSignal, right: EvidenceSignal) -> bool:
    if abs(right.timestamp - left.timestamp) > EPISODE_MERGE_GAP_SECONDS:
        return False
    if left.scene_number > 0 and right.scene_number > 0 and left.scene_number != right.scene_number:
        return False
    if left.scene_number == right.scene_number:
        return True
    return left.signal_type == right.signal_type and comparable_agnostic_signal(left, right)


def comparable_agnostic_signal(left: EvidenceSignal, right: EvidenceSignal) -> bool:
    if left.scene_number > 0 and right.scene_number > 0:
        return False
    left_label = label_key(left.label)
    right_label = label_key(right.label)
    if left_label and right_label and left_label == right_label:
        return True
    return left.source == right.source


def label_key(label: str) -> str:
    return " ".join(label.lower().split())


def maybe_append_episode(
    episodes: list[InteractionEpisode],
    bucket: list[EvidenceSignal],
) -> None:
    if not bucket or episode_score(bucket) < MIN_EPISODE_SCORE:
        return
    scenes = [signal.scene_number for signal in bucket if signal.scene_number > 0]
    anchor = max(bucket, key=lambda item: (item.score, priority_rank(item.signal_type)))
    episodes.append(
        InteractionEpisode(
            scene_number=dominant_scene_number(scenes),
            start=round(min(signal.timestamp for signal in bucket), 2),
            end=round(max(signal.timestamp for signal in bucket), 2),
            anchor_timestamp=anchor.timestamp,
            evidence=sorted(bucket, key=lambda item: item.timestamp),
        )
    )


def episode_score(bucket: list[EvidenceSignal]) -> float:
    top = max((signal.score for signal in bucket), default=0.0)
    breadth = min(len({signal.signal_type for signal in bucket}) * 0.08, 0.24)
    return round(min(top + breadth, 1.0), 3)


def priority_rank(signal_type: str) -> int:
    order = {
        "click": 4,
        "state_hint": 3,
        "transition": 2,
        "transcript_action": 1,
    }
    return order.get(signal_type, 0)


def dominant_scene_number(scenes: list[int]) -> int:
    if not scenes:
        return 0
    counts: dict[int, int] = {}
    for scene in scenes:
        counts[scene] = counts.get(scene, 0) + 1
    return max(counts.items(), key=lambda item: (item[1], -item[0]))[0]
