#!/usr/bin/env bash
set -euo pipefail

BACKEND_SERVICE="ai-testcase-backend"
LEGACY_SERVICE="ai-testcase-legacy-ui"
STOP_LEGACY="${STOP_LEGACY_UI:-false}"

for arg in "$@"; do
  if [ "$arg" = "--with-legacy" ]; then
    STOP_LEGACY="true"
  fi
done

service_exists() {
  systemctl list-unit-files --type=service 2>/dev/null | grep -Fq "$1.service"
}

if [ "$STOP_LEGACY" = "true" ] && service_exists "$LEGACY_SERVICE"; then
  echo "停止 Legacy UI 服务"
  sudo systemctl stop "$LEGACY_SERVICE"
fi

echo "停止后端服务"
sudo systemctl stop "$BACKEND_SERVICE"

echo "停止 nginx"
sudo systemctl stop nginx

echo "服务已停止。"
