#!/usr/bin/env bash
# free_proxy 一键运行（首次自动建 venv 装依赖）
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "首次运行：创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

exec .venv/bin/python -m free_proxy -c "${1:-conf/config.yaml}"