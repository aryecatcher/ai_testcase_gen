#!/usr/bin/env bash
set -euo pipefail

BACKEND_SERVICE="ai-testcase-backend"
LEGACY_SERVICE="ai-testcase-legacy-ui"
SHOW_LEGACY="${SHOW_LEGACY_UI:-false}"

for arg in "$@"; do
  if [ "$arg" = "--with-legacy" ]; then
    SHOW_LEGACY="true"
  fi
done

service_exists() {
  systemctl list-unit-files --type=service 2>/dev/null | grep -Fq "$1.service"
}

print_status() {
  local service_name="$1"
  if service_exists "$service_name"; then
    sudo systemctl status "$service_name" --no-pager
  else
    echo "$service_name.service 未安装。"
  fi
}

echo "=== nginx ==="
sudo systemctl status nginx --no-pager
echo

echo "=== $BACKEND_SERVICE ==="
print_status "$BACKEND_SERVICE"
echo

if [ "$SHOW_LEGACY" = "true" ]; then
  echo "=== $LEGACY_SERVICE ==="
  print_status "$LEGACY_SERVICE"
  echo
fi
