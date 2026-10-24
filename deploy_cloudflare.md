# 部署到公网：Cloudflare Tunnel（推荐，零成本、零信用卡、你已有账号）

> 适用：把本机 `agent_memory_challenge` 的 FastAPI 服务暴露成公网 HTTPS 端点，
> 供 Agent Memory Leaderboard 平台调用 `/add` `/search` `/health`。
> 复用现有 `app.py` + `store.py`（SQLite+BM25），**无需任何代码改造**。
>
> 为什么选它：免费、不休眠、URL 稳定、CF 全球边缘 → 到境外评测机延迟低；
> 比 ngrok（URL 变/易超时）、阿里云中国机（跨境延迟高）都更稳。

---

## 0. 前置：本机先起服务（必须一直开着）

```bat
:: start_local.bat
cd /d C:\D\zhiyi\agent_memory_challenge
call .venv\Scripts\activate
:: AML_API_KEY 已在 .env（=LDBD Key），服务会做恒定时间鉴权
python app.py
:: 监听 127.0.0.1:8080
```
> 评测期间**这台机器不能睡眠/关机**，隧道和服务都要常驻。

---

## 1. 方式 A：Quick Tunnel（零配置，今天就能用 ✅）

不需要域名、不需要登录账号，一条命令拿公网 URL：

```bat
:: 安装 cloudflared（一次）
winget install --id Cloudflare.cloudflared -e
:: 或 https://github.com/cloudflare/cloudflared/releases 下 Windows 版

:: 起隧道，把本机 8080 暴露出去
cloudflared tunnel --url http://localhost:8080
```

输出里会有形如 `https://xxxx.trycloudflare.com` 的地址 → 这就是你的公网端点：

```
Add    = https://xxxx.trycloudflare.com/add
Search = https://xxxx.trycloudflare.com/search
Health = https://xxxx.trycloudflare.com/health
```

- ✅ 免费、HTTPS、无休眠、CF 边缘加速
- ⚠️ URL 在**每次重启隧道时变化**（进程内稳定）。适合 Smoke + 单次 Full。
- 跑 Full 前：先起隧道拿到 URL → 立刻去平台登记 → 提交（评测窗口内保持隧道+服务在线）。

---

## 2. 方式 B：Named Tunnel + 你的域名（稳定，适合最终 Full）

如果你在 Cloudflare 有托管域名（如 `yourdomain.com`），可固定子域：

```bat
cloudflared login                      :: 浏览器授权到你的 CF 账号
cloudflared tunnel create aml-adapter  :: 创建命名隧道，记下 tunnel-id
cloudflared tunnel route dns aml-adapter aml.yourdomain.com
:: 常驻运行（建议用服务/后台）：
cloudflared tunnel run --url http://localhost:8080 aml-adapter
```

得到**永久稳定** `https://aml.yourdomain.com`，评测期间无需重填 URL。

> 你没有额外域名时，直接用方式 A 即可，竞赛足够。

---

## 3. 平台登记（agentmemoryleaderboard.ai/evaluation）

| 字段 | 填法 |
|---|---|
| System Name | `Antinet-AML` |
| Version | `0.1.0` |
| Add URL | `https://<你的隧道域名>/add` |
| Search URL | `https://<你的隧道域名>/search` |
| Health URL | `https://<你的隧道域名>/health` |
| Auth Token | 填 **LDBD Key**（服务 `AML_API_KEY` 已设同值，恒定时间比对通过） |
| Concurrency | Add / Search 各 16（起步；本机 BM25 单机可扛） |

> 平台硬要求：URL 必须是公网 HTTPS、**不得含凭据/回环/私有地址** → CF 隧道全满足。

---

## 4. 评测前自检（本机跑）

```bat
python smoke_test.py https://<你的隧道域名>
```
`ALL SMOKE CHECKS PASSED ✅` → 去平台点 **Smoke**（私有，每小时 1 次）。

---

## 5. 关键提醒

- **评测窗口内机器必须在线**：Full 每 Key 每赛道仅 **2 次**、二次须隔 30 天 → 首次要稳。
- **数据不出域**：默认 `bm25` 模式纯本地，记忆留你机器；平台发来的评测题是正常评测交互。
- **不要**在 Search 里答题 / 硬编码 / 跨 user_id 共享 → 红线取消资格。
- 如需更高分：开 `SEMANTIC=1`（需填 `DASHSCOPE_API_KEY`/`OPENROUTER_API_KEY`）走语义 RRF，但会出网调 API。

---

## 6. 与其他方案对比（为什么是它）

| 方案 | 跨境延迟 | 稳定性 | 成本 | 结论 |
|---|---|---|---|---|
| **CF Tunnel（本机）** | ✅ 低（CF 边缘） | ✅ 不休眠 | 免费 | **首选** |
| 阿里云 39.108（中国） | ❌ 高 | 高 | 已有 | 仅备用 |
| ngrok | 中 | ❌ URL 变 | 免费 | 仅 Smoke |
| GCP 美区 VM | ✅ 低 | 高 | 免费档 | 备用（需信用卡） |
| HF/免费 Render | 中 | ❌ 休眠 | 免费 | 不推荐（评测命中休眠→超时） |
