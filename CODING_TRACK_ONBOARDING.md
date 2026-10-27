# Coding 赛道接入说明（Textual 之外的第二赛道）

> 本文只讲「Coding 赛道怎么接」，Macro 层面的报名/材料口径见 `SUBMISSION_CHECKLIST.md`。
> 依据：官方《第二步：部署并提交接口 / 第三步：准备提交材料》中文细则（2026-10 版）。

---

## 一、结论：Coding 赛道**不用换接口契约**，但要换三样东西

三赛道共用**同一套** Add / Search / Health 契约，平台侧流程也相同。真正需要差异化的是**记忆层**：

| 维度 | Textual（已跑完 30.97） | Coding（本次接入） |
|---|---|---|
| 端口 | 8080 | **8081** |
| DB | `aml_mem.db` | **`aml_code.db`**（隔离，防 user_id 空间污染） |
| 分词器 | `nlp`（自然语言） | **`code`（代码感知）** |
| 检索模式 | `card`（四色卡图谱） | `card`（复用，同一套机制） |
| 端点契约 | Add / Search / Health | **完全相同** |

**为什么必须换分词器**：实测（本地，`_test_code_tok.py`）用代码风格记忆对比两种分词器：

| 查询 | nlp 分词器 | code 分词器 |
|---|---|---|
| `get user by id` | ❌ MISS | ✅ HIT |
| `validate token` | ❌ MISS | ✅ HIT |
| `http request handler process` | ✅ HIT | ✅ HIT |
| `E5001 retry` | ✅ HIT | ✅ HIT |
| `readUTF8 utf-8 decoding` | ✅ HIT | ✅ HIT |
| **合计** | **3/5** | **5/5** |

根因：`getUserById` 在 nlp 分词器下不切分 → 整词 `getuserbyid`，而查询切出 `get/user/by/id`，**词面零重叠 → BM25 得 0 分**。code 模式补 camelCase 边界子词 + 保留数字，故 5/5。

---

## 二、服务器部署（逐条执行，勿整段粘贴）

```bash
cd ~/agent_memory_challenge

# ① 取最新代码（含 code 分词器）
git fetch origin && git reset --hard origin/main
grep -c "_tokenize_code" store.py        # 须 ≥1，否则没拉到

# ② 建独立 DB（空库，评测会自己灌数据）
ls -la aml_code.db 2>/dev/null || echo "OK: 独立库不存在，首次启动会自动建"

# ③ 安装 Coding 服务单元（把仓库里的 unit 拷到 systemd 目录）
sudo cp aml-adapter-coding.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aml-adapter-coding

# ④ 自检（注意端口 8081）
sudo systemctl status aml-adapter-coding --no-pager     # 须 active (running)
ss -ltnp | grep 8081                                    # 须见 0.0.0.0:8081
curl 127.0.0.1:8081/health                              # 须 index_ready:true
grep -i "four-color-card" aml.log                       # 确认 card 模式 + 分词器
```

### ⚠️ 内存红线（这台机器只有 1.8G）

当前 `aml-adapter`（Textual，91097 文档索引）已占相当内存，**再起一个实例有 OOM 风险**。三个选项，按推荐度：

1. **串行（推荐）**：Textual 的 Full 已完成（状态=成功），可在 Smoke/Full 期间临时停掉它腾内存：
   ```bash
   sudo systemctl stop aml-adapter        # 跑 Coding 评测前
   sudo systemctl start aml-adapter-coding
   ```
   注意：官方要求"正式评测受理后运行条件保持稳定"——Textual 已跑完，停服务**不会影响已提交结果**，但若平台在 11-04 前复核拉取端点，停机会导致复核失败。**保守做法：两个都留着，但先确认 free 内存充足**。
2. **共存 + 加 swap**：已有 4G swap，可再观察 Coding 实例启动后的 `free -m`。
3. **只跑一个赛道**：如果 Coding 只是试水，跑完即停。

**先决策再操作**——这台机器内存紧张，双实例同时在评测窗口内持续跑是有实际风险的。

---

## 三、平台侧提交（Coding 独立走一遍，与 Textual 互不影响）

