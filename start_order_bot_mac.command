#!/bin/sh
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR" || exit 1

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating Python virtual environment..."
  python3 -m venv .venv || exit 1
fi

PYTHON=".venv/bin/python"

"$PYTHON" -c "import playwright" >/dev/null 2>&1
if [ $? -ne 0 ]; then
  echo "Installing Python dependencies..."
  "$PYTHON" -m pip install -r requirements.txt || exit 1
fi

"$PYTHON" -m playwright install chromium || exit 1

exec "$PYTHON" -m order_bot.gui
