#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Financial Timeline Engine — dev/preview launcher
# -----------------------------------------------------------------------------
# Runs BOTH processes needed by the iframe-embedded terminal:
#   1. FastAPI (api.main:app)  → serves frontend/* at "/" and /api/v1/* data
#   2. Streamlit (app (1) (9).py) → embeds the terminal via iframe
#
# The Streamlit app's terminal embed resolves to $FTE_TERMINAL_URL if set,
# otherwise http://localhost:5000/ — this script starts FastAPI on that port
# (override with FTE_TERMINAL_PORT). On Ctrl-C both processes are stopped.
set -euo pipefail
cd "$(dirname "$0")/.."

FTE_TERMINAL_PORT="${FTE_TERMINAL_PORT:-5000}"

# Start FastAPI (terminal SPA + API) in the background.
uvicorn api.main:app --host 0.0.0.0 --port "$FTE_TERMINAL_PORT" &
UV_PID=$!
trap 'kill "$UV_PID" 2>/dev/null || true' EXIT

# Give uvicorn a moment to bind before Streamlit renders the iframe.
sleep 2

# Streamlit stays in the foreground; Ctrl-C tears down uvicorn via the trap.
exec streamlit run 'app (1) (9).py' \
  --server.enableCORS false \
  --server.enableXsrfProtection false
