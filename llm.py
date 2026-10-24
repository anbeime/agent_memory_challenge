"""
米鱼菜菜 gateway LLM 客户端（OpenAI 兼容 chat/completions，model=auto 自动路由）。

⚠️ 定位：这是「LLM（chat）端点」，不是 embedding 端点。
⚠️ 合规：仅可用于「开放方法」赛道（模型自由）。学术赛道强制 LLM=gpt-4o-mini，禁止用此网关。
依赖：标准库，无第三方包（沙箱/小机都能跑）。
"""
import os
import json
import logging
import urllib.request

logger = logging.getLogger("aml.llm")


class GatewayLLM:
    def __init__(self):
        self.url = os.getenv(
            "GATEWAY_URL",
            "https://api1.miyucaicai.cn/api/ai/chat/completions",
        ).strip()
        self.token = os.getenv("GATEWAY_TOKEN", "").strip()
        self.model = os.getenv("GATEWAY_MODEL", "auto").strip()
        self.enabled = bool(self.token)
        # 单call超时（秒）：网关慢时不要傻等，到期即回退 BM25
        self.timeout = int(os.getenv("GATEWAY_TIMEOUT", "8"))
        # 连续失败达到此数 -> 熔断，本进程剩余请求直接走 BM25，不再打网关
        self.max_failures = int(os.getenv("GATEWAY_MAX_FAILURES", "5"))
        self.fail_count = 0
        self.degraded = False

    def chat(self, system, user, max_tokens=512, temperature=0.0):
        if not self.enabled:
            return None
        if self.degraded:
            return None  # 已熔断，跳过网络调用，由调用方回退 BM25
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self.fail_count = 0  # 成功即复位熔断计数
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            self.fail_count += 1
            if self.fail_count >= self.max_failures:
                self.degraded = True
                logger.error(
                    f"[llm] gateway DEGRADED after {self.fail_count} consecutive failures "
                    f"-> falling back to pure BM25 for the rest of this run"
                )
            else:
                logger.warning(f"[llm] gateway call failed ({self.fail_count}/{self.max_failures}): {e}")
            return None

    def ping(self):
        """连通性自检：绕过熔断直接打一次，返回 (ok, detail)。"""
        if not self.enabled:
            return False, "GATEWAY_TOKEN 未设置"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Reply with the single word: PONG"},
                {"role": "user", "content": "ping"},
            ],
            "max_tokens": 10,
            "temperature": 0.0,
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            return True, (content or "").strip()[:50]
        except Exception as e:
            return False, str(e)
