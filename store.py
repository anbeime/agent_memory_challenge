"""
AML 记忆存储层
- SQLite 持久化（同步写入，Add 返回 200 前已落库并可检索）
- 内存 BM25 索引（按 user_id 隔离，满足「记忆不跨 user_id」约束）
- 幂等：相同 request_id 重复写入安全；冲突复用返回 409
"""
import sqlite3
import os
import re
import sys
import math
import json
import hashlib
import logging
import time
import pickle
import threading
from collections import defaultdict

logger = logging.getLogger("aml.store")

STOPWORDS = set("""
a an the and or but if then else when at by for with without from to into of in on off
is are was were be been being am do does did doing have has had having this that these those
i you he she it we they me him her us them my your his our their as at so not no yes will would
can could should may might must shall what which who whom whose how why where there here
""".split())


def _tokenize_nlp(text: str):
    if not text:
        return []
    text = text.lower()
    toks = re.split(r"[^a-z0-9\u4e00-\u9fff]+", text)
    out = []
    intern = sys.intern  # 驻留去重：同一 token 全库只存一份字符串，显著降低索引内存（1.8G 小内存机关键）
    for t in toks:
        if not t:
            continue
        if t in STOPWORDS:
            continue
        if len(t) < 2:
            continue
        out.append(intern(t))
    return out


# ---------- 代码感知分词（Coding 赛道，AML_TOKENIZER=code 启用） ----------
# 自然语言分词切代码有两处硬伤：
#   ① camelCase/PascalCase 不切分：getUserById -> ['getuserbyid']，而查询 "get user by id" 切出
#      ['get','user','by','id']，词面零重叠 -> BM25 得 0 分，整条记忆召不回。
#   ② 长度<2 的 token 被丢弃：数字 0/1/3、单字符变量 i/x/n 全丢 -> 错误码、版本号、行号失配。
# code 模式做「超集」切分，保留自然语言切出的整标识符，并额外补 camel/字母数字 边界子词与全部数字。
# 因是超集，对自然语言查询只增不漏；默认仍走 _tokenize_nlp，保证 Textual 既有版本行为不变。
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_CODE_PART = re.compile(r"[A-Za-z]+|\d+|[\u4e00-\u9fff]+")


def _tokenize_code(text: str):
    if not text:
        return []
    out = []
    intern = sys.intern
    for raw in re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", text):
        if not raw:
            continue
        whole = raw.lower()
        if len(whole) >= 2 and whole not in STOPWORDS:
            out.append(intern(whole))
        for seg in _CAMEL_SPLIT.split(raw):
            for p in _CODE_PART.findall(seg):
                lp = p.lower()
                if lp == whole:
                    continue                      # 避免与整标识符重复计数
                if lp.isdigit():
                    out.append(intern(lp))        # 数字无条件保留（错误码 500/版本 8/索引 0）
                elif len(lp) >= 2 and lp not in STOPWORDS:
                    out.append(intern(lp))
    return out


# 分词器在导入时定死（版本可复现）：nlp（默认，自然语言/Textual）| code（Coding 赛道）
_TOKENIZER_MODE = os.getenv("AML_TOKENIZER", "nlp").strip().lower()


def tokenize(text: str):
    if _TOKENIZER_MODE == "code":
        return _tokenize_code(text)
    return _tokenize_nlp(text)


# 中文功能词/高频词（避免误连）。四色卡实体抽取时剔除。
CJK_STOP = set(
    "我们 你们 他们 她们 它们 自己 这个 那个 这些 那些 什么 怎么 为什么 因为 所以 "
    "但是 一个 没有 现在 时候 已经 还是 应该 可能 如果 然后 这样 那样 这种 那种 可以 "
    "需要 想要 觉得 知道 喜欢 问题 东西 感觉 一直 其实 真的 比较 或者 而且 虽然 不过 "
    "这么 那么 这个 那个 一样 起来 出来 一些 这个 通过 由于 对于 以及 并且 进行 表示 "
    "认为 发现 看到 告诉 回答 帮助 希望 感谢 请问 您好 你好 我们 你们".split()
)


def _extract_entities_code(text: str):
    """代码实体抽取：整标识符 + 错误码/版本号 + 引号串。
    代码里「同一标识符出现在报错与修复两处」正是最强的关联信号，故实体取完整标识符而非子词。"""
    if not text:
        return set()
    entities = set()
    intern = sys.intern
    for raw in re.split(r"[^A-Za-z0-9\u4e00-\u9fff_]+", text):
        if not raw:
            continue
        lp = raw.lower()
        if len(lp) < 3 or lp in STOPWORDS or lp in CJK_STOP:
            continue
        entities.add(intern(lp))
    for m in re.finditer(r"[\"“『]([^\"”』]{1,24})[\"”』]", text):
        v = m.group(1).strip().lower()
        if v:
            entities.add(intern(v))
    return entities


