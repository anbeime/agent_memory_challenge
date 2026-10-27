# Coding 赛道 8081 部署 — 可直接粘贴清单

> 目标：在**不影响**正在跑 8080（Textual，已出分 30.97）的前提下，起一个独立的 Coding 实例。
> 三赛道共用同一份代码，靠环境变量区分：**8081 / `aml_code.db` / `AML_TOKENIZER=code`**。

---

## ⛔ 三条铁律（先读，否则会踩）

1. **绝对不要 `pkill -f app.py`** —— 8080 的 Textual 实例也叫 `app.py`，这一刀会把已出分的实例一起杀掉。
   本清单**全程不出现** `pkill -f app.py`，只用 `systemctl` 精确控制 `aml-adapter-coding`。
2. **sudo 命令逐条执行**，出现 `[sudo] password for topgo:` 时**手动输入密码回车**。
   整段粘贴会把后续命令文本当成密码（你之前连续 3 次密码错就是这个原因）。
   本清单已把 sudo 命令与普通命令**分开成块**。
3. **`git fetch` 只动远程指针，必须跟 `git reset --hard`** 才真正更新工作区。已合并在 `&&` 链里。

---

## ⚙️ 一个必须先理解的点：端口为什么不会撞车

`.env` 里写着 `AML_PORT=8080`，而 Coding 实例要跑 8081。**两者不冲突**，原理是：

`app.py` 用 `os.environ.setdefault()` 加载 `.env` —— 而 systemd 在启动进程**之前**就把 `Environment=AML_PORT=8081` 注入了进程环境，
所以 `os.environ` 里**已经有** 8081，`setdefault` 遇到已存在的键**不会覆盖** → Coding 实例读到的就是 8081。

**两条推论，务必记住**：
- ✅ 通过 `systemctl restart aml-adapter-coding` 启动 → 永远 8081，安全。
- ⛔ **不要**在项目目录手动 `~/miniconda3-aml/bin/python app.py` —— 那样没有 systemd 注入，会读 `.env` 的 8080，
  与 Textual 实例**抢端口**，要么启动失败、要么把线上实例搞乱。

> 若 Coding 实例真的错用了 8080，它会因端口被占而**显式启动失败**（不会静默出错），所以不会造成隐蔽事故——但仍请按上述方式启动。

---

## 第 0 步：本地推送（在**你自己电脑**上跑，不在服务器）

本地有 3 个 commit 与 1 个 tag 未推送（沙箱连不上 GitHub）。

```bash
cd C:\D\zhiyi\agent_memory_challenge
git push origin main
git push origin v2-coding-submission
```

> Coding 登记用 SHA：tag `v2-coding-submission`

---

## 第 1 步：拉代码（服务器，可整段粘贴，无 sudo）

```bash
cd ~/agent_memory_challenge
git fetch origin && git reset --hard origin/main
echo "--- 校验（两项都必须 ≥1）---"
grep -c "_tokenize_code" store.py
grep -c "AML_TOKENIZER" aml-adapter-coding.service
echo "--- 确认 8080 还活着 ---"
curl -s 127.0.0.1:8080/health
```

预期：`_tokenize_code` ≥1、`AML_TOKENIZER` ≥1、8080 返回 `{"status":"ok",...,"index_ready":true}`。

---

## 第 1.5 步：端口冲突处理（若目标端口已被占用）

**先诊断，不要盲目换端口。** 这台机器是共享盒子（同机跑着 docker/mysql/redis/node），端口确实紧。更重要的是：**很可能占着端口的就是你自己的 coding 服务**——你改了端口→启动→服务占住→你再查显示"被占"，这是完全正常的。

### ① 先看清"谁"在占（决定后续走哪条路）

