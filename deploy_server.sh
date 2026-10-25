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
# 说明：CentOS7 = GLIBC 2.17，新版 Miniconda 安装器要求 2.28，故用旧版安装器
#       （4.7.12.1 等，自带 Python3.8，兼容 GLIBC2.17）。镜像走清华（国内快）。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

# ---- 1) 选定解释器：优先 3.8+，其次 miniconda base，否则自动装旧版 Miniconda ----
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
  if [ -x "$HOME/miniconda3/bin/python" ]; then
    echo "$HOME/miniconda3/bin/python"; return 0
  fi
  return 1
}

if ! PY=$(pick_python); then
  echo "✗ 未找到 Python 3.8+，自动安装旧版 Miniconda（清华镜像，兼容 CentOS7/GLIBC2.17）..."
  MC_CANDIDATES="
https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-4.7.12.1-Linux-x86_64.sh
https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-4.9.2-Linux-x86_64.sh
https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-py38_4.8.3-Linux-x86_64.sh
"
  for u in $MC_CANDIDATES; do
    echo "  尝试: $u"
    if curl -fsSL --connect-timeout 20 --retry 2 "$u" -o /tmp/miniconda.sh; then
      sz=$(wc -c < /tmp/miniconda.sh)
      if [ "$sz" -gt 50000000 ]; then echo "  下载成功 ($sz bytes)"; break; fi
    fi
  done
  rm -rf "$HOME/miniconda3"
  bash /tmp/miniconda.sh -b -p "$HOME/miniconda3"
  PY="$HOME/miniconda3/bin/python"
fi

echo "[1/4] Python: $($PY --version 2>&1)"

# 若已有 .venv 但解释器版本 < 3.8，丢弃重建
if [ -d .venv ] && [ -x .venv/bin/python ]; then
  MAJ=$(.venv/bin/python -c 'import sys;print(sys.version_info.major)' 2>/dev/null || echo 0)
  MIN=$(.venv/bin/python -c 'import sys;print(sys.version_info.minor)' 2>/dev/null || echo 0)
  if [ "$MAJ" -lt 3 ] || { [ "$MAJ" -eq 3 ] && [ "$MIN" -lt 8 ]; }; then
    echo "    现有 .venv 是 Python $MAJ.$MIN（过旧），删除重建"
    rm -rf .venv
  fi
fi

# ---- 2) venv + 依赖（pip 走清华 PyPI 镜像）----
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  echo "[2/4] 已创建虚拟环境"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip -q -i https://pypi.tuna.tsinghua.edu.cn/simple
pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
echo "[2/4] 依赖安装完成"

# ---- 3) .env 密钥检查 ----
if [ ! -f .env ]; then
  echo "[warn] 未发现 .env → 鉴权关闭（仅本地可用）。"
  echo "       生产请在服务器建 .env，写入： AML_API_KEY=<你的 LDBD Key>"
else
  echo "[3/4] 检测到 .env，鉴权按 AML_API_KEY 开启"
fi

# ---- 4) 注册 systemd 服务（root 且有 systemd 时）----
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
