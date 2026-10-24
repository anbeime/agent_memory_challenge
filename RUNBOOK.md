# Agent Memory Challenge（CSIG）Cycle 2 — 完成手册

> 系统名建议：**Antinet-AML**（基于知易 Antinet 太史阁记忆层封装）
> 赛道建议：**Textual · Academic Methods（开放方法）**
> 关键日期：材料截止 **2026-10-31 23:59 UTC+8** | 评测截止 **2026-11-04** | 当前剩余 ≈8 天
> 奖金池：**US$22,000**（仅开放方法可参评）

---

## 0. 这个比赛到底比什么

平台固定「数据 / Answer 模型 / 裁判」，你只负责**记忆系统的写入与检索**：

```
你实现                        平台负责
POST /add   (写记忆)     ──▶  统一 Answer 生成
POST /search(检索记忆)   ──▶  统一 Eval 打分
GET  /health             ──▶  审计 / 排行榜
```

- 你**不能**在 Search 里直接出最终答案，也不能硬编码题目、不能跨样本共享状态。
- 平台把你的检索结果喂给**统一的 Answer 模型**生成答案再评分 → 比的是「记忆写得好不好、检索得准不准」。

---

## 1. 官方契约（已对照 ReFind #2 提交验证）

### Add  `POST /add` （别名 `/v1/memories/add`）
请求：
```json
{
  "request_id": "eval:run:locomo:conv-0:chunk-0",
  "messages": [{"role":"user","timestamp":1704067200000,"content":"memory text"}],
  "user_id": "eval:run:locomo:conv-0",
  "session_id": "eval:run:sample:0"
}
```
- `request_id`/`user_id`/`session_id` 必填；`messages` 有序，每条含 `role`+非空 `content`，`timestamp` 可选（Unix 毫秒）。
- **同步语义**：写完且可立即检索后才返回 200；不要返回 202 / task id。
- 响应（严格回显，HTTP 200）：
```json
{"success": true, "request_id":"...", "user_id":"...", "session_id":"..."}
```

### Search  `POST /search` （别名 `/v1/memories/search`）
请求：
```json
{"query":"Which answer best matches the memory?",
 "options":["A. ...","B. ..."], "user_id":"eval:run:locomo:conv-0", "top_k":100}
```
- `options` 可选（选择题才有），**不得用选项推断答案**。
- `top_k` 必填，返回不得超此值；官方外部评测固定 **100**。
- 响应（官方 schema，逐字确认）：
```json
{"data":[{"id":"1","content":"[2024-01-01T00:00:00Z] USER: I adopted a cat named Luna.","score":1.25,"created_at":"2026-08-07T12:00:00Z"}]}
```
  无命中 → `{"data":[]}`。记忆**绝不跨 user_id**、**绝不超 top_k**。

### Health `GET /health`
无鉴权，返回任意 2xx 即正常（未单独配置时平台查 Add 同源 `/health`）。

### 鉴权
Add/Search 支持 `Authorization: Bearer` / `Token` / `X-Api-Key`；`none` 仅公开 smoke。
本服务把 **LDBD 评测密钥**当作 `AML_API_KEY` 做恒定时间比对校验。

---

## 2. 本仓库已交付的适配服务（在 `agent_memory_challenge/`）

