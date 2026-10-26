"""
Agent Memory Leaderboard — Add/Search/Health 适配服务
严格遵循 Cycle 2 官方契约（已对照 ReFind #2 提交验证）：
  POST /add  (alias /v1/memories/add)
  POST /search (alias /v1/memories/search)
  GET  /health
鉴权：平台实测不发送任何鉴权头 → 无 token 调用一律放行（开放端点）；
仅当调用方主动携带 Authorization/X-Api-Key 时才与 AML_API_KEY/LDBD_KEY 匹配校验。
"""
import os
import sys
import hmac
import logging
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aml.app")

# ---- 极简 .env 加载（离线优先，不强制依赖 python-dotenv） ----
def load_env():
    p = Path(__file__).parent / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env()

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from store import MemoryStore
from retrieval import RetrievalEngine

DB_PATH = os.getenv("AML_DB_PATH", str(Path(__file__).parent / "aml_mem.db"))
# 优先读云平台注入的 $PORT（Cloud Run / 12-factor），否则回退 AML_PORT，默认 8080
PORT = int(os.getenv("PORT") or os.getenv("AML_PORT", "8080"))
EXPECTED_TOKEN = os.getenv("AML_API_KEY", "").strip()
LDBD_TOKEN = os.getenv("LDBD_KEY", "").strip()

store = MemoryStore(DB_PATH)
engine = RetrievalEngine(store)

app = FastAPI(title="Antinet-AML-Adapter", version="0.1.0")


# ---------------- 鉴权 ----------------
# 重要（据 2026-10-25 平台实测 tcpdump 修正）：
# 平台在 Smoke/Full 评测中对我们的 /add、/search 端点【不发送任何 Authorization / X-Api-Key】。
# 抓包确认 POST /add 仅带 Host/User-Agent/Content-Type 等标准头，无任何鉴权头。
# 因此端点对“无 token 调用”一律放行（开放端点）；仅当调用方主动携带 token 时才做匹配校验。
def auth_ok(request: Request) -> bool:
    raw = request.headers.get("authorization", "").strip()
    token = raw
    # 去掉常见前缀（Bearer / Token / Basic），兼容"无前缀原始 token"
    for prefix in ("Bearer ", "Token ", "Basic "):
        if token.startswith(prefix):
            token = token[len(prefix):].strip()
            break
    if not token:
        token = request.headers.get("x-api-key", "").strip()
    # 平台不携带任何 token → 直接放行（开放端点）
    if not token:
        return True
    # 调用方携带了 token：必须与 .env 中任一密钥匹配（防误配 / 防滥用）
    candidates = [t for t in (EXPECTED_TOKEN, LDBD_TOKEN) if t]
    if not candidates:
        return True
    ok = any(hmac.compare_digest(token, c) for c in candidates)
    if not ok:
        scheme = (raw.split(" ", 1)[0] if raw else "MISSING")
        logger.warning("AUTH_FAIL token_mismatch authorization_scheme=%s", scheme)
    return ok


# ---------------- 模型 ----------------
class Message(BaseModel):
    role: str
    content: str
    timestamp: Optional[int] = None


class AddRequest(BaseModel):
    request_id: str
    messages: List[Message]
    user_id: str
    session_id: str


class SearchRequest(BaseModel):
    query: str
    options: Optional[List[str]] = None
    user_id: str
    top_k: int = 100


# ---------------- 端点 ----------------
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/add")
@app.post("/v1/memories/add")
async def add(req: AddRequest, request: Request):
    if not auth_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    if not req.request_id or not req.user_id or not req.session_id:
        raise HTTPException(status_code=400, detail="request_id/user_id/session_id required")
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages required")
    msgs = [m.model_dump() for m in req.messages]
    store.add(req.request_id, req.user_id, req.session_id, msgs, None)
    # 宽松幂等：相同 request_id（无论 payload 是否相同）一律回显成功，
    # 防止平台重试（同 request_id）被判 409 失败，且更贴合官方 200 回显契约。
    return {
        "success": True,
        "request_id": req.request_id,
        "user_id": req.user_id,
        "session_id": req.session_id,
    }


@app.post("/search")
@app.post("/v1/memories/search")
async def search(req: SearchRequest, request: Request):
    if not auth_ok(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    if not req.query or not req.user_id:
        raise HTTPException(status_code=400, detail="query/user_id required")
    top_k = max(1, min(int(req.top_k or 100), 100))
    hits = engine.search(req.user_id, req.query, top_k)
    data = []
    for doc_id, score, created_at in hits:
        content = store.get_content(doc_id)
        if content is None:
            continue
        # Search 载荷截断：content 限长 ~300 字（语义完整截断），降出站带宽
        # 64 worker 并发下，100 条 × 300 字 vs 100 条 × 2000 字，出站量差 6 倍+
        truncated_content = content[:300]
        # 避免截断在单词中间（简单回退到空格）
        if len(content) > 300 and truncated_content[-1] not in (' ', '。', '！', '？', '.', '!', '?', '\n'):
            truncated_content = truncated_content[:truncated_content.rfind(' ')]
            if not truncated_content:
                truncated_content = content[:100]  # 回退硬截断
        data.append({
            "id": str(doc_id),
            "content": truncated_content,
            "score": round(float(score), 4),
            "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return {"data": data}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting AML adapter on :{PORT}  auth={'ON' if EXPECTED_TOKEN else 'OFF'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
