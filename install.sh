#!/usr/bin/env bash
# free_proxy 一键安装脚本
# 用法：
#   bash install.sh                 # 安装（建 venv + 装依赖 + 生成配置 + 装 free-proxy 命令）
#   sudo bash install.sh --systemd  # 额外注册并启动 systemd 服务
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

err() { echo -e "\033[31m$*\033[0m" >&2; }
info() { echo -e "\033[36m==>\033[0m $*"; }
ok() { echo -e "\033[32m$*\033[0m"; }

info "检查 Python ..."
command -v "$PY" >/dev/null || { err "未找到 python3，请先安装 Python 3.9 或更高版本"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null \
  || { err "需要 Python >= 3.9，当前 $("$PY" -V 2>&1)"; exit 1; }

info "创建虚拟环境 .venv ..."
"$PY" -m venv .venv

info "安装 free_proxy 及依赖（镜像：$PIP_INDEX）..."
.venv/bin/pip install -q -i "$PIP_INDEX" . || { err "安装失败，请检查网络或改用 PIP_INDEX_URL 指定镜像"; exit 1; }

info "准备配置 ..."
if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  ok "已生成 config.yaml —— 请编辑并填入供应商凭证（密钥建议用 \${ENV} 环境变量）"
else
  ok "config.yaml 已存在，保留不动"
fi

if [ "${1:-}" = "--systemd" ]; then
  [ "$(id -u)" = "0" ] || { err "--systemd 需要 root：请用 sudo bash install.sh --systemd"; exit 1; }
  info "注册 systemd 服务 ..."
  cat > /etc/systemd/system/free-proxy.service <<EOF
[Unit]
Description=free_proxy - local IP proxy gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=$PWD
EnvironmentFile=$PWD/free_proxy.env
ExecStart=$PWD/.venv/bin/free-proxy -c $PWD/config.yaml
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
  [ -f free_proxy.env ] || cp deploy/systemd/free_proxy.env.example free_proxy.env
  systemctl daemon-reload
  systemctl enable --now free-proxy
  ok "systemd 服务已启动：systemctl status free-proxy 查看日志"
else
  ok "（未部署 systemd，可稍后用 sudo bash install.sh --systemd 注册为服务）"
fi

echo
ok "✅ 安装完成"
echo "  · 前台运行：  $PWD/.venv/bin/free-proxy -c $PWD/config.yaml"
echo "  · 前向代理：  127.0.0.1:8899"
echo "  · Web 控制台：http://127.0.0.1:8891"
echo "  · 配置供应商：编辑 $PWD/config.yaml"