| 文件 | 作用 |
|---|---|
| `app.py` | FastAPI：严格契约的 `/add` `/search` `/health`，含鉴权、幂等、回显 |
| `store.py` | SQLite 持久化（同步落库）+ 内存 BM25 索引（按 `user_id` 隔离）+ 幂等/冲突 409 |
| `retrieval.py` | BM25 检索；`SEMANTIC=1` 叠加 `text-embedding-v4` 向量 RRF；`RETRIEVAL_MODE=agent` 用米鱼菜菜 gateway（auto）做 LLM 重排（含超时+熔断降级） |
| `llm.py` | 米鱼菜菜 gateway 客户端（OpenAI 兼容 chat/completions，`model=auto` 自动路由），仅 `agent` 模式用；内置 `ping()` 连通自检与连续失败熔断 |
| `smoke_test.py` | 本地全链路冒烟（health→add→幂等→search），默认 `http://127.0.0.1:8080` |
| `check_gateway.py` | **部署后跑一次**：验证网关可达 + agent 检索端到端（重排或安全回退 BM25） |
| `Dockerfile` | 容器化部署；`HEALTHCHECK` 打 `/health` |
| `.env` / `.env.example` | **含 LDBD 密钥（已 gitignore，禁止提交）** |
| `requirements.txt` | fastapi / uvicorn / python-dotenv（语义模式可选 dashscope / sentence-transformers） |

默认 `RETRIEVAL_MODE=bm25`：**纯本地、离线、零云依赖**，已是稳健基线（ReFind 的 bm25 无密钥模式即排第 2）。

> **📌 已定决策（2026-10-24）：冲名次/奖金 → 选「开放方法赛道」+ 米鱼菜菜免费网关**
> - 理由：开放方法赛道**模型自由、零模型费**（网关 `auto` 免费），且**奖金池 US$22,000 仅在这条赛道评**；学术赛道强制 gpt-4o-mini（仍可按 token 付费跑，但我们目标不是它）。
> - 落地：`.env` 已设 `RETRIEVAL_MODE=agent` + 填入 `GATEWAY_TOKEN`。网关是 LLM(chat) 端点，**非**本地 8910、也**非** embedding 端点。
> - 韧性：网关单次超时 `GATEWAY_TIMEOUT=8s`（到点即回退 BM25）；连续失败 `GATEWAY_MAX_FAILURES=5` 次 → **熔断**，本进程剩余请求直接走 BM25，不再打网关。整进程共用一个网关实例（app.py 启动建一次），熔断在全部 `/search` 间共享。
> - 部署后务必跑一次 `python check_gateway.py` 确认网关可达、重排链路通。

> **关于「模型自动切换」与赛道合规（重要）**
> 适配服务里涉及「模型」的有三处，务必分清：
> 1. **BM25**（`bm25` 模式，默认）：零模型，纯本地关键词，离线。
> 2. **text-embedding-v4 + gpt-4o-mini**（`SEMANTIC=1` + 学术冲刺）：**学术赛道强制**，缺一不可。
> 3. **米鱼菜菜 gateway（`RETRIEVAL_MODE=agent`）**：第三方 **LLM(chat) 端点**，`model=auto` 自动路由——这是你提到的「模型自动切换」来源，**不是你本地 8910 服务器**，也**不是 embedding 端点**。
>
> ⚠️ 米鱼菜菜 gateway **仅适用于「开放方法」赛道**（模型自由）；**学术赛道强制 LLM=gpt-4o-mini，禁止用它**。开启方式：`.env` 设 `RETRIEVAL_MODE=agent` + `GATEWAY_TOKEN=...`。`agent` 模式 = BM25 粗排 + gateway `auto` LLM 重排；网关不可达/超时/抖动时**自动回退 BM25**，评测不会因此失败。

---

## 3. 本地跑通（你现在就能做）

```bash
cd agent_memory_challenge
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
# AML_API_KEY 留空 → 本地放行
python app.py                       # 监听 :8080
# 另开终端：先验证网关（开放方法赛道 agent 模式必跑）
python check_gateway.py
# 再跑全链路冒烟
python smoke_test.py http://127.0.0.1:8080
```
期望：`[OK] health / add / add idempotent / search` + `ALL SMOKE CHECKS PASSED ✅`。

---

## 4. 冲刺更高分（学术赛道合规）

规则：**学术赛道 embedding 必须用 `text-embedding-v4`，LLM 必须用 `gpt-4o-mini`**（reranker 不限）。

