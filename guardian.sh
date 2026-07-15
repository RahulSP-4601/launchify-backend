#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

run_limits() {
  python3 "$ROOT_DIR/scripts/guardian_limits.py" "$ROOT_DIR/app" "$ROOT_DIR/scripts"
}

run_types() {
  "$VENV_PYTHON" -m mypy app
}

run_syntax() {
  "$VENV_PYTHON" -m py_compile app/main.py app/api/routes.py app/core/config.py
}

run_smoke() {
  PYTHONPATH="$ROOT_DIR" "$VENV_PYTHON" scripts/health_smoke.py
}

main() {
  if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "Backend guardian failed: missing virtualenv at launchify-backend/.venv"
    exit 1
  fi

  echo "Running backend guardian..."
  run_limits
  run_types
  run_syntax
  run_smoke
  echo "Backend guardian passed."
}

main "$@"
