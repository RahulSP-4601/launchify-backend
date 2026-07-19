from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.models.projects import ProjectRecord
from app.services.grounded_script_refinement import refine_launch_script_with_events, refine_launch_script_with_visuals
from app.services.inference_step_builder import build_inference_script
from app.services.inferred_recording_session import infer_recording_session
from app.services.transcription import transcribe_media_file
from app.services.visual_analysis import analyze_video_scenes, visual_analysis_available


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the manual-upload extraction path against a local raw video and dump intermediate artifacts.",
    )
    parser.add_argument("video_path", help="Path to the raw walkthrough video file.")
    parser.add_argument(
        "--output-dir",
        default="tmp/raw-upload-probe",
        help="Directory where JSON artifacts will be written.",
    )
    parser.add_argument("--project-name", default="Pronouncly walkthrough probe")
    parser.add_argument("--product-name", default="Pronouncly")
    parser.add_argument("--product-description", default="Local raw upload extraction probe.")
    parser.add_argument("--target-audience", default="Internal QA")
    parser.add_argument("--video-goal", default="launch_video")
    parser.add_argument(
        "--content-type",
        default="",
        help="Optional content type override. Defaults to the mime type inferred from the file extension.",
    )
    parser.add_argument(
        "--skip-vision",
        action="store_true",
        help="Skip scene visual analysis and only emit transcript plus coarse script artifacts.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    video_path = Path(args.video_path).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    ensure_video_exists(video_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    content_type = args.content_type or inferred_content_type(video_path)
    project = probe_project(args)

    print(f"[1/4] Transcribing {video_path.name}")
    transcript = transcribe_media_file(video_path, content_type)
    write_json(output_dir / "transcript.json", [segment.model_dump(mode="json") for segment in transcript])

    print(f"[2/4] Building inference script from {len(transcript)} transcript segments")
    launch_script, scene_ranges = build_inference_script(project, transcript)
    write_json(output_dir / "launch_script.json", launch_script.model_dump(mode="json"))
    write_json(output_dir / "scene_ranges.json", scene_ranges)

    visual_analyses = None
    refined_launch_script = launch_script
    if args.skip_vision:
        print("[3/4] Skipping visual analysis by request")
    elif not visual_analysis_available():
        print("[3/4] Visual analysis unavailable; continuing without scene analyses")
    else:
        print(f"[3/4] Running visual analysis across {len(launch_script.scenes)} inferred scenes")
        visual_analyses = analyze_video_scenes(video_path, launch_script, transcript, scene_ranges)
        refined_launch_script = refine_launch_script_with_visuals(launch_script, visual_analyses)
        write_json(output_dir / "launch_script.json", refined_launch_script.model_dump(mode="json"))
        write_json(
            output_dir / "visual_analyses.json",
            [analysis.model_dump(mode="json") for analysis in visual_analyses],
        )

    print("[4/4] Inferring grounded recording session events")
    recording_session = infer_recording_session(project, video_path, launch_script, transcript, visual_analyses)
    if recording_session is not None:
        refined_launch_script = refine_launch_script_with_events(refined_launch_script, recording_session.events)
        write_json(output_dir / "launch_script.json", refined_launch_script.model_dump(mode="json"))
        write_json(output_dir / "recording_session.json", recording_session.model_dump(mode="json"))

    write_json(
        output_dir / "summary.json",
        build_summary(video_path, content_type, transcript, refined_launch_script, scene_ranges, visual_analyses, recording_session),
    )
    print_results(output_dir, recording_session)
    return 0


def ensure_video_exists(video_path: Path) -> None:
    if not video_path.exists():
        raise SystemExit(f"Video file not found: {video_path}")
    if not video_path.is_file():
        raise SystemExit(f"Video path is not a file: {video_path}")


def inferred_content_type(video_path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(str(video_path))
    return guessed or "application/octet-stream"


def probe_project(args: argparse.Namespace) -> ProjectRecord:
    now = datetime.now(UTC)
    return ProjectRecord(
        id="raw-upload-probe",
        project_name=args.project_name,
        product_name=args.product_name,
        product_description=args.product_description,
        target_audience=args.target_audience,
        video_goal=args.video_goal,
        status="uploading",
        created_at=now,
        updated_at=now,
    )


def build_summary(
    video_path: Path,
    content_type: str,
    transcript: list,
    launch_script,
    scene_ranges: list[tuple[float, float]],
    visual_analyses,
    recording_session,
) -> dict[str, object]:
    return {
        "video_path": str(video_path),
        "content_type": content_type,
        "transcript_segments": len(transcript),
        "transcript_duration_seconds": round(max((segment.end for segment in transcript), default=0.0), 2),
        "script_scene_count": len(launch_script.scenes),
        "scene_ranges": scene_ranges,
        "visual_analysis_count": 0 if visual_analyses is None else len(visual_analyses),
        "recording_event_count": 0 if recording_session is None else len(recording_session.events),
        "grounding_diagnostics": {} if recording_session is None else recording_session.grounding_diagnostics,
    }


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def print_results(output_dir: Path, recording_session) -> None:
    print(f"Artifacts written to {output_dir}")
    if recording_session is None:
        print("No recording session events were inferred.")
        return
    print(f"Inferred {len(recording_session.events)} events.")
    for event in recording_session.events:
        label = event.target.label or event.target.text or event.target.selector or "(unlabeled)"
        print(f"- {event.timestamp:>6.2f}s  {event.type:<10}  {label}")


if __name__ == "__main__":
    raise SystemExit(main())
