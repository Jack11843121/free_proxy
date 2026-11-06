#!/usr/bin/env bash
# free_proxy 一键安装脚本
#   bash install.sh                 # 安装在当前目录（venv + conf/config.yaml + free-proxy 命令）
#   sudo bash install.sh --systemd  # 额外注册并启动 systemd 服务
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
DEPLOY_DIR="$(pwd)"

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

info "准备目录结构与配置 ..."
mkdir -p conf logs data
if [ ! -f conf/config.yaml ]; then
  cp conf/config.example.yaml conf/config.yaml
  ok "已生成 conf/config.yaml —— 请编辑并填入供应商凭证（密钥建议用 \${ENV} 环境变量）"
else
  ok "conf/config.yaml 已存在，保留不动"
fi
[ -f conf/free_proxy.env ] || cp conf/free_proxy.env.example conf/free_proxy.env
[ -f VERSION ] && cp VERSION "$DEPLOY_DIR/VERSION" 2>/dev/null || true

if [ "${1:-}" = "--systemd" ]; then
  [ "$(id -u)" = "0" ] || { err "--systemd 需要 root：请用 sudo bash install.sh --systemd"; exit 1; }
  info "注册 systemd 服务 ..."
  cat > /etc/systemd/system/free-proxy.service <<EOF
[Unit]
Description=free_proxy - local IP proxy gateway
After=network.target

[Service]
Type=simple
WorkingDirectory=$DEPLOY_DIR
EnvironmentFile=$DEPLOY_DIR/conf/free_proxy.env
ExecStart=$DEPLOY_DIR/.venv/bin/free-proxy -c $DEPLOY_DIR/conf/config.yaml
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now free-proxy
  ok "systemd 服务已启动：systemctl status free-proxy 查看日志"
else
  ok "未部署 systemd；可手动 bin/free-proxy start，或 sudo bash install.sh --systemd 注册服务"
fi

echo
ok "✅ 安装完成"
echo "  目录结构：conf/（配置） logs/（日志） data/（pid） bin/（管理脚本）"
echo "  · 前台运行：  $DEPLOY_DIR/.venv/bin/free-proxy -c $DEPLOY_DIR/conf/config.yaml"
echo "  · 后台守护：  $DEPLOY_DIR/bin/free-proxy start"
echo "  · 前向代理：  127.0.0.1:8899"
echo "  · Web 控制台：http://127.0.0.1:8891"