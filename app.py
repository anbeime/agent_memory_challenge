"""
Agent Memory Leaderboard — Add/Search/Health 适配服务
严格遵循 Cycle 2 官方契约（已对照 ReFind #2 提交验证）：
  POST /add  (alias /v1/memories/add)
  POST /search (alias /v1/memories/search)
  GET  /health
鉴权：Authorization: Bearer / Token / X-Api-Key；AML_API_KEY 为空时放行（本地 smoke）。
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

store = MemoryStore(DB_PATH)
engine = RetrievalEngine(store)

app = FastAPI(title="Antinet-AML-Adapter", version="0.1.0")


# ---------------- 鉴权 ----------------
def auth_ok(request: Request) -> bool:
    if not EXPECTED_TOKEN:
        return True
    hdr = request.headers.get("authorization", "")
    token = None
    if hdr.startswith("Bearer "):
        token = hdr[7:].strip()
    elif hdr.startswith("Token "):
        token = hdr[6:].strip()
    if token is None:
        token = request.headers.get("x-api-key", "").strip()
    if not token:
        return False
    return hmac.compare_digest(token, EXPECTED_TOKEN)


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
        data.append({
            "id": str(doc_id),
            "content": content,
            "score": round(float(score), 4),
            "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return {"data": data}


if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting AML adapter on :{PORT}  auth={'ON' if EXPECTED_TOKEN else 'OFF'}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
