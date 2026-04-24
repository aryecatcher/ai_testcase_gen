#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

choose_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  echo "未找到 Python，请先安装 python3。" >&2
  return 1
}

contains_profile_arg() {
  for arg in "$@"; do
    if [ "$arg" = "--profile" ]; then
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(choose_python)"
CONFIG_ARGS=("$@")

if ! contains_profile_arg "$@"; then
  CONFIG_ARGS=(--profile linux "${CONFIG_ARGS[@]}")
fi

echo "=== AI Test Case Generator CentOS 首次安装 ==="
echo "步骤 1/2：执行配置向导"
"$PYTHON_BIN" "$ROOT_DIR/scripts/configure.py" "${CONFIG_ARGS[@]}"

if [ ! -f "$ROOT_DIR/deploy/generated/install_centos.sh" ]; then
  echo "未找到 deploy/generated/install_centos.sh。" >&2
  echo "请确认配置向导已按 Linux 模式成功生成部署脚本。" >&2
  exit 1
fi

echo
echo "步骤 2/2：执行 CentOS 安装脚本"
bash "$ROOT_DIR/deploy/generated/install_centos.sh"

echo
echo "首次安装完成。"
echo "后续启动：bash scripts/start-centos-services.sh"
echo "查看状态：bash scripts/status-centos-services.sh"
echo "停止服务：bash scripts/stop-centos-services.sh"