```bash
# 停掉我们的 coding 服务，避免它反复重启干扰诊断（没装过会报错，忽略）
sudo systemctl stop aml-adapter-coding 2>/dev/null

# 全部监听端口一览（谁在占一目了然）
echo "=== 所有 LISTEN 端口 ==="
sudo ss -ltnp | awk 'NR==1 || /LISTEN/'

# 只看我们关系到的几个
echo "=== 8080-8090 / 18080-18090 ==="
sudo ss -ltnp | grep -E ':(808[0-9]|809[0-9]|1808[0-9]|1809[0-9]) ' || echo "(该区间全空闲)"
```

| 输出情况 | 含义 | 处理 |
|---|---|---|
| 端口 holder 是 **`python` / `app.py`** | **是我们自己的服务在跑** → 端口没冲突，**虚惊** | `sudo systemctl start aml-adapter-coding` 即可，跳过本节 |
| holder 是 **nginx / docker / mysqld / node / 其他** | 真被占用 | 走 ② 找空闲端口 |
| 输出 `(该区间全空闲)` | 占用者已退出或之前是竞态 | 直接 `start` 观察 |

### ② 自动找一个真正空闲的端口

```bash
# 扫描 8081-8099 + 18080-18099，列出所有空闲端口
echo "=== 空闲端口候选 ==="
for p in $(seq 8081 8099) $(seq 18080 18099); do
  ss -ltn 2>/dev/null | grep -q ":$p " || echo "$p"
done | head -20
```

挑第一个（比如 `8083`）用。

### ③ 🔴 关键一步：验证该端口**公网可达**（比"空闲"更重要）

阿里云**安全组是 VPC 层，实例内 `ss` / `iptables` 都看不到它**。8080 之前能被平台打通，不代表新端口也放行——**如果安全组只放行了 8080，你换任何端口平台都连不上，直接 ENDPOINT_UNAVAILABLE**。

所以换端口后**必须**立刻验证外部可达（在**你自己电脑**上执行，不是服务器）：

```bash
# 把 PORT 换成你选定的端口
curl -s --max-time 8 http://39.108.254.228:8083/health
```

- ✅ 返回 `{"status":"ok",...}` → 安全组放行，继续
- ❌ 超时 / 连接被拒 → **安全组没放行该端口** → 去阿里云控制台给该 EIP 绑定的实例安全组**入方向加一条 `TCP <端口> 授权对象 0.0.0.0/0`**，或改用 8080（见 ④）

### ④ 备选：改用 80 / 443（大概率已在安全组放行）

若安全组不便于新增规则，可试 `80` 或 `443`（Web 端口通常默认放行）。注意**低于 1024 的端口需要特权**：

- 我们的 unit 设了 `User=topgo` → 普通用户**无法**绑定 80/443。
- 解法：在 unit 里加一行授权，而非改成 root 运行：
  ```ini
  AmbientCapabilities=CAP_NET_BIND_SERVICE
  ```
  并确认 `aml-adapter-coding.service` 里 `Environment=AML_PORT=80`。
- 前提：80/443 **本身空闲**（先跑 ① 确认；本机有 nginx 的话通常已占用）。

### 换端口操作（代码零硬编码端口，改一行即可）

`app.py` 只读 `$PORT` / `$AML_PORT`，**没有任何硬编码端口**，改 service 文件一行就够：

```bash
cd ~/agent_memory_challenge
# 把 8083 换成你选定的端口
sed -i 's/^Environment=AML_PORT=.*/Environment=AML_PORT=8083/' aml-adapter-coding.service
grep AML_PORT aml-adapter-coding.service

sudo cp aml-adapter-coding.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl reset-failed aml-adapter-coding 2>/dev/null   # 若之前因冲突已 failed，先清状态
sudo systemctl start aml-adapter-coding
sudo systemctl status aml-adapter-coding --no-pager | head -6
```

> ⚠️ 改完**平台登记要用新端口**：Add/Search = `http://39.108.254.228:<端口>/{add,search}`。
> 同时把本仓库的 `aml-adapter-coding.service` 一起改掉并提交，保证「仓库 == 运行」。

