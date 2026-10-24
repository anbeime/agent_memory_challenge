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
        self.conn.commit()

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

            doc_ids = []
            for m in messages:
                content = m.get("content", "")
                if not content or not content.strip():
                    continue
                ts = m.get("timestamp")
                role = m.get("role", "user")
                cur = self.conn.execute(
                    "INSERT INTO memories (user_id, session_id, request_id, role, content, ts, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (user_id, session_id, request_id, role, content, ts, created_at))
                doc_id = cur.lastrowid
                doc_ids.append(doc_id)
                self._index_doc(doc_id, user_id, content, created_at)

            self.conn.execute(
                "INSERT INTO processed_requests (request_id, user_id, session_id, payload_hash) VALUES (?,?,?,?)",
                (request_id, user_id, session_id, phash))
            self.conn.commit()
            return ("ok", doc_ids)

    # ---------- 索引 ----------
    def _index_doc(self, doc_id, user_id, content, created_at):
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

    def rebuild_index(self):
        with self._lock:
            self.docs = {}
            self.df = defaultdict(int)
            self.N = 0
            self.avgdl = 0.0
            cur = self.conn.execute(
                "SELECT id, user_id, content, created_at FROM memories")
            for doc_id, uid, content, created_at in cur.fetchall():
                self._index_doc(doc_id, uid, content, created_at)
        logger.info(f"[store] index rebuilt: N={self.N} docs")

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
