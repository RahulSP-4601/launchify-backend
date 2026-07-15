#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

MAX_FILE_LINES = 500
MAX_FUNCTION_LINES = 50
IGNORE_PARTS = {".git", ".venv", "__pycache__"}
SOURCE_SUFFIXES = {".py"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guard backend file and function size limits.")
    parser.add_argument("roots", nargs="+", help="Directories to scan")
    return parser.parse_args()


def should_skip(path: Path) -> bool:
    return any(part in IGNORE_PARTS for part in path.parts)


def iter_source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in SOURCE_SUFFIXES and not should_skip(path):
            files.append(path)
    return files


def count_file_lines(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def function_spans(path: Path) -> list[tuple[str, int, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    spans: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            spans.append((node.name, start, end - start + 1))
    return spans


def file_violations(path: Path) -> list[str]:
    violations: list[str] = []
    line_count = count_file_lines(path)
    if line_count > MAX_FILE_LINES:
        violations.append(f"{path}: file has {line_count} lines (max {MAX_FILE_LINES})")

    for name, start, size in function_spans(path):
        if size > MAX_FUNCTION_LINES:
            violations.append(
                f"{path}:{start}: function '{name}' has {size} lines (max {MAX_FUNCTION_LINES})",
            )
    return violations


def main() -> int:
    args = parse_args()
    violations: list[str] = []
    for root_arg in args.roots:
        root = Path(root_arg).resolve()
        for path in iter_source_files(root):
            violations.extend(file_violations(path))

    if violations:
        print("Backend guardian limits failed:")
        for violation in violations:
            print(f"  - {violation}")
        return 1

    print("Backend guardian limits passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
