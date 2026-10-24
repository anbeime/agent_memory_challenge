"""
检索引擎
- 默认：纯 BM25（离线、零依赖、稳）
- 可选：SEMANTIC=1 时叠加文本向量（学术赛道用 text-embedding-v4），BM25 与向量 RRF 融合
  满足「学术赛道 embedding 必须用 text-embedding-v4」的硬规则。
"""
import os
import logging
import math
from store import MemoryStore, tokenize

logger = logging.getLogger("aml.retrieval")


class SemanticEmbedder:
    """优先 DashScope text-embedding-v4（规则指定），回退本地 sentence-transformers。"""
    def __init__(self):
        self.kind = None
        self._model = None
        self.dim = 0
        key = os.getenv("DASHSCOPE_API_KEY", "").strip()
        if key:
            try:
                import dashscope
                dashscope.api_key = key
                self.kind = "dashscope"
                self.dim = 1024
                logger.info("[semantic] using DashScope text-embedding-v4")
                return
            except Exception as e:
                logger.warning(f"[semantic] dashscope init failed: {e}")
        # 回退：本地模型
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
            self.kind = "local"
            self.dim = 384
            logger.info("[semantic] using local all-MiniLM-L6-v2")
        except Exception as e:
            logger.warning(f"[semantic] local embedder unavailable: {e}")
            self.kind = None

    def embed(self, texts):
        if self.kind == "dashscope":
            import dashscope
            resp = dashscope.TextEmbedding.call(
                model="text-embedding-v4",
                input=texts,
                dimension=1024,
            )
            return [d["embedding"] for d in resp.output["embeddings"]]
        if self.kind == "local" and self._model:
            return self._model.encode(texts, normalize_embeddings=True).tolist()
        return None


def rrf(*rank_lists, k=60):
    fused = {}
    for ranks in rank_lists:
        for i, item in enumerate(ranks):
            doc_id = item[0]
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + i + 1)
    return sorted(fused.items(), key=lambda x: x[1], reverse=True)


class RetrievalEngine:
    def __init__(self, store: MemoryStore):
        self.store = store
        self.mode = os.getenv("RETRIEVAL_MODE", "bm25").strip().lower()
        self.semantic_on = os.getenv("SEMANTIC", "0") == "1"
        self.embedder = SemanticEmbedder() if self.semantic_on else None
        if self.semantic_on and not self.embedder.kind:
            logger.warning("[retrieval] SEMANTIC=1 but no embedder; falling back to BM25 only")
            self.semantic_on = False
        # agent 模式：BM25 粗排 + 米鱼菜菜 gateway (auto) LLM 重排
        self.llm = None
        if self.mode == "agent":
            try:
                from llm import GatewayLLM
                self.llm = GatewayLLM()
                if not self.llm.enabled:
                    logger.warning("[retrieval] agent mode requested but gateway token missing -> fallback bm25")
                    self.mode = "bm25"
                else:
                    logger.info(f"[retrieval] agent mode (gateway LLM reranker), model={self.llm.model}")
            except Exception as e:
                logger.warning(f"[retrieval] agent init failed ({e}) -> fallback bm25")
                self.mode = "bm25"
        # 向量缓存：doc_id -> vec
        self._vec_cache = {}

    def _ensure_vec(self, doc_id, content):
        if doc_id in self._vec_cache:
            return self._vec_cache[doc_id]
        if not self.embedder:
            return None
        try:
            v = self.embedder.embed([content])[0]
            self._vec_cache[doc_id] = v
            return v
        except Exception as e:
            logger.warning(f"[retrieval] embed fail doc {doc_id}: {e}")
            return None

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    def search(self, user_id, query, top_k):
        top_k = max(1, min(int(top_k or 100), 100))
        if self.mode == "agent" and self.llm:
            return self._search_agent(user_id, query, top_k)
        bm25 = self.store.bm25_search(user_id, query, top_k * 3)
        if not self.semantic_on:
            return bm25[:top_k]

        # 语义分支：取 BM25 候选 + 全量 user 记忆候选做向量打分
        cand_ids = {d[0] for d in bm25}
        # 扩展候选到该用户全部记忆（向量可补召回）
        with self.store._lock:
            for did, d in self.store.docs.items():
                if d["uid"] == user_id:
                    cand_ids.add(did)
        vec_q = self._ensure_vec(-1, query)
        if vec_q is None:
            return bm25[:top_k]
        sem_ranks = []
        scored = []
        for did in cand_ids:
            content = self.store.get_content(did)
            v = self._ensure_vec(did, content)
            if v:
                scored.append((did, self._cosine(vec_q, v)))
        scored.sort(key=lambda x: x[1], reverse=True)
        sem_ranks = scored[: top_k * 3]
        fused = rrf(bm25, sem_ranks)
        return [(did, score, self.store.docs.get(did, {}).get("created_at")) for did, score in fused[:top_k]]

    # ---------------- agent 模式：BM25 粗排 + gateway LLM 重排 ----------------
    def _search_agent(self, user_id, query, top_k):
        cand = self.store.bm25_search(user_id, query, 15)
        if not cand:
            return []
        items = [(doc_id, self.store.get_content(doc_id) or "") for doc_id, _, _ in cand]
        system = (
            "You are a memory retrieval reranker. Given a user query and a set of candidate "
            "memories, return ONLY the candidate ids most relevant to the query, ordered from "
            "most to least relevant. Output format: a comma-separated list of ids only, "
            "e.g. r1,r3,r2. Do not output any other text."
        )
        user = f"Query: {query}\n\nCandidates:\n" + "\n".join(
            f"[{i}] id={doc_id}: {content[:280]}" for i, (doc_id, content) in enumerate(items)
        )
        out = self.llm.chat(system, user, max_tokens=200, temperature=0.0)
        order = self._parse_rank(out, [d for d, _ in items]) if out else []
        seen = set()
        ranked = []
        for did in order:  # LLM 重排优先
            if did not in seen:
                seen.add(did)
                ranked.append(did)
        for doc_id, _, _ in cand:  # 兜底补齐 LLM 漏排的候选（按 BM25 原序）
            if doc_id not in seen:
                seen.add(doc_id)
                ranked.append(doc_id)
        ranked = ranked[:top_k]
        return [(did, float(len(ranked) - i), self.store.docs.get(did, {}).get("created_at"))
                for i, did in enumerate(ranked)]

    @staticmethod
    def _parse_rank(text, valid_ids):
        if not text:
            return []
        toks = [t.strip().strip("[]").strip("`").strip() for t in text.replace("\n", ",").split(",")]
        # LLM 返回的是字符串 id，需映射回原始 id 类型（可能为 int/str）
        valid_map = {str(v): v for v in valid_ids}
        return [valid_map[t] for t in toks if t in valid_map]