按官方流程：**三赛道独立排名、独立评奖、独立 Smoke/Full**，所以 Coding 必须**单独提交一次**：

1. **提交评测申请**，参评类型 = Coding · 开源方法
2. 填 **已部署的 Coding 端点**：
   - Add = `http://39.108.254.228:8081/add`
   - Search = `http://39.108.254.228:8081/search`
   3. **运行 Smoke** → 通过后跑 **Full**
4. 复核通过 → 进 Coding 榜

### 🔑 两个 Key 的区别（官方明确，务必别混）

| 凭证 | 谁提供 | 用途 | 我们是否需要 |
|---|---|---|---|
| **Eval Key / Leaderboard Key** | AML 平台 | 验证参赛身份、跑评测、看私有结果 | ✅ 已持有（= `LDBD_KEY`） |
| **Memory System Key** | **参赛方自己** | 供平台访问我们的 Add/Search | ❌ **无需提供**（我们的端点对无 token 调用放行，属"无鉴权接口"） |

官方原文：「两个 Key 的签发方和用途不同，请勿将它们放入公开仓库、URL、截图、邮件正文或群聊。」
→ 我们的 Eval Key 只存在服务器 `.env`，已 gitignore，**不入仓库**。

---

## 四、提交材料（Coding · 开源方法）

官方「共同材料」+「开源方法」两份清单，逐项对照：

| # | 材料 | 我们的状态 |
|---|---|---|
| 1 | 系统名称与版本 | `Antinet-AML` / tag 待打（coding） |
| 2 | 联系人 | ⬜ 待你填 |
| 3 | 机构或团队 | 珠海安能宇新能源科技有限公司 / TOPGO |
| 4 | 拟参评类型 | Coding · 开源方法 |
| 5 | 方法说明 | 见 `README.md`（card 模式 + code 分词器） |
| 6 | 允许公开展示的信息 | ⬜ 待你确认 |
| 7 | 完整提交说明 | 本文件 + `RUNBOOK.md` |
| 8 | 已部署的 Add/Search API | 8081 端点 |
| 9 | 鉴权与容量声明 | **无鉴权**；容量见下方 |
| 10 | 公开仓库 + 固定 commit | 同一仓库，需为 Coding 打独立 tag |
| 11 | **原始工作引用** | ⬜ **需补**（见下） |
| 12 | **方法改动说明** | ⬜ **需补**（见下） |

### 容量声明（建议填）
- 并发：Add / Search 各 16（与 Textual 相同能力）
- Top K：100
- 数据保留：评测期内常驻，不删
- 超时：单请求 BM25 检索 < 50ms（零 LLM、零外部依赖）

---

## 五、🔴 必补合规项：开源引用披露

官方「参赛基本要求」第 3 条写得明确：

> **披露来源与改动**：复用论文、仓库或代码时，必须注明原作者、技术报告和全部方法改动。

我们的实现**必须是原创声明 + 引用清单**，否则复核可能不通过。当前实现涉及：

| 组件 | 来源 | 我们的改动 |
|---|---|---|
| BM25 排序 | Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond* (2009) | 加入时间衰减加权（`_recency`，α=0.2/τ=72h）+ 低分阈值拒答 |
| 四色卡实体图谱 | 知易 Antinet 自研（本团队） | 本条为原创 |
| RRF 融合 | Cormack, Clarke & Buettcher, *Reciprocal Rank Fusion* (SIGIR 2009) | 仅 `SEMANTIC=1` 时启用，本版本未开启 |
| FastAPI / uvicorn | 上游开源（MIT/BSD） | 未修改 |

**待办**：在 `README.md` 增加「参考与致谢」段落，逐条列出上述引用 + 我们的改动。这一条**必须做**，是硬性合规。

---

## 六、时间线

- 材料截止 **2026-10-31 23:59 UTC+8**（剩 4 天）
- 评测停止 **2026-11-04 23:59 UTC+8**
- Full 通常需 **0.5–2 天**，须提前提交稳定 API 并完成测试
- 正式 Full 受理后**不得更换版本或撤回**
