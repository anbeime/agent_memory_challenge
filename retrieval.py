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
        # 四色卡模式：BM25 主召回 + 实体图谱 1 跳联想扩展（零 LLM、零延迟）
        # min_shared=2：仅共享≥2 实体才联想，质量优先、噪声低（H 认识论安全）；
        # 配合下方 _search_card 的「强制末位 reserve slots」，即便 primary 充足也稳定插入高 shared 联想，
        # 补 G 上下文学习弱项，无需靠降低 min_shared 来换取联想召回。
        self.card_min_shared = int(os.getenv("CARD_MIN_SHARED", "2"))  # 质量优先：共享≥2 实体才联想
        self.card_reserve = int(os.getenv("CARD_RESERVE_SLOTS", "1"))  # 强制保留 1 个 slot 给高 shared 联想
        if self.mode == "card":
            logger.info(f"[retrieval] four-color-card mode (BM25 + graph expand, min_shared={self.card_min_shared}, reserve={self.card_reserve})")
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
        if self.mode == "card":
            return self._search_card(user_id, query, top_k)
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

    # ---------------- 四色卡模式：BM25 主召回 + 实体图谱联想扩展 ----------------
    def _search_card(self, user_id, query, top_k):
        primary = self.store.bm25_search(user_id, query, top_k * 3)
        if not primary:
            return []
        seed = [d[0] for d in primary]
        expanded = self.store.graph_expand(user_id, seed, top_k, min_shared=self.card_min_shared)
        if not expanded:
            return primary[:top_k]
        # 强制保留末位 reserve 个 slots 给高 shared 实体联想，使调参在 primary
        # 充足时也稳定生效（不依赖"primary 不足"这一偶然条件），补 G 上下文学习
        reserve = min(self.card_reserve, top_k)
        main_slots = top_k - reserve
        result = []
        seen = set()
        # 1) 主召回先占 (top_k - reserve) 个名额（按 BM25 分降序）
        for did, score, ca in primary:
            if did not in seen:
                seen.add(did)
                result.append((did, score, ca))
            if len(result) >= main_slots:
                break
        # 2) 高 shared 联想记忆强制填末位 reserve 个名额（即使 primary 充足也插入）
        for did, shared in expanded:
            if did not in seen and len(result) < top_k:
                seen.add(did)
                ca = self.store.docs.get(did, {}).get("created_at")
                # 联想记忆按"共享实体密度"给微评分（<< BM25 主分，绝不压过主召回），
                # 让关联记忆按相关性排布，补 G 上下文学习与执行弱项
                result.append((did, round(0.01 * shared, 4), ca))
        # 3) 若 primary 充足但联想候选不足 reserve，用未入选的 primary 补满 top_k
        if len(result) < top_k:
            for did, score, ca in primary:
                if did not in seen:
                    seen.add(did)
                    result.append((did, score, ca))
                if len(result) >= top_k:
                    break
        return result[:top_k]

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
