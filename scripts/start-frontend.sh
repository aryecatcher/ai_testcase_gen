#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR/frontend"

export VITE_BACKEND_URL="${VITE_BACKEND_URL:-http://127.0.0.1:8002}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5173}"

exec npm run dev -- --host "$HOST" --port "$PORT"
