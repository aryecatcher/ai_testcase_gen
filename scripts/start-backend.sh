#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
source .venv/bin/activate

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8002}"

exec python -m uvicorn src.api.main:app --host "$HOST" --port "$PORT"
