# AML Adapter — Agent Memory Challenge (CSIG) Cycle 2

适配第二届智能体长期记忆挑战赛（开放方法赛道）的 **Add / Search / Health** 端点实现。
平台固定数据、Answer 模型与裁判；参赛者只需实现「记忆的写入与检索」。

## 架构

- `app.py` — FastAPI，严格遵循官方契约：`POST /add`（回显 + 幂等）、`POST /search`（`{"data":[...]}`）、`GET /health`，含令牌鉴权。
- `store.py` — SQLite 持久化 + 内存 BM25 索引（按 `user_id` 隔离）+ 幂等冲突 409。
- `retrieval.py` — 检索策略：
  - `bm25`（默认）：纯本地关键词，离线、零云依赖。
  - `agent`：BM25 粗排 + 米鱼菜菜 gateway（`model=auto`）LLM 重排；网关超时/失败自动回退 BM25（熔断保护）。
- `llm.py` — 米鱼菜菜 gateway 客户端（OpenAI 兼容），标准库零依赖，含连通自检与熔断。

## 赛道与合规

- **开放方法赛道**（本仓库目标）：模型自由，**零模型费**（米鱼菜菜免费网关），奖金池仅此赛道评。
- 学术赛道强制 `text-embedding-v4` + `gpt-4o-mini`，本仓库未采用（参见 `RUNBOOK.md`）。

## 快速运行

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # 填入 LDBD_KEY / AML_API_KEY / GATEWAY_TOKEN
python app.py                 # 监听 :8080
python check_gateway.py       # 验证网关可达（agent 模式）
python smoke_test.py http://127.0.0.1:8080
```

## 部署

详见 `RUNBOOK.md`（自有服务器 / Cloudflare Tunnel / GCP 等方案与提交流程）。
提交平台需公开仓库 + 固定 commit + 公网托管 API。

## 安全

`.env`（含评测密钥与网关令牌）已被 `.gitignore` 忽略，**绝不提交**。服务器侧单独创建 `.env`。
