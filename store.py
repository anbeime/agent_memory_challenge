"""
AML 记忆存储层
- SQLite 持久化（同步写入，Add 返回 200 前已落库并可检索）
- 内存 BM25 索引（按 user_id 隔离，满足「记忆不跨 user_id」约束）
- 幂等：相同 request_id 重复写入安全；冲突复用返回 409
"""
import sqlite3
import os
import re
import math
import json
import hashlib
import logging
from collections import defaultdict

logger = logging.getLogger("aml.store")

STOPWORDS = set("""
a an the and or but if then else when at by for with without from to into of in on off
is are was were be been being am do does did doing have has had having this that these those
i you he she it we they me him her us them my your his our their as at so not no yes will would
can could should may might must shall what which who whom whose how why where there here
""".split())


def tokenize(text: str):
    if not text:
        return []
    text = text.lower()
    toks = re.split(r"[^a-z0-9\u4e00-\u9fff]+", text)
    out = []
    for t in toks:
        if not t:
            continue
        if t in STOPWORDS:
            continue
        if len(t) < 2:
            continue
        out.append(t)
    return out


# 中文功能词/高频词（避免误连）。四色卡实体抽取时剔除。
CJK_STOP = set(
    "我们 你们 他们 她们 它们 自己 这个 那个 这些 那些 什么 怎么 为什么 因为 所以 "
    "但是 一个 没有 现在 时候 已经 还是 应该 可能 如果 然后 这样 那样 这种 那种 可以 "
    "需要 想要 觉得 知道 喜欢 问题 东西 感觉 一直 其实 真的 比较 或者 而且 虽然 不过 "
    "这么 那么 这个 那个 一样 起来 出来 一些 这个 通过 由于 对于 以及 并且 进行 表示 "
    "认为 发现 看到 告诉 回答 帮助 希望 感谢 请问 您好 你好 我们 你们".split()
)


def extract_entities(text: str):
    """规则化实体抽取（零 LLM、零延迟）。
    返回实体集合：英文专有名词/长词、数字、引号内串、长度>=2 的中文词（剔除功能词）。
    这是四色卡『结构化元数据 + 强制溯源 + 图谱链接』的写入侧实现。"""
    if not text:
        return set()
    entities = set()
    # 1) 英文专有名词（首字母大写）/ 长词 / 数字（含百分比）
    for m in re.finditer(r"[A-Z][a-zA-Z]+|[A-Za-z][a-zA-Z]{3,}|\d+(?:\.\d+)?%?", text):
        entities.add(m.group(0).lower())
    # 2) 引号内串（中英文引号）
    for m in re.finditer(r"[\"“『]([^\"”』]{1,24})[\"”』]", text):
        v = m.group(1).strip().lower()
        if v:
            entities.add(v)
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
        self._init_schema()
        # BM25 状态
        self.docs = {}                # doc_id -> {"uid","tokens","len","created_at"}
        self.df = defaultdict(int)    # term -> 文档频率
        self.N = 0
        self.avgdl = 0.0
        # 四色卡：实体索引（图谱）+ 每文档实体缓存
        self._doc_entities = {}                   # doc_id -> set(entities)
        self.entity_index = defaultdict(set)      # (user_id, entity) -> set(doc_id)
        self._lock = __import__("threading").Lock()
        self.rebuild_index()

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
                if score > 0:
                    results.append((doc_id, score, d["created_at"]))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    # ---------- 四色卡图谱扩展（联想记忆） ----------
    def graph_expand(self, user_id, seed_doc_ids, limit, min_shared=2):
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