### 顺手确认 8080 没被动过

```bash
curl -s 127.0.0.1:8080/health
```
必须仍返回 `index_ready:true`。**端口冲突是 Coding 实例自己的事，与 8080 无关**——只要你没执行过 `pkill -f app.py`，Textual 实例就是安全的。

---

## 第 2 步：内存决策（**先想清楚再往下走**）
这台机器 **1.8G RAM + 4G swap**，Textual 实例的 9.1 万文档索引已占用较多。三选一：

| 方案 | 做法 | 优点 | 风险 |
|---|---|---|---|
| **A · 共存（默认推荐）** | 两个实例都留着 | Textual 端点持续在线，复核不会被判掉线 | 内存紧张；Coding 灌数据时可能挤爆 |
| **B · 串行** | 跑 Coding 前停 Textual，跑完再起 | 内存最安全 | Textual 停机期间若平台复核拉端点→复核失败 |
| **C · Coding-only** | 永久停 Textual | 最省内存 | 同上，且 Textual 已出分无必要 |

**我的建议：走 A，并加内存护栏**（见第 4 步可选加固）。
理由：Textual 的 Full 已出分（30.97），但官方明确"复核版本、结果与合规状态"后才上公榜——**端点掉线有被判定复核失败的实际风险**，不值得为省内存去赌。

> 如果走 B/C，在第 3 步之后、评测前执行 `sudo systemctl stop aml-adapter` 即可；跑完 Coding 再 `sudo systemctl start aml-adapter`。

---

## 第 3 步：安装并启动 Coding 服务（sudo，**逐条执行**）

> ⚠️ 下面 4 条**每条单独粘贴**，密码提示出现时手输。

```
sudo cp aml-adapter-coding.service /etc/systemd/system/
```

```
sudo systemctl daemon-reload
```

```
sudo systemctl enable --now aml-adapter-coding
```

```
sudo systemctl status aml-adapter-coding --no-pager
```

预期最后一条显示 `Active: active (running)`、`Main PID` 为新进程、`Loaded: ... enabled`。

---

## 第 4 步：验证（可整段粘贴，无 sudo）

```bash
echo "=== 8081 监听 ==="; ss -ltnp | grep 8081
echo "=== 8081 health ==="; curl -s 127.0.0.1:8081/health; echo
echo "=== 8080 未被影响 ==="; curl -s 127.0.0.1:8080/health; echo
echo "=== 公网可达（两个都要）==="
curl -s --max-time 8 http://39.108.254.228:8081/health; echo
curl -s --max-time 8 http://39.108.254.228:8080/health; echo
echo "=== 内存 ==="; free -m
echo "=== 分词器确认 ==="; sudo -n true 2>/dev/null; journalctl -u aml-adapter-coding -n 20 --no-pager 2>/dev/null | grep -iE "four-color-card|tokenizer|error" || echo "(journalctl 需权限，改用下面这条)"
```

若最后一条无输出（无权限看 journal），用这个替代：

```bash
grep -iE "four-color-card" ~/agent_memory_challenge/aml.log | tail -3
```

**判读要点**：
- `ss` 须见到 `0.0.0.0:8081`
- 8081 health 须 `"index_ready":true`（**空库启动，秒级就绪**，不会有 Textual 那样 10–20 分钟的全量重建）
- **8080 health 必须仍然 ok** —— 这是"没误伤 Textual"的铁证
- `free -m` 若 `available` < 200MB，考虑切方案 B 串行

### 端到端写读自检（强烈建议，5 秒）

```bash
curl -s -X POST http://127.0.0.1:8081/add -H 'Content-Type: application/json' \
  -d '{"request_id":"code_selftest_1","user_id":"code_u","session_id":"code_s","messages":[{"role":"user","content":"Fixed TypeError in getUserById: userId was None. Added guard in userService.validateToken()."}]}'
echo
curl -s -X POST http://127.0.0.1:8081/search -H 'Content-Type: application/json' \
  -d '{"query":"validate token","user_id":"code_u","top_k":5}'
echo
```

