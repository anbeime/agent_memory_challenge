"""
网关连通性 / 重排能力自检（部署到自有服务器后跑一次即可）。
用法：python check_gateway.py
读取 .env 的 GATEWAY_* 配置，验证：
  1) 网关可达且返回一个非空回复（ping）
  2) agent 模式下 RetrievalEngine 能正常完成一次检索（重排或回退 BM25）
不依赖平台密钥。无出网时（如沙箱）会报网关不可达，属正常。
"""
import os
import sys

# 加载 .env（若存在）
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    # 无 python-dotenv 时手动解析
    p = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, os.path.dirname(__file__))

from llm import GatewayLLM
from store import MemoryStore
from retrieval import RetrievalEngine


def main():
    print("=== 1) 网关 ping ===")
    llm = GatewayLLM()
    ok, detail = llm.ping()
    if ok:
        print(f"[OK] 网关可达，回复: {detail!r}")
    else:
        print(f"[FAIL] 网关不可达: {detail}")
        print("       -> 部署到自有服务器（有出网）后重试；本机沙箱无出网属预期。")
        print("       -> RETRIEVAL_MODE=agent 时网关不可达会自动回退 BM25，评测仍可跑。")
        return

    print("\n=== 2) agent 检索端到端（重排路径）===")
    os.environ["RETRIEVAL_MODE"] = "agent"
    s = MemoryStore(":memory:")
    s.add(1, "u1", "s1", [{"role": "user", "content": "My cat Luna sleeps on the sofa."}], None)
    s.add(2, "u1", "s1", [{"role": "user", "content": "I bought a red bicycle."}], None)
    s.add(3, "u1", "s1", [{"role": "user", "content": "Luna likes tuna fish."}], None)
    eng = RetrievalEngine(s)
    print(f"       engine.mode = {eng.mode} (agent=用了网关重排, bm25=网关缺失已回退)")
    hits = eng.search("u1", "what does my cat like", 3)
    print(f"       检索结果 id 顺序: {[h[0] for h in hits]}")
    if eng.llm and eng.llm.degraded:
        print("       [WARN] 网关已熔断（重排失败），本次走 BM25 兜底。")
    else:
        print("[OK] agent 检索链路正常（重排生效或已安全回退）。")
    print("\nGATEWAY CHECK DONE")


if __name__ == "__main__":
    main()