1. 填 `.env`：`DASHSCOPE_API_KEY=...`、`OPENROUTER_API_KEY=...`、`SEMANTIC=1`
2. `pip install dashscope`（可选本地 `sentence-transformers` 兜底）
3. 语义模式 = BM25 + `text-embedding-v4` 向量 RRF 融合，召回更稳。
4. 若要复刻 ReFind 的「agentic 迭代重排」，在 `retrieval.py` 加一个 `gpt-4o-mini` planner（OpenRouter），但**只重排、不答题**（答题归平台）。

---

## 5. 提交流程（平台侧）

1. 在 https://agentmemoryleaderboard.ai/evaluation 申请 Evaluation Key（已拿到 LDBD Key，直接绑定）。
2. **部署公网 HTTPS 端点**（见第 6 节，这是唯一阻塞项）。
3. 在平台登记：系统名 `Antinet-AML`、版本、`Add/Search/Health` 地址、`AML_API_KEY`（=LDBD Key）、并发（建议 Add/Search 各 16）。
4. 先跑 **Smoke**（每小时 1 次，仅私有，不发布）→ 通过。
5. 提交 **Full**（每 Key 每赛道最多 2 次，第二次须距首次完成 30 天）。
6. 开源方法须附 **GitHub 仓库 + 固定 commit**（本目录即仓库内容）。
7. 材料截止前完成：仓库、README、固定版本、运行日志。

---

## 6. 部署公网端点（已定：自有服务器，SecureCRT + SecureFX 推送）

平台必须能从公网访问你的 `/add` `/search`。**用户已选定：推送到自有 Linux 服务器**（如 39.108.254.228），用 SecureCRT 跑命令 + SecureFX 传文件。

| 优先级 | 方案 | 成本 | 稳定性 | 说明 |
|---|---|---|---|---|
| **① 选定** | **自有服务器（SecureCRT/SecureFX 推送）** | 已有机器 | 高 | 详见 `deploy_server.md`；`deploy_server.sh` 一键起 systemd 服务，监听 8080 |
| ② 增强 | 服务器 + Cloudflared Tunnel 前置 | 免费 | 高 | 解决「平台要 HTTPS / 跨境延迟」：服务器上跑 `cloudflared tunnel --url http://localhost:8080` |
| ③ 备用 | GCP 美区 e2-micro（always-free） | 免费档 | 高 | 需信用卡；延迟最优 |
| ✗ 不推荐 | ngrok / HF Spaces / 免费 Render | 免费 | 中/休眠 | URL 变或会休眠 → 评测易超时 |

**服务器一键部署**：`bash deploy_server.sh`（建 venv+装依赖+起 `aml-adapter` 服务，监听 0.0.0.0:8080）。
**质量自检**：`curl http://127.0.0.1:8080/health` + 本机 `python smoke_test.py http://<公网IP>:8080`。
**HTTPS 兜底**（若平台强制）：服务器上 `cloudflared tunnel` 免域名，或 nginx+certbot 需域名。

> 沙箱限制：SSH 22 被封、无出网推送 → 上服务器命令需你在 SecureCRT 执行；部署文档 `deploy_server.md` 已备齐。

---

## 7. 时间线（剩余 ≈8 天）

- **D1（今天）**：本地冒烟跑通 ✅（本仓库已就绪）、确定部署方案。
- **D2–D3**：部署公网 + 绑定 LDBD Key + 跑通 Smoke。
- **D4–D5**：开启 `SEMANTIC=1` 语义模式（如有 DashScope key）调优；准备 GitHub 仓库 + 固定 commit。
- **D6–D7**：提交 Full（Textual 学术赛道）；记录 commit/镜像版本。
- **D8（10-31）**：材料截止前复核：仓库、README、版本、日志齐全。

---

## 8. 反作弊红线（一碰即取消资格）

- 禁止硬编码公开题目、禁止跨样本共享状态、禁止注入提示词操纵。
- 禁止把金标答案写进检索、禁止用 `options` 反推答案。
- Add/Search 必须用**完全相同**的 `user_id`。