def extract_entities(text: str):
    """规则化实体抽取（零 LLM、零延迟）。
    返回实体集合：英文专有名词/长词、数字、引号内串、长度>=2 的中文词（剔除功能词）。
    这是四色卡『结构化元数据 + 强制溯源 + 图谱链接』的写入侧实现。"""
    if not text:
        return set()
    if _TOKENIZER_MODE == "code":
        return _extract_entities_code(text)
    entities = set()
    intern = sys.intern
    # 1) 英文专有名词（首字母大写）/ 长词 / 数字（含百分比）
    for m in re.finditer(r"[A-Z][a-zA-Z]+|[A-Za-z][a-zA-Z]{3,}|\d+(?:\.\d+)?%?", text):
        entities.add(intern(m.group(0).lower()))
    # 2) 引号内串（中英文引号）
    for m in re.finditer(r"[\"“『]([^\"”』]{1,24})[\"”』]", text):
        v = m.group(1).strip().lower()
        if v:
            entities.add(intern(v))
    # 3) 分词结果再筛：中文>=2 字且非功能词；拉丁>=3 且非停用词
    for t in tokenize(text):
        if re.search(r"[\u4e00-\u9fff]", t):
            if len(t) >= 2 and t not in CJK_STOP:
                entities.add(t)
        elif len(t) >= 3 and t not in STOPWORDS:
            entities.add(t)
    entities = {e for e in entities if e not in STOPWORDS and e not in CJK_STOP}
    return entities


