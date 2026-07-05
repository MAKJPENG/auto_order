#!/bin/sh
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv
fi

PYTHON=".venv/bin/python"
ARGS="tools/build_release.py --target mac"

if [ "${1:-}" = "--timestamp" ] && [ -n "${2:-}" ]; then
  ARGS="$ARGS --timestamp $2"
  shift 2
fi

exec "$PYTHON" $ARGS "$@"
