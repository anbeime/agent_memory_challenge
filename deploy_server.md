# 部署到自有服务器（SecureCRT + SecureFX，你已选定）

> 适用：把 `agent_memory_challenge/` 传到你自己的 Linux 服务器（如 39.108.254.228），
> 常驻运行 `app.py`（FastAPI + SQLite + BM25），平台从公网调用 `/add` `/search` `/health`。
> 复用现有代码，**零改造**。

---

## 0. 你要准备

- 服务器：Linux（Ubuntu/Debian/CentOS 均可），公网 IP（阿里云 EIP 等）。
- 本机：SecureCRT（SSH 跑命令）+ SecureFX（SFTP 传文件）。
- 端口：服务监听 **8080**；服务器**防火墙 + 云安全组**都放行 8080 入站。

---

## 1. SecureFX 传文件

把整个 `agent_memory_challenge/` 传到服务器（如 `/opt/agent_memory_challenge/`）。
**排除**：`.venv/`、`__pycache__/`、`aml_mem.db`（本地测试库）。

> 关于密钥：`.env` 含 LDBD Key。SFTF 传上去 = 密钥落到你自己的服务器（可接受，端点本就要用它鉴权）。
> 若想更干净：服务器上单独建 `.env` 只写 `AML_API_KEY=<LDBD Key>` 即可（其余用默认）。

---

## 2. SecureCRT 一键部署

```bash
cd /opt/agent_memory_challenge
bash deploy_server.sh
```

脚本自动：建 venv → 装 fastapi/uvicorn/python-dotenv → 注册并启动 `aml-adapter` systemd 服务（无 systemd 则 nohup 兜底）。

自检：
```bash
curl http://127.0.0.1:8080/health
# → {"status":"ok",...}
```

---

## 3. 放行端口（两步都要）

**① 云厂商安全组**（阿里云控制台）：入方向添加 TCP 8080，来源 0.0.0.0/0。
**② 系统防火墙**（若开了 ufw/firewalld）：
```bash
sudo ufw allow 8080/tcp          # ufw
# 或
sudo firewall-cmd --add-port=8080/tcp --permanent && sudo firewall-cmd --reload  # firewalld
```

外网验证（本机浏览器/ curl 走公网 IP）：
```
http://<公网IP>:8080/health
```

---

## 4. 平台登记（agentmemoryleaderboard.ai/evaluation）

| 字段 | 填法 |
|---|---|
| System Name | `Antinet-AML` |
| Add URL | `http://<公网IP>:8080/add` |
| Search URL | `http://<公网IP>:8080/search` |
| Health URL | `http://<公网IP>:8080/health` |
| Auth Token | 填 **LDBD Key**（服务器 `.env` 的 `AML_API_KEY` 已设同值） |
| Concurrency | Add / Search 各 16（起步） |

> **HTTPS 问题**：若平台登记时报「需 HTTPS」→ 二选一：
> - **A（推荐，免域名）**：服务器上跑 `cloudflared tunnel --url http://localhost:8080`，用你已有 CF 账号拿 `https://xxxx.trycloudflare.com` 登记（CF 终止 TLS，服务器不用开公网端口）。
> - **B（需域名）**：nginx 反代 + certbot 免费证书（`apt install nginx certbot -y` → `certbot --nginx -d 你的域名`）。

---

## 5. 跑评测

1. 本机：`python smoke_test.py http://<公网IP>:8080` → `ALL SMOKE CHECKS PASSED ✅`
2. 平台先点 **Smoke**（私有，每小时 1 次）→ 通过。
3. 提交 **Full**（每 Key 每赛道最多 2 次，二次隔 30 天）。

---

## 6. 运维

```bash
systemctl status aml-adapter      # 看状态
systemctl restart aml-adapter     # 重启（改了代码后）
journalctl -u aml-adapter -f      # 看日志
```
- 数据库：`aml_mem.db`（SQLite，在部署目录）。评测产生记忆，容量几十 MB~1GB，无需干预。
- 升级高分：服务器 `.env` 设 `SEMANTIC=1` + 填 `DASHSCOPE_API_KEY`/`OPENROUTER_API_KEY` → 重启服务。
- 停服：`systemctl stop aml-adapter`。

---

## 7. 与 Cloudflare Tunnel 方案对比

| 项 | 自有服务器（本方案） | CF Tunnel（本机/服务器） |
|---|---|---|
| 数据控制 | ✅ 全在你机器 | ✅ 全在你机器 |
| 跨境延迟 | ⚠️ 中国机偏高 | ✅ CF 边缘低 |
| 是否需要域名 | 仅 HTTPS 时需要 | 不需要 |
| 运维 | 自己管服务 | 自己管服务 + 隧道 |
| 稳定性 | 高（机器常驻） | 高（CF 不休眠） |

> 想要「自有服务器 + 低延迟 + 免域名 HTTPS」：直接在**服务器上**跑 `cloudflared tunnel --url http://localhost:8080`，
> 计算仍在你机器，CF 只做 HTTPS 边缘与寻址。