class MemoryStore:
    def __init__(self, db_path: str = "aml_mem.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")  # Full 16 并发写：避免 database is locked 雪崩
        self._init_schema()
        # BM25 状态
        self.docs = {}                # doc_id -> {"uid","tokens","len","created_at"}
        self.df = defaultdict(int)    # term -> 文档频率
        self.N = 0
        self.avgdl = 0.0
        # 四色卡：实体索引（图谱）+ 每文档实体缓存
        self._doc_entities = {}                   # doc_id -> set(entities)
        self.entity_index = defaultdict(set)      # (user_id, entity) -> set(doc_id)
        self._lock = threading.Lock()
        self._idx_lock = threading.Lock()         # 索引加载/重建互斥（与读写锁分离）
        self._index_ready = False
        # —— B 方案检索增强参数（env 可覆盖）——
        # 时间加权（C 时间序列）：新记忆得分乘 (1+alpha*recency)，recency∈[0,1]
        self.recency_alpha = float(os.getenv("AML_RECENCY_ALPHA", "0.2"))
        self.recency_tau = float(os.getenv("AML_RECENCY_TAU_H", "72")) * 3600.0
        # 低置信拒答（H 认识论安全）：丢弃得分低于阈值的弱匹配噪声（默认极低，仅滤纯噪声）
        self.min_score = float(os.getenv("AML_MIN_SCORE", "0.03"))  # 降为 0.03，滤掉更弱匹配提升 H 认识论安全
        # 索引落盘：优先从 pickle 加载（<5s，重启端口秒开），无缓存才重建并落盘。
        # —— 根治「重启 15 分钟不监听」：不再每次都从头 rebuild_index 阻塞 uvicorn 绑端口。
        self.ensure_index()

    def _init_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ts INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_uid ON memories(user_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_rid ON memories(request_id)")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_requests (
                request_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                payload_hash TEXT NOT NULL
            )
        """)
        # 四色卡元数据列（幂等补列，兼容旧库）
        self._ensure_columns()
        self.conn.commit()

    def _ensure_columns(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(memories)").fetchall()}
        for col, ctype in [
            ("entities", "TEXT"),   # JSON 实体集合
            ("topic", "TEXT"),      # 主题（首条用户消息关键词）
            ("card_type", "TEXT"),  # statement|question|action
            ("color", "TEXT"),      # 蓝/绿/黄/红（四色卡）
            ("source", "TEXT"),     # 强制溯源：request_id
        ]:
            if col not in cols:
                self.conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {ctype}")

    # ---------- 写入 ----------
    def add(self, request_id, user_id, session_id, messages, created_at):
        """
        返回 ("ok", None) | ("dup", None) | ("conflict", None)
        同步落库并更新索引；不抛异常，由调用方转 HTTP。
        """
        from datetime import datetime, timezone
        if not created_at:
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # 计算 payload 哈希用于幂等/冲突判定
        norm = (user_id, session_id,
                hashlib.sha1(("|".join(
                    f"{m.get('role','')}:{m.get('content','')}:{m.get('timestamp',0)}"
                    for m in messages)).encode()).hexdigest())
        phash = hashlib.sha1(("|".join(map(str, norm))).encode()).hexdigest()

        with self._lock:
            cur = self.conn.execute(
                "SELECT user_id, session_id, payload_hash FROM processed_requests WHERE request_id=?",
                (request_id,))
            row = cur.fetchone()
            if row:
                if row[2] == phash:
                    return ("dup", None)
                return ("conflict", None)

            # 主题：取本批首条 user 消息的关键词（四色卡 topic）
            topic = ""
            for m in messages:
                if m.get("role") == "user" and m.get("content", "").strip():
                    topic = self._derive_topic(m["content"])
                    break

            doc_ids = []
            for m in messages:
                content = m.get("content", "")
                if not content or not content.strip():
                    continue
                ts = m.get("timestamp")
                role = m.get("role", "user")
                # D 记忆治理：同一用户完全相同内容 → 后写刷新（去重 + 更新），不重复建卡
                dup_id = self._find_identical(user_id, content)
                if dup_id is not None:
                    self.conn.execute(
                        "UPDATE memories SET created_at=?, ts=? WHERE id=?",
                        (created_at, ts, dup_id))
                    self._refresh_doc(dup_id, created_at)
                    doc_ids.append(dup_id)
                    continue
                ents = extract_entities(content)
                ctype, color = self._classify_card(content)
                cur = self.conn.execute(
                    "INSERT INTO memories (user_id, session_id, request_id, role, content, ts, created_at, "
                    "entities, topic, card_type, color, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, session_id, request_id, role, content, ts, created_at,
                     json.dumps(sorted(ents), ensure_ascii=False), topic, ctype, color, request_id))
                doc_id = cur.lastrowid
                doc_ids.append(doc_id)
                self._index_doc(doc_id, user_id, content, created_at, ents)

            self.conn.execute(
                "INSERT INTO processed_requests (request_id, user_id, session_id, payload_hash) VALUES (?,?,?,?)",
                (request_id, user_id, session_id, phash))
            self.conn.commit()
            return ("ok", doc_ids)

    # ---------- 索引 ----------
    def _classify_card(self, content):
        """四色卡类型/颜色判定（规则化）。"""
        c = (content or "").strip()
        if not c:
            return "statement", "blue"
        if c.endswith("?") or c.endswith("？") or c.endswith("吗") or c.endswith("呢"):
            return "question", "yellow"   # 黄：疑问/不确定
        if any(k in c for k in ["风险", "失败", "错误", "禁止", "警告", "丢失", "崩溃", "异常", "危险"]):
            return "risk", "red"          # 红：风险
        if any(k in c for k in ["做", "执行", "发送", "创建", "启动", "调用", "购买", "提交", "运行"]):
            return "action", "green"      # 绿：动作
        return "statement", "blue"        # 蓝：陈述/事实

    def _derive_topic(self, content):
        """四色卡主题：取首条用户消息中最具体的若干关键词。"""
        toks = [t for t in tokenize(content) if t not in STOPWORDS]
        toks = sorted(set(toks), key=len, reverse=True)[:3]
        return " ".join(toks)

    def _index_doc(self, doc_id, user_id, content, created_at, ents=None):
        toks = tokenize(content)
        self.docs[doc_id] = {"uid": user_id, "tokens": toks,
                             "len": len(toks), "created_at": created_at}
        seen = set()
        for t in toks:
            if t not in seen:
                self.df[t] += 1
                seen.add(t)
        self.N += 1
        self.avgdl = (self.avgdl * (self.N - 1) + len(toks)) / self.N if self.N > 0 else 0.0
        # 四色卡实体索引（图谱）
        if ents is None:
            ents = extract_entities(content)
        self._doc_entities[doc_id] = ents
        for e in ents:
            self.entity_index[(user_id, e)].add(doc_id)

    # ---------- D 记忆治理：去重辅助 ----------
    def _find_identical(self, user_id, content):
        """同用户完全相同内容是否已存在（用于后写刷新而非重复建卡）。"""
        row = self.conn.execute(
            "SELECT id FROM memories WHERE user_id=? AND content=? LIMIT 1",
            (user_id, content)).fetchone()
        return row[0] if row else None

    def _refresh_doc(self, doc_id, created_at):
        """后写刷新：仅更新内存索引中的 created_at（新近度），内容不变。"""
        if doc_id in self.docs:
            self.docs[doc_id]["created_at"] = created_at

    # ---------- C 时间序列：新近度权重 ----------
    def _recency(self, d):
        ca = d.get("created_at")
        if not ca:
            return 0.0
        try:
            from datetime import datetime, timezone
            ep = datetime.strptime(ca, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
            age = max(0.0, time.time() - ep)
            return max(0.0, 1.0 - age / self.recency_tau)
        except Exception:
            return 0.0

    def rebuild_index(self):
        with self._lock:
            self.docs = {}
            self.df = defaultdict(int)
            self.N = 0
            self.avgdl = 0.0
            self._doc_entities = {}
            self.entity_index = defaultdict(set)
            cur = self.conn.execute(
                "SELECT id, user_id, content, created_at, entities FROM memories")
            for doc_id, uid, content, created_at, ents_json in cur.fetchall():
                ents = set()
                if ents_json:
                    try:
                        ents = set(json.loads(ents_json))
                    except Exception:
                        ents = extract_entities(content)
                self._index_doc(doc_id, uid, content, created_at, ents)
        logger.info(f"[store] index rebuilt: N={self.N} docs, entity_edges={len(self.entity_index)}")

    # ---------- 索引持久化（重启秒加载，根治重建阻塞端口） ----------
    def _index_sig(self):
        """廉价签名：文档数 + 最大 id + db mtime。任一变化即判定缓存失效需重建。"""
        try:
            cnt, mx = self.conn.execute(
                "SELECT COUNT(*), COALESCE(MAX(id),0) FROM memories").fetchone()
        except Exception:
            cnt, mx = 0, 0
        try:
            mtime = int(os.path.getmtime(self.db_path)) if os.path.exists(self.db_path) else 0
        except Exception:
            mtime = 0
        return (cnt, mx, mtime, _TOKENIZER_MODE)

    def _index_file(self):
        # 分词器模式入文件名：nlp / code 两套索引互不覆盖，避免切模式后误用旧缓存
        suffix = "" if _TOKENIZER_MODE == "nlp" else f".{_TOKENIZER_MODE}"
        return self.db_path + suffix + ".idx.pkl"

    def save_index(self):
        try:
            cnt, mx, _, _ = self._index_sig()
            payload = {
                "sig": (cnt, mx),
                "tokenizer": _TOKENIZER_MODE,   # 分词器切换必须使旧缓存失效（nlp 切 code 时索引不可复用）
                "max_doc_id": max(self.docs.keys(), default=0),
                "docs": self.docs,
                "df": dict(self.df),
                "N": self.N,
                "avgdl": self.avgdl,
                "_doc_entities": self._doc_entities,
                "entity_index": {k: v for k, v in self.entity_index.items()},
            }
            tmp = self._index_file() + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self._index_file())
            logger.info(f"[store] index saved (docs<= {payload['max_doc_id']}, N={self.N}, edges={len(self.entity_index)})")
        except Exception as e:
            logger.warning(f"[store] index save failed: {e}")

    def load_index(self):
        """加载落盘索引；DB 只增不减（本服务从不删行），故支持增量补齐 id>max_doc_id 的新文档。
        返回 True 表示索引已就绪（全量或 基座+增量）；False 表示需全量重建。"""
        try:
            p = self._index_file()
            if not os.path.exists(p):
                return False
            with open(p, "rb") as f:
                payload = pickle.load(f)
            cnt, mx, _, _ = self._index_sig()
            # 向后兼容：本次改动前落盘的 pkl 无 tokenizer 字段，按 nlp 处理，避免无谓触发全量重建
            if payload.get("tokenizer", "nlp") != _TOKENIZER_MODE:
                logger.info(f"[store] index tokenizer mismatch (saved={payload.get('tokenizer')} "
                            f"now={_TOKENIZER_MODE}) -> rebuild")
                return False
            saved_max = int(payload.get("max_doc_id", 0))
            if saved_max > mx:
                logger.warning(f"[store] index newer than db? saved_max={saved_max} db_max={mx} -> rebuild")
                return False
            self.docs = payload["docs"]
            self.df = defaultdict(int, payload["df"])
            self.N = payload["N"]
            self.avgdl = payload["avgdl"]
            self._doc_entities = payload["_doc_entities"]
            self.entity_index = defaultdict(set, payload["entity_index"])
            # 增量补齐：只索引落盘之后新写入的文档（秒级，无需全量 rebuild）
            n_new = 0
            if mx > saved_max:
                cur = self.conn.execute(
                    "SELECT id, user_id, content, created_at, entities FROM memories WHERE id>? ORDER BY id",
                    (saved_max,))
                for doc_id, uid, content, created_at, ents_json in cur.fetchall():
                    ents = set()
                    if ents_json:
                        try:
                            ents = set(json.loads(ents_json))
                        except Exception:
                            ents = extract_entities(content)
                    self._index_doc(doc_id, uid, content, created_at, ents)
                    n_new += 1
            logger.info(f"[store] index loaded from {p} (base N={payload['N']}, +{n_new} incremental, total N={self.N}, edges={len(self.entity_index)})")
            return True
        except Exception as e:
            logger.warning(f"[store] index load failed ({e}) -> rebuild")
            return False

    def ensure_index(self):
        """线程安全：已就绪直接返回；否则加载或重建，并落盘。"""
        if self._index_ready:
            return
        with self._idx_lock:
            if self._index_ready:
                return
            if not self.load_index():
                logger.info("[store] building index (no usable cache) ...")
                self.rebuild_index()
                self.save_index()
            self._index_ready = True

    # ---------- 检索（BM25） ----------
    def bm25_search(self, user_id, query, top_k, k1=1.5, b=0.75):
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        with self._lock:
            docs = self.docs
            df = self.df
            N = self.N
            avgdl = self.avgdl
            results = []
            for doc_id, d in docs.items():
                if d["uid"] != user_id:
                    continue
                if d["len"] == 0:
                    continue
                scores = defaultdict(int)
                for t in d["tokens"]:
                    scores[t] += 1
                score = 0.0
                for qt in q_tokens:
                    f = scores.get(qt, 0)
                    if f == 0:
                        continue
                    dft = df.get(qt, 0)
                    idf = math.log(1 + (N - dft + 0.5) / (dft + 0.5))
                    score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d["len"] / avgdl))
                if score <= 0:
                    continue
                # C 时间序列：新记忆轻微加权（recency∈[0,1]，不喧宾夺主）
                score = score * (1.0 + self.recency_alpha * self._recency(d))
                results.append((doc_id, score, d["created_at"]))
        results.sort(key=lambda x: x[1], reverse=True)
        # H 认识论安全：丢弃低于阈值的弱匹配噪声（默认阈值极低，仅滤纯噪声，保召回）
        if self.min_score > 0:
            results = [(i, s, c) for (i, s, c) in results if s >= self.min_score]
        return results[:top_k]

    # ---------- 四色卡图谱扩展（联想记忆） ----------
    def graph_expand(self, user_id, seed_doc_ids, limit, min_shared=1):
        """给定种子记忆，沿共享实体做 1 跳扩展，返回 (doc_id, shared_count) 降序。
        仅在该 user_id 内、且不重复返回种子本身；用于检索时补充联想上下文。"""
        seed_set = set(seed_doc_ids)
        seed_entities = set()
        for did in seed_doc_ids:
            ents = self._doc_entities.get(did)
            if ents:
                seed_entities |= ents
        if not seed_entities:
            return []
        shared = defaultdict(int)
        for ent in seed_entities:
            for did in self.entity_index.get((user_id, ent), ()):
                if did not in seed_set:
                    shared[did] += 1
        cand = [(did, c) for did, c in shared.items() if c >= min_shared]
        cand.sort(key=lambda x: (x[1], self.docs.get(x[0], {}).get("created_at", "")), reverse=True)
        return cand[:limit]

    def get_content(self, doc_id):
        cur = self.conn.execute(
            "SELECT role, content, ts, created_at FROM memories WHERE id=?", (doc_id,))
        row = cur.fetchone()
        if not row:
            return None
        role, content, ts, created_at = row
        if ts:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                prefix = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                return f"[{prefix}] {role.upper()}: {content}"
            except Exception:
                pass
        return f"{role.upper()}: {content}"


if __name__ == "__main__":
    s = MemoryStore(":memory:")
    s.add("r1", "u1", "s1", [{"role": "user", "content": "I adopted a cat named Luna."}], None)
    s.add("r2", "u1", "s1", [{"role": "user", "content": "Luna likes to sleep on the sofa."}], None)
    res = s.bm25_search("u1", "what is my cat name", 5)
    print("hits:", res)
    print("content:", s.get_content(res[0][0]) if res else None)
