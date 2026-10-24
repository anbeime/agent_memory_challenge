#!/usr/bin/env bash
# ============================================================
# 自有服务器部署脚本（Linux：Ubuntu/Debian/CentOS 均可，无需 root）
# 用法（SecureCRT 连上服务器后执行）：
#   cd ~/agent_memory_challenge
#   bash deploy_server.sh
# 作用：
#   1) 自动选定 Python >= 3.8（系统已有则用之，否则装 Miniconda 到 ~/miniconda3）
#   2) 建 venv + 装依赖（若已有 .venv 但版本 < 3.8 会自动丢弃重建）
#   3) 注册并启动 systemd 常驻服务（无 systemd / 非 root 则 nohup 兜底）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

# ---- 1) 选定解释器：优先 3.8+，其次 conda env aml，否则自动装 Miniconda ----
pick_python() {
  for c in python3.12 python3.11 python3.10 python3.9 python3.8 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      v=$("$c" -c 'import sys;print(sys.version_info[:2])' 2>/dev/null || true)
      [ -z "$v" ] && continue
      maj=$(echo "$v" | cut -d, -f1 | tr -d '(')
      min=$(echo "$v" | cut -d, -f2 | tr -d ') ')
      if [ "$maj" -gt 3 ] || { [ "$maj" -eq 3 ] && [ "$min" -ge 8 ]; }; then
        echo "$c"; return 0
      fi
    fi
  done
  if [ -x "$HOME/miniconda3/envs/aml/bin/python" ]; then
    echo "$HOME/miniconda3/envs/aml/bin/python"; return 0
  fi
  return 1
}

if ! PY=$(pick_python); then
  echo "✗ 未找到 Python 3.8+，自动安装 Miniconda（无需 root，装到 ~/miniconda3）..."
  if command -v wget >/dev/null 2>&1; then
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
  else
    curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
  fi
  bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
  "$HOME/miniconda3/bin/conda" create -y -n aml python=3.11
  PY="$HOME/miniconda3/envs/aml/bin/python"
fi

echo "[1/4] Python: $($PY --version 2>&1)"

# 若已有 .venv 但解释器版本 < 3.8，丢弃重建
if [ -d .venv ] && [ -x .venv/bin/python ]; then
  MAJ=$(".venv/bin/python" -c 'import sys;print(sys.version_info.major)' 2>/dev/null || echo 0)
  MIN=$(".venv/bin/python" -c 'import sys;print(sys.version_info.minor)' 2>/dev/null || echo 0)
  if [ "$MAJ" -lt 3 ] || { [ "$MAJ" -eq 3 ] && [ "$MIN" -lt 8 ]; }; then
    echo "    现有 .venv 是 Python $MAJ.$MIN（过旧），删除重建"
    rm -rf .venv
  fi
fi

# ---- 2) venv + 依赖 ----
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "[2/4] 已创建虚拟环境"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "[2/4] 依赖安装完成"

# ---- 3) .env 密钥检查 ----
if [ ! -f .env ]; then
  echo "[warn] 未发现 .env → 鉴权关闭（仅本地可用）。"
  echo "       生产请在服务器建 .env，写入： AML_API_KEY=<你的 LDBD Key>"
else
  echo "[3/4] 检测到 .env，鉴权按 AML_API_KEY 开启"
fi

# ---- 4) 注册 systemd 服务（root 且有 systemd 时） ----
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
  # 先杀掉可能残留的旧进程，避免端口冲突
  pkill -f 'python app.py' 2>/dev/null || true
  sleep 1
  nohup .venv/bin/python app.py > aml.log 2>&1 &
  sleep 3
  echo "       已后台启动，日志 aml.log；进程: $(pgrep -f 'python app.py' || echo 无)"
fi

echo "------------------------------------------------------------"
echo "自检： curl http://127.0.0.1:8080/health"
echo "外网： http://<你的公网IP>:8080/health  （需放行防火墙/安全组 8080）"
echo "------------------------------------------------------------"
