#!/usr/bin/env bash
set -euo pipefail

BACKEND_SERVICE="ai-testcase-backend"
LEGACY_SERVICE="ai-testcase-legacy-ui"
START_LEGACY="${START_LEGACY_UI:-false}"

for arg in "$@"; do
  if [ "$arg" = "--with-legacy" ]; then
    START_LEGACY="true"
  fi
done

service_exists() {
  systemctl list-unit-files --type=service 2>/dev/null | grep -Fq "$1.service"
}

echo "启动 nginx"
sudo systemctl start nginx

echo "启动后端服务"
sudo systemctl start "$BACKEND_SERVICE"

if [ "$START_LEGACY" = "true" ]; then
  if service_exists "$LEGACY_SERVICE"; then
    echo "启动 Legacy UI 服务"
    sudo systemctl start "$LEGACY_SERVICE"
  else
    echo "未检测到 $LEGACY_SERVICE.service，已跳过。"
  fi
fi

echo
echo "当前服务状态："
bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/status-centos-services.sh" "$@"
