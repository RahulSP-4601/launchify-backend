from __future__ import annotations

from dataclasses import dataclass, field

from app.models.projects import FocusBox, FrameSignalRecord, TranscriptSegment, VisualSceneAnalysisRecord
from app.services.inferred_recording_support import normalize_label
from app.services.scene_intent_resolver import ACTION_WORDS
from app.services.ui_structure_insights import collected_labels, frame_structure, structure_state_label

ACTION_HINTS = frozenset({"account", "choose", "click", "continue", "course", "google", "login", "open", "pick", "select"})


@dataclass(frozen=True)
class EvidenceSignal:
    timestamp: float
    scene_number: int
    source: str
    signal_type: str
    score: float
    label: str = ""
    focus_box: FocusBox | None = None
    details: dict[str, str] = field(default_factory=dict)


def build_evidence_timeline(
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[EvidenceSignal]:
    signals = transcript_signals(transcript, analyses_by_scene)
    for analysis in analyses_by_scene.values():
        signals.extend(scene_signals(analysis))
    return sorted(signals, key=lambda item: (item.timestamp, item.scene_number, item.score))


def evidence_payload(signals: list[EvidenceSignal]) -> list[dict[str, object]]:
    return [
        {
            "timestamp": signal.timestamp,
            "scene_number": signal.scene_number,
            "source": signal.source,
            "signal_type": signal.signal_type,
            "score": signal.score,
            "label": signal.label,
            "focus_box": None if signal.focus_box is None else signal.focus_box.model_dump(mode="json"),
            "details": signal.details,
        }
        for signal in signals
    ]


def transcript_signals(
    transcript: list[TranscriptSegment],
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> list[EvidenceSignal]:
    signals: list[EvidenceSignal] = []
    for index, segment in enumerate(transcript, start=1):
        label = transcript_signal_label(segment.text)
        if not label:
            continue
        midpoint = round((segment.start + segment.end) / 2, 2)
        signals.append(
            EvidenceSignal(
                timestamp=midpoint,
                scene_number=scene_number_for_timestamp(midpoint, analyses_by_scene),
                source="transcript",
                signal_type="transcript_action",
                score=transcript_signal_score(segment.text),
                label=label,
                details={
                    "excerpt": segment.text[:180],
                    "transcript_index": str(index),
                },
            )
        )
    return signals


def scene_number_for_timestamp(
    timestamp: float,
    analyses_by_scene: dict[int, VisualSceneAnalysisRecord],
) -> int:
    if not analyses_by_scene:
        return 0
    containing = [
        analysis
        for analysis in analyses_by_scene.values()
        if analysis.start - 0.2 <= timestamp <= analysis.end + 0.2
    ]
    if containing:
        best = min(
            containing,
            key=lambda analysis: (
                abs(((analysis.start + analysis.end) / 2) - timestamp),
                abs(analysis.end - analysis.start),
                analysis.scene_number,
            ),
        )
        return best.scene_number
    nearby = min(
        analyses_by_scene.values(),
        key=lambda analysis: (
            distance_to_scene_window(timestamp, analysis),
            abs(((analysis.start + analysis.end) / 2) - timestamp),
            analysis.scene_number,
        ),
    )
    if distance_to_scene_window(timestamp, nearby) <= 1.5:
        return nearby.scene_number
    return 0


def distance_to_scene_window(timestamp: float, analysis: VisualSceneAnalysisRecord) -> float:
    if analysis.start <= timestamp <= analysis.end:
        return 0.0
    if timestamp < analysis.start:
        return round(analysis.start - timestamp, 3)
    return round(timestamp - analysis.end, 3)


def transcript_signal_label(text: str) -> str:
    lowered = normalize_label(text)
    if not lowered:
        return ""
    if not any(token in lowered.split() for token in ACTION_WORDS):
        return ""
    return text.strip()[:72]


def transcript_signal_score(text: str) -> float:
    lowered = normalize_label(text)
    matches = sum(1 for token in ACTION_HINTS if token in lowered)
    return round(min(0.38 + matches * 0.08, 0.86), 3)


def scene_signals(analysis: VisualSceneAnalysisRecord) -> list[EvidenceSignal]:
    signals: list[EvidenceSignal] = [analysis_signal(analysis)]
    for frame in analysis.frames:
        signals.extend(frame_signals(analysis.scene_number, frame, analysis.visible_labels))
    return signals


def analysis_signal(analysis: VisualSceneAnalysisRecord) -> EvidenceSignal:
    midpoint = round((analysis.start + analysis.end) / 2, 2)
    label = next((label for label in analysis.visible_labels if label.strip()), analysis.summary)
    return EvidenceSignal(
        timestamp=midpoint,
        scene_number=analysis.scene_number,
        source="visual",
        signal_type="scene_presence",
        score=round(min(0.3 + analysis.confidence * 0.4, 0.78), 3),
        label=label.strip(),
        focus_box=analysis.primary_focus_box or analysis.anchor_box,
    )


def frame_signals(
    scene_number: int,
    frame: FrameSignalRecord,
    visible_labels: list[str],
) -> list[EvidenceSignal]:
    signals: list[EvidenceSignal] = []
    local_labels = local_frame_labels(frame, visible_labels)
    if frame.click_confidence >= 0.34:
        signals.append(click_signal(scene_number, frame))
    if frame.diff_score >= 0.32:
        signals.append(transition_signal(scene_number, frame))
    state_label = structure_state_label(frame, local_labels)
    structure = frame_structure(frame, local_labels)
    if state_label and structure != "generic":
        signals.append(
            EvidenceSignal(
                timestamp=round(frame.timestamp, 2),
                scene_number=scene_number,
                source="visual",
                signal_type="state_hint",
                score=round(min(0.32 + frame.importance_score * 0.4, 0.82), 3),
                label=state_label,
                focus_box=frame.dominant_box,
                details={"structure": structure},
            )
        )
    return signals


def click_signal(scene_number: int, frame: FrameSignalRecord) -> EvidenceSignal:
    label = best_frame_label(frame)
    return EvidenceSignal(
        timestamp=round(frame.timestamp, 2),
        scene_number=scene_number,
        source="visual",
        signal_type="click",
        score=round(min(0.42 + frame.click_confidence * 0.5, 0.98), 3),
        label=label,
        focus_box=frame.click_target_box or frame.dominant_box,
    )


def transition_signal(scene_number: int, frame: FrameSignalRecord) -> EvidenceSignal:
    return EvidenceSignal(
        timestamp=round(frame.timestamp, 2),
        scene_number=scene_number,
        source="visual",
        signal_type="transition",
        score=round(min(0.28 + frame.diff_score * 0.42, 0.78), 3),
        label=best_frame_label(frame),
        focus_box=frame.dominant_box,
    )


def best_frame_label(frame: FrameSignalRecord) -> str:
    labels = [item.label.strip() for item in frame.ui_elements if item.label.strip()]
    labels.extend(label.strip() for label in frame.ocr_labels if label.strip())
    return labels[0] if labels else frame.summary.strip()


def local_frame_labels(frame: FrameSignalRecord, visible_labels: list[str]) -> list[str]:
    labels = collected_labels(frame, visible_labels)
    return [label for label in labels if label]
