"""
本地契约冒烟测试：验证 Add -> Search -> Health 全链路。
用法：python smoke_test.py http://127.0.0.1:8000 [token]
无需平台密钥即可跑（AML_API_KEY 留空时服务放行）。
"""
import sys
import json
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else ""


def _post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read())


def _get(path):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def main():
    # health
    s, b = _get("/health")
    assert s == 200, f"health failed: {s}"
    print("[OK] health ->", b)

    rid = "eval:smoke:conv-0:chunk-0"
    uid = "eval:smoke:conv-0"
    sid = "eval:smoke:sample:0"
    s, b = _post("/add", {
        "request_id": rid,
        "messages": [
            {"role": "user", "content": "I adopted a cat named Luna."},
            {"role": "assistant", "content": "Nice! Luna is a lovely name."},
        ],
        "user_id": uid,
        "session_id": sid,
    })
    assert s == 200 and b.get("success") is True, f"add failed: {s} {b}"
    assert b["request_id"] == rid and b["user_id"] == uid and b["session_id"] == sid, "echo mismatch"
    print("[OK] add ->", b)

    # 幂等
    s2, b2 = _post("/add", {
        "request_id": rid,
        "messages": [{"role": "user", "content": "I adopted a cat named Luna."}],
        "user_id": uid, "session_id": sid,
    })
    assert s2 == 200 and b2.get("success") is True, "idempotent add failed"
    print("[OK] add idempotent ->", b2)

    s, b = _post("/search", {"query": "what is my cat name", "user_id": uid, "top_k": 5})
    assert s == 200 and "data" in b, f"search failed: {s} {b}"
    assert len(b["data"]) >= 1, "search returned empty"
    print("[OK] search ->", json.dumps(b["data"][:2], ensure_ascii=False))
    print("\nALL SMOKE CHECKS PASSED ✅")


if __name__ == "__main__":
    main()
