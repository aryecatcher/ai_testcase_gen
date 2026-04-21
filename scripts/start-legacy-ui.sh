#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"
source .venv/bin/activate

PORT="${PORT:-8504}"

exec streamlit run ui/main.py --server.port "$PORT"
