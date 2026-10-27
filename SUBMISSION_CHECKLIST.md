# 材料提交清单 — Agent Memory Challenge（CSIG）Cycle 2

> 生成日期：2026-10-27 ｜ 赛道：Textual · 开放方法（Open-source Methods）
> **材料截止 2026-10-31 23:59 UTC+8** ｜ **评测关闭 2026-11-04 23:59 UTC+8** ｜ 官方结果计划 2026-11 中旬

---

## 一、官方材料要求（据官方站点 / 公告逐项）

官方公开公告中的关键原文（两处独立来源一致）：

> "Evaluation applications and complete materials must be submitted by 23:59 (UTC+8) on October 31, 2026."
> "Cycle 2 requires participant-hosted Add / Search APIs; **repositories support disclosure and version verification**."
> "AML reviews result validity, **version consistency**, and compliance status before leaderboard publication."
> "Once a Full evaluation begins, the submitted **code, image, endpoint, authentication method, and operating conditions must remain stable**."

**官方中文细则补充（2026-10 最新，优先级高于早期英文公告）**：
- **第二期不接受「只提交 GitHub 仓库 / Docker 镜像 / 启动说明」并由 AML 代为部署** → 必须**自托管 Add/Search API**。我们已满足。
- 「共同材料」：系统名称与版本、**联系人**、机构或团队、拟参评类型、方法说明、允许公开展示的信息、完整提交说明。
- 「开源方法」追加：已部署 API、**鉴权与容量声明**、公开仓库、**固定 commit**、**原始工作引用**、**方法改动说明**。
- 两个 Key 由**不同方**签发，**不得**放入公开仓库/URL/截图/邮件正文/群聊：
  | 凭证 | 签发方 | 用途 | 我们 |
  |---|---|---|---|
  | Eval / Leaderboard Key | AML 平台 | 验证身份、跑评测、看私有结果 | ✅ 已持有（`LDBD_KEY`） |
  | Memory System Key | **参赛方自己** | 供平台访问我们的 API | ❌ **无需**（端点无鉴权，属"无鉴权接口"） |
- **奖金（人民币）**：总额 **150,000 元**，三赛道各 50,000 元。每赛道：一等 1 名 20,000 元；二等 2 名各 8,000 元；三等 3 名各 3,000 元；最佳技术创新奖 1 名 5,000 元。**仅开源方法榜可评奖**，商业产品榜不参与。
- **有效提交五要件**：材料完整 ✓ / Smoke 通过 ✓ / Full 完成 ✓ / **版本一致** / 复核通过。

推导出的材料清单（**没有独立的 PDF 申请表**，全部在平台侧作业界面完成）：

| # | 材料 | 官方依据 | 状态 |
|---|---|---|---|
| 1 | 平台注册 / Evaluation Key | "Evaluation applications ... submitted" | ✅ 已完成（LDBD Key 已绑定） |
| 2 | 公网托管 Add / Search API | "participant-hosted Add / Search APIs" | ✅ 已完成（39.108.254.228:8080） |
| 3 | 公开仓库 + 固定 commit | "repositories support disclosure and version verification" | ⚠️ 见下方待办 |
| 4 | Smoke 通过 | "Pass a per-track Smoke test" | ✅ 69.90（2026-10-25） |
| 5 | Full 评测完成 | "Start a formal Full evaluation" | ✅ 30.97 · 成功（2026-10-27） |
| 6 | 版本一致性（登记版本 == 实际运行版本） | "version consistency" | 🔴 **必须处理** |
| 7 | 运行条件稳定（端点 / 鉴权 / 并发） | "must remain stable" | ✅ 已 systemd 托管 |
| 8 | 联系人 / 拟参评类型 / 可公开信息 | 官方「共同材料」 | ⬜ **待你填** |
| 9 | 原始工作引用 + 方法改动说明 | 官方「开源方法」+ 基本要求第3条 | ✅ 已补入 README「参考与致谢」 |
| 10 | 鉴权与容量声明 | 官方「开源方法」 | ✅ 无鉴权 + 容量见 CODING_TRACK_ONBOARDING.md |

---