**预期**：search 返回含 `validateToken` 的记忆。
**这就是 code 分词器生效的证据** —— 在 nlp 分词器下这条查询会返回空数组。

---

## 第 5 步（可选加固）：给 Coding 实例加内存上限

防止 Coding 灌数据时把 Textual 挤爆。若第 4 步 `free -m` 显示内存吃紧，加这两行：

```
sudo mkdir -p /etc/systemd/system/aml-adapter-coding.service.d
```

```
sudo tee /etc/systemd/system/aml-adapter-coding.service.d/memlimit.conf >/dev/null <<'EOF'
[Service]
MemoryMax=700M
MemorySwapMax=2G
EOF
```

```
sudo systemctl daemon-reload
```

```
sudo systemctl restart aml-adapter-coding
```

> 说明：`MemoryMax=700M` 是保守值。若日志出现 OOM kill，调大到 900M；若 Textual 吃紧，调小到 500M。

---

## 第 6 步：平台提交（Coding 独立走一遍）

1. **提交评测申请**：参评类型 = **Coding · 开源方法**
2. 端点填：
   - Add = `http://39.108.254.228:8081/add`
   - Search = `http://39.108.254.228:8081/search`
3. **运行 Smoke** → 通过后跑 **Full**
4. 复核通过 → 进 Coding 榜

**共同材料需新填**（Textual 那轮没有的）：

| 项 | 建议填法 |
|---|---|
| 系统名称与版本 | `Antinet-AML` · tag `v2-coding-submission` |
| 联系人 | ⬜ 待你填 |
| 机构或团队 | 珠海安能宇新能源科技有限公司 / TOPGO |
| 拟参评类型 | Coding · 开源方法 |
| 方法说明 | 见 `README.md`（card 模式 + code 分词器） |
| 允许公开展示的信息 | ⬜ 待你确认 |
| 鉴权声明 | **无鉴权**（端点对无 token 调用放行） |
| 容量声明 | 并发 Add/Search 各 16；TopK 100；单请求检索 <50ms（零 LLM） |
| 固定 commit | tag `v2-coding-submission` |
| 原始工作引用 | 见 `README.md`「参考与致谢」 |

> **Memory System Key 不用提供** —— 我们是无鉴权端点。
> Eval Key（= `LDBD_KEY`）只存服务器 `.env`，**不要**贴进平台材料、截图或邮件。

---

## 排障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `systemctl status` 显示 `failed` | 启动即崩 | `journalctl -u aml-adapter-coding -n 50 --no-pager` 看 traceback；**端口冲突最常见**（见第 1.5 步）；清状态用 `sudo systemctl reset-failed aml-adapter-coding` |
| 日志含 `Address already in use` | 端口被占 | 走第 1.5 步「换端口」 |
| `ss` 无 8081 | 服务没起来 | 同上；确认 `daemon-reload` 执行过 |
| 8081 health 无 `index_ready` | 还在建索引 | 空库应秒级；若卡住看日志是否在做全量重建 |
| **8080 变成不通了** | 误杀了 Textual | `sudo systemctl start aml-adapter` 立即恢复 |
| 日志出现 `MemoryError` / OOM | 内存不足 | 切方案 B 串行，或加第 5 步内存上限 |
| `git reset` 后 grep 仍是 0 | GFW 截断，没拉到 | 重试 `git fetch`；或用 SecureFX 传 `store.py` 等文件（**须 LF**） |

---

## 时间线红线

- 材料截止 **2026-10-31 23:59 UTC+8**（剩 4 天）
- 评测停止 **2026-11-04 23:59 UTC+8**
- Full 通常 **0.5–2 天**，须提前提交稳定 API
- **正式 Full 受理后不得更换版本或撤回**
