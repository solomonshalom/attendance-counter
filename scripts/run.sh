#!/usr/bin/env bash
# Starts the counter API + dashboard.
# By default runs the FastAPI server which serves both the built frontend and
# the API on http://localhost:8765. Pass --dev to also start `vite dev` for
# hot-reload frontend development.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COUNTER_DIR="$ROOT_DIR/counter"
FRONTEND_DIR="$ROOT_DIR/frontend"

DEV_MODE=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV_MODE=1 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

if [[ ! -d "$COUNTER_DIR/.venv" ]]; then
  echo "[run] Python venv missing. Run ./scripts/setup.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$COUNTER_DIR/.venv/bin/activate"

if [[ "$DEV_MODE" -eq 0 ]]; then
  if [[ ! -d "$FRONTEND_DIR/build" ]]; then
    echo "[run] Frontend build missing. Building once…"
    (cd "$FRONTEND_DIR" && npm run build)
  fi
  cd "$COUNTER_DIR"
  exec python run.py
fi

# Dev mode: counter on 8765, vite on 5173 (proxies api+ws to 8765).
cleanup() {
  if [[ -n "${VITE_PID:-}" ]] && kill -0 "$VITE_PID" 2>/dev/null; then
    kill "$VITE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

(cd "$FRONTEND_DIR" && npm run dev -- --host 127.0.0.1) &
VITE_PID=$!
echo "[run] Vite dev server started (pid=$VITE_PID)"

cd "$COUNTER_DIR"
python run.py