## 二、🔴 阻塞项：版本一致性（tag 与实跑 commit 不符）

这是目前**唯一未闭合的合规风险**。AML 明确会在发布排名前复核 version consistency。

当前三处版本**互不相同**：

| 位置 | 版本 | 时间 | 说明 |
|---|---|---|---|
| tag `v1-submission` | `3d2892e` | 10-24 19:06 | ⚠️ **游离提交**（不在 `main` 历史中），且是四色卡 / 鉴权 / 幂等 / 索引修复**全部之前**的旧版 |
| 平台登记（日志） | `4d8e496` | 10-25 11:39 | V6.0 冲的 Smoke / Full |
| 服务器实跑并出 30.97 | `45eca11` | 10-26 20:59 | ✅ 真实产生成绩的版本 |

**处置（已完成）**：
- ✅ 已在实跑版本上打新 tag：`v2-textual-full → 45eca11`
- ✅ **已推送成功（2026-10-27 12:00）**：
  - `45eca11..ea30fa1  main -> main`
  - `[new tag]  v2-textual-full -> v2-textual-full`
  - （`git-credential-manager.exe: No such file or directory` 为凭据助手无关警告，不影响推送）
- ⬜ **待办：回平台把登记的 commit 改为 `45eca11`**（完整 SHA 见下）。若界面不允许改，则以平台重新登记新版本为准。

**平台登记用的完整 SHA**：
```
45eca1168f742db835f4a1e49d43202cbfdb480b
```
> 说明：`main` 顶端现为 `ea30fa1`（仅新增 README/材料清单两份文档，`app.py`/`store.py`/`retrieval.py` 与 `45eca11` **逐字节相同**）。登记的应是**实际跑出成绩的代码版本** `45eca11`；若平台按 `main` HEAD 校验并提示不符，可改登记 `ea30fa1`（代码等价），二者均可自证一致。

> 原则：**平台登记的版本必须等于服务器实际运行的版本**。当前服务器跑的就是 `45eca11`（已 `git reset --hard origin/main` 过），所以登记 `45eca11` 即一致。

---

## 三、待办动作清单（按优先级）

1. ✅ **推仓库 + tag**：已完成（`main`→`ea30fa1`、tag `v2-textual-full`→`45eca11` 均已上传）
2. 🔴 **平台登记 commit 改/确认为 `45eca1168f742db835f4a1e49d43202cbfdb480b`**（= `v2-textual-full`）
3. 🟡 **保持端点在线**：评测关闭（11-04）前勿停 `aml-adapter` 服务、勿重启机器
4. 🟡 **确认材料截止前平台无未填项**：登录 AML 作业界面逐项检查（系统名 / 版本 / 三个 URL / 鉴权 / 并发）
5. 🟢 可选：如需再战 Coding / Multimodal 赛道，须**各自独立**走一遍 Smoke→Full（冷却规则按赛道独立）

---

## 四、红线（一碰即取消资格）

- 禁止硬编码公开题目、禁止跨样本共享状态、禁止注入提示词操纵
- 禁止把金标答案写进检索、禁止用 `options` 反推答案
- Add / Search 必须使用**完全相同**的 `user_id`
- `.env`（含 LDBD Key / 网关令牌）**绝不进入公开仓库**（已由 `.gitignore` 忽略，已验证 21 个跟踪文件中无密钥）

---

## 五、当前成绩留档

| 指标 | 值 |
|---|---|
| Smoke（V6.0） | **69.90** |
| Full（实跑版本 `45eca11`） | **30.97** · 状态成功 · 评分 9602/9602 |
| 维度得分 | H 75.17 / G 46.99 / I 40.21 / C 32.75 / D 28.49 / A 26.34 / B 23.60 / E 19.77 |
| 本轮排名（用户反馈） | 约第 14 名 |

**维度解读**：H（认识论安全与隐私）75.17 显著领先，得益于 card 模式 `min_shared=2` + 跨用户隔离 + 无 LLM 幻觉；A/B/E 偏弱源于零模型 BM25 无语义层（对照：Smoke 跑 agent 引擎时 B/E 均为 100）。提分方向 = 语义层（异步 LLM 重排 + embedding 向量），非调参。
