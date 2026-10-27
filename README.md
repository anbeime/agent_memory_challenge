# AML Adapter — Agent Memory Challenge (CSIG) Cycle 2

适配第二届智能体长期记忆挑战赛（开放方法赛道）的 **Add / Search / Health** 端点实现。
平台固定数据、Answer 模型与裁判；参赛者只需实现「记忆的写入与检索」。

## 架构

- `app.py` — FastAPI，严格遵循官方契约：`POST /add`（回显 + 宽松幂等，同 `request_id` 一律 200）、`POST /search`（`{"data":[...]}`，空结果返回 `[]`）、`GET /health`（上报 `index_ready` / `docs`）。开放端点（调用方不带 token 即放行）。
- `store.py` — SQLite 持久化（WAL + `busy_timeout`，抗 16 并发写）+ 内存 BM25 索引（按 `user_id` 严格隔离）+ 四色卡实体图谱索引；索引 pickle 落盘 `<db>.idx.pkl`，重启**增量加载**（只补 `id > max_doc_id` 的新文档），避免全量重建阻塞端口。
- `retrieval.py` — 检索策略（`RETRIEVAL_MODE`）：
  - `card`（**生产模式**）：BM25 主召回 + 四色卡实体图谱 1 跳联想，强制末位 reserve slot；**零 LLM、零外部依赖**。
  - `bm25`：纯本地关键词基线。
  - `agent`：BM25 粗排 + 米鱼菜菜 gateway LLM 重排（同步阻塞调用，高并发下会卡事件循环，仅实验用）。
- `llm.py` — 米鱼菜菜 gateway 客户端（OpenAI 兼容），标准库零依赖，含连通自检与熔断。

## 赛道与合规

- **开放方法赛道**（本仓库目标）：模型自由，**零模型费**，奖金池仅此赛道评。
- 学术赛道强制 `text-embedding-v4` + `gpt-4o-mini`，本仓库未采用（参见 `RUNBOOK.md`）。
- **提交版本（固定 commit）**：tag `v2-textual-full`（Textual 赛道 Full）。
  提交后**不得再改**该 tag 指向的代码；任何微调须新开 commit + 新 tag 并在平台重新登记。

## 快速运行

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env          # 填入 LDBD_KEY / AML_API_KEY / GATEWAY_TOKEN
python app.py                 # 监听 :8080
python check_gateway.py       # 验证网关可达（agent 模式）
python smoke_test.py http://127.0.0.1:8080
```

## 提交事实（Textual 赛道）

| 项 | 值 |
|---|---|
| 系统名 | `Antinet-AML` |
| 赛道 / 分榜 | Textual · 开放方法 |
| 提交版本 | tag `v2-textual-full` |
| Add | `http://39.108.254.228:8080/add` |
| Search | `http://39.108.254.228:8080/search` |
| Health | `http://39.108.254.228:8080/health` |
| 并发 | Add / Search 各 16 |
| Top K | 100 |
| Smoke | 69.90（V6.0，2026-10-25） |
| Full | 30.97 · 状态成功 · 评分 9602/9602（2026-10-27） |

## 部署

详见 `RUNBOOK.md`（自有服务器 / Cloudflare Tunnel / GCP 等方案与提交流程）。
提交平台需公开仓库 + 固定 commit + 公网托管 API。材料截止 **2026-10-31 23:59 UTC+8**，评测关闭 **2026-11-04 23:59 UTC+8**。

## 安全

`.env`（含评测密钥与网关令牌）已被 `.gitignore` 忽略，**绝不提交**。服务器侧单独创建 `.env`。
