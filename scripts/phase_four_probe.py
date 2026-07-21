from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.projects import (
    BenchmarkReportRecord,
    EditPlanRecord,
    GuideRecord,
    LaunchScriptRecord,
    ManualOverrideRecord,
    ProjectRecord,
    QualityReportRecord,
    RecordingSessionRecord,
    TemplateConfigRecord,
    TranscriptSegment,
    VisualSceneAnalysisRecord,
    VoiceoverMode,
    VoiceoverRecord,
)
from app.services.benchmarking import build_benchmark_report
from app.services.edit_planner import generate_edit_plan
from app.services.phase_four import refined_plan_and_report, voiceover_for_project
from app.services.guide_synthesizer import cluster_events, reconcile_grounded_guide, synthesize_grounded_guide
from app.services.guide_timing_ranges import contextual_step_ranges
from app.services.preview_delivery import preview_delivery_diagnostics
from app.services.voiceover_timeline import reconcile_edit_plan_to_voiceover


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay Step 2 locally from raw-upload probe artifacts and dump the edit-plan pipeline outputs.",
    )
    parser.add_argument("artifact_dir", help="Directory containing transcript.json, launch_script.json, recording_session.json, and optional visual_analyses.json.")
    parser.add_argument("--output-dir", default="", help="Where to write phase-four artifacts. Defaults to <artifact_dir>/phase-four-probe.")
    parser.add_argument("--project-name", default="Phase 4 probe")
    parser.add_argument("--product-name", default="Launchify probe")
    parser.add_argument("--product-description", default="Local phase four replay from raw-upload artifacts.")
    parser.add_argument("--target-audience", default="Internal QA")
    parser.add_argument("--video-goal", default="launch_video")
    parser.add_argument(
        "--voiceover-mode",
        choices=["original", "voiceover", "mixed"],
        default="original",
        help="Use 'original' to inspect timings/copy without generating TTS audio.",
    )
    parser.add_argument(
        "--skip-guide-synthesis",
        action="store_true",
        help="Use the saved launch script directly instead of synthesizing a grounded guide from the recording session.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact_dir = Path(args.artifact_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else artifact_dir / "phase-four-probe"
    output_dir.mkdir(parents=True, exist_ok=True)

    transcript = load_list(artifact_dir / "transcript.json", TranscriptSegment)
    launch_script = LaunchScriptRecord.model_validate(load_json(artifact_dir / "launch_script.json"))
    recording_session = RecordingSessionRecord.model_validate(load_json(artifact_dir / "recording_session.json"))
    visual_analyses = load_optional_list(artifact_dir / "visual_analyses.json", VisualSceneAnalysisRecord)

    project = probe_project(args, transcript, launch_script, recording_session)
    if args.skip_guide_synthesis:
        guide = load_existing_guide(artifact_dir)
        effective_launch_script = launch_script
        if guide is not None:
            guide = refreshed_existing_guide(project, transcript, guide, visual_analyses)
            project = project.model_copy(update={"guide": guide, "launch_script": effective_launch_script})
    else:
        guide, effective_launch_script = synthesize_grounded_guide(project, transcript, visual_analyses)
        project = project.model_copy(update={"guide": guide, "launch_script": effective_launch_script})

    edit_plan = generate_edit_plan(project, visual_analyses)
    refined_edit_plan, quality_report = refined_plan_and_report(project, edit_plan, project.manual_overrides or ManualOverrideRecord())
    voiceover = voiceover_for_project("phase-four-probe", project, refined_edit_plan, args.voiceover_mode)
    reconciled_edit_plan, reconciled_voiceover = reconcile_edit_plan_to_voiceover(refined_edit_plan, voiceover)
    benchmark_report = build_benchmark_report(project, reconciled_edit_plan, quality_report)
    diagnostics = preview_delivery_diagnostics(reconciled_edit_plan, reconciled_voiceover)

    if guide is not None:
        write_json(output_dir / "guide.json", guide.model_dump(mode="json"))
    write_json(output_dir / "launch_script.json", effective_launch_script.model_dump(mode="json"))
    write_json(output_dir / "edit_plan.json", reconciled_edit_plan.model_dump(mode="json"))
    write_json(output_dir / "quality_report.json", quality_report.model_dump(mode="json"))
    write_json(output_dir / "benchmark_report.json", benchmark_report.model_dump(mode="json"))
    write_json(output_dir / "voiceover.json", reconciled_voiceover.model_dump(mode="json"))
    write_json(output_dir / "preview_delivery.json", dataclasses.asdict(diagnostics))

    print(f"Artifacts written to {output_dir}")
    print_scene_table("Guide", guide)
    print_edit_plan(reconciled_edit_plan)
    print_voiceover(reconciled_voiceover)
    print_reports(quality_report, benchmark_report, diagnostics)
    return 0


def probe_project(
    args: argparse.Namespace,
    transcript: list[TranscriptSegment],
    launch_script: LaunchScriptRecord,
    recording_session: RecordingSessionRecord,
) -> ProjectRecord:
    now = datetime.now(UTC)
    return ProjectRecord(
        id="phase-four-probe",
        project_name=args.project_name,
        product_name=args.product_name,
        product_description=args.product_description,
        target_audience=args.target_audience,
        video_goal=args.video_goal,
        status="planning",
        created_at=now,
        updated_at=now,
        transcript=transcript,
        launch_script=launch_script,
        recording_session=recording_session,
        template_config=TemplateConfigRecord(),
        manual_overrides=ManualOverrideRecord(),
    )


def print_scene_table(title: str, guide: GuideRecord | None) -> None:
    if guide is None:
        return
    print(f"\n{title}:")
    for step in guide.steps:
        print(
            f"- step {step.step_index}: {step.start:>6.2f}s -> {step.end:>6.2f}s | "
            f"{step.action_class:<14} | {step.title} | {step.narration}"
        )


def print_edit_plan(edit_plan: EditPlanRecord) -> None:
    print("\nEdit plan:")
    print(f"- total_duration_seconds={edit_plan.total_duration_seconds:.2f}")
    for scene in edit_plan.scenes:
        print(
            f"- scene {scene.scene_number}: {scene.start:>6.2f}s -> {scene.end:>6.2f}s "
            f"(render {scene.render_duration_seconds or 0:.2f}s) | "
            f"layout={scene.layout_mode:<14} camera={scene.camera_mode:<6} "
            f"zooms={len(scene.zooms)} highlights={len(scene.highlights)} "
            f"action_ts={scene.action_timestamp!s:<6} label={scene.title}"
        )
        print(f"  spoken: {scene.spoken_line}")
        if scene.captions:
            print(f"  caption: {scene.captions[0].text}")


def print_voiceover(voiceover: VoiceoverRecord) -> None:
    print("\nVoiceover:")
    print(f"- mode={voiceover.mode} status={voiceover.status} duration={voiceover.duration_seconds:.2f}s")
    for cue in voiceover.cues:
        print(f"- cue {cue.scene_number}: {cue.start:>5.2f}s -> {cue.end:>5.2f}s | {cue.text}")


def print_reports(
    quality_report: QualityReportRecord,
    benchmark_report: BenchmarkReportRecord,
    diagnostics,
) -> None:
    print("\nQuality:")
    print(f"- score={quality_report.score} ready_for_export={quality_report.ready_for_export}")
    for issue in quality_report.issues:
        scene_label = f"scene {issue.scene_number}" if issue.scene_number is not None else "global"
        print(f"- {scene_label}: {issue.code} ({issue.severity}) | {issue.message}")
    print("\nBenchmark:")
    print(f"- overall_score={benchmark_report.overall_score} verdict={benchmark_report.verdict}")
    print("\nPreview delivery:")
    print(
        f"- dynamic_scene_ratio={diagnostics.dynamic_scene_ratio:.2f} "
        f"highlight_scene_ratio={diagnostics.highlight_scene_ratio:.2f} "
        f"voiced_scene_ratio={diagnostics.voiced_scene_ratio:.2f} "
        f"avg_voice_words={diagnostics.avg_voice_words:.2f}"
    )


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def load_list(path: Path, model):
    payload = load_json(path)
    return [model.model_validate(item) for item in payload]


def load_optional_list(path: Path, model):
    if not path.exists():
        return None
    return load_list(path, model)


def load_existing_guide(artifact_dir: Path) -> GuideRecord | None:
    candidates = (
        artifact_dir / "phase-four-probe" / "guide.json",
        artifact_dir / "guide.json",
    )
    for path in candidates:
        if path.exists():
            return GuideRecord.model_validate(load_json(path))
    return None


def refreshed_existing_guide(
    project: ProjectRecord,
    transcript: list[TranscriptSegment],
    guide: GuideRecord,
    visual_analyses: list[VisualSceneAnalysisRecord] | None,
) -> GuideRecord:
    session = project.recording_session
    if session is None or not session.events:
        return guide
    clusters = cluster_events(session.events, transcript)
    if not clusters:
        return guide
    return reconcile_grounded_guide(guide, clusters, contextual_step_ranges(clusters, session), visual_analyses)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
