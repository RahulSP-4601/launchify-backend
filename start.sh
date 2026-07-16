#!/bin/sh
set -eu

ROLE="${PROCESS_ROLE:-web}"
PORT_VALUE="${PORT:-8000}"

case "$ROLE" in
  web)
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT_VALUE"
    ;;
  worker)
    exec python -m app.worker
    ;;
  all)
    exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT_VALUE"
    ;;
  *)
    echo "Unknown PROCESS_ROLE: $ROLE" >&2
    exit 1
    ;;
esac
