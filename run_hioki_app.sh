#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    PYTHON_BIN="$APP_DIR/.venv/bin/python"
else
    PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
fi

cd "$APP_DIR"
echo "$(date '+%F %T') [APP] Starting main.py with ${PYTHON_BIN}"
exec "$PYTHON_BIN" "$APP_DIR/main.py"
