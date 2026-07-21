from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.services.extraction_evaluator import evaluate_probe_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate raw_upload_probe output against an expected extraction flow.",
    )
    parser.add_argument("output_dir", help="Probe output directory that contains summary.json, recording_session.json, and launch_script.json.")
    parser.add_argument("--expected", required=True, help="Path to a JSON file containing expected_events, expected_canonical_script, and optional expected_screen_after.")
    parser.add_argument("--write-json", default="", help="Optional path to write the evaluation result as JSON.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    expected_path = Path(args.expected).expanduser().resolve()
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    evaluation = evaluate_probe_output(output_dir, expected if isinstance(expected, dict) else {})
    payload = {
        "overall_score": evaluation.overall_score,
        "verdict": evaluation.verdict,
        "metrics": [
            {"name": item.name, "score": item.score, "detail": item.detail}
            for item in evaluation.metrics
        ],
        "findings": evaluation.findings,
    }
    print(f"Overall: {evaluation.overall_score}/100")
    print(f"Verdict: {evaluation.verdict}")
    for metric in evaluation.metrics:
        print(f"- {metric.name}: {metric.score:.2f} | {metric.detail}")
    if evaluation.findings:
        print("Findings:")
        for finding in evaluation.findings:
            print(f"- {finding}")
    if args.write_json:
        output_path = Path(args.write_json).expanduser().resolve()
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
