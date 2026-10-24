#!/usr/bin/env bash
# ============================================================
# 自有服务器部署脚本（Linux：Ubuntu/Debian/CentOS 均可）
# 用法（SecureCRT 连上服务器后执行）：
#   cd /path/to/agent_memory_challenge
#   bash deploy_server.sh
# 作用：建 venv + 装依赖 + 注册并启动 systemd 常驻服务（无 systemd 则 nohup 兜底）
# ============================================================
set -u
cd "$(dirname "$0")"

PY=$(command -v python3 || command -v python)
echo "[1/4] Python: $($PY --version 2>&1)"

# ---- venv + 依赖 ----
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "[2/4] 已创建虚拟环境"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "[2/4] 依赖安装完成"

# ---- .env 密钥检查 ----
if [ ! -f .env ]; then
  echo "[warn] 未发现 .env → 鉴权关闭（仅本地可用）。"
  echo "       生产请在服务器建 .env，写入： AML_API_KEY=<你的 LDBD Key>"
else
  echo "[3/4] 检测到 .env，鉴权按 AML_API_KEY 开启"
fi

# ---- 注册 systemd 服务（root 且有 systemd 时） ----
if [ "$(id -u)" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
  SVC=/etc/systemd/system/aml-adapter.service
  cat > "$SVC" <<EOF
[Unit]
Description=Antinet-AML Adapter (Agent Memory Challenge)
After=network.target

[Service]
Type=simple
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/python app.py
Environment=AML_PORT=8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now aml-adapter
  sleep 2
  systemctl status aml-adapter --no-pager || true
  echo "[4/4] systemd 服务已启动（aml-adapter）"
else
  echo "[4/4] 无 systemd / 非 root → 用 nohup 后台启动："
  nohup .venv/bin/python app.py > aml.log 2>&1 &
  sleep 2
  echo "       已后台启动，日志 aml.log；进程: $(pgrep -f 'python app.py')"
fi

echo "------------------------------------------------------------"
echo "自检： curl http://127.0.0.1:8080/health"
echo "外网： http://<你的公网IP>:8080/health  （需放行防火墙/安全组 8080）"
echo "------------------------------------------------------------"
