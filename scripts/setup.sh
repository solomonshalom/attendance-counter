#!/usr/bin/env bash
# Sets up the counter Python venv and the frontend Node deps.
# Idempotent: safe to re-run.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COUNTER_DIR="$ROOT_DIR/counter"
FRONTEND_DIR="$ROOT_DIR/frontend"

# ---- Python -----------------------------------------------------------------

PY_BIN="${PYTHON_BIN:-}"
if [[ -z "$PY_BIN" ]]; then
  for candidate in python3.12 python3.11 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PY_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$PY_BIN" ]]; then
  echo "[setup] No Python interpreter found. Install Python 3.11+ first." >&2
  exit 1
fi

PY_VERSION="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "[setup] Using Python $PY_VERSION ($PY_BIN)"

if [[ ! -d "$COUNTER_DIR/.venv" ]]; then
  echo "[setup] Creating venv at counter/.venv"
  "$PY_BIN" -m venv "$COUNTER_DIR/.venv"
fi

# shellcheck disable=SC1091
source "$COUNTER_DIR/.venv/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null
echo "[setup] Installing Python deps (this can take a few minutes the first time)"
pip install -r "$COUNTER_DIR/requirements.txt"
deactivate

# ---- Frontend ---------------------------------------------------------------

if ! command -v npm >/dev/null 2>&1; then
  echo "[setup] npm not found. Install Node 18+ first (e.g. via Homebrew: brew install node)." >&2
  exit 1
fi

echo "[setup] Installing frontend deps"
(cd "$FRONTEND_DIR" && npm install --silent)

echo "[setup] Building frontend"
(cd "$FRONTEND_DIR" && npm run build)

echo
echo "[setup] Done."
echo "  Run the system: ./scripts/run.sh"
echo "  Open the UI:    http://localhost:8765"
