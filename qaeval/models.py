"""模型客户端：一个 mock 模拟器 + 一个 OpenAI 兼容客户端（只用标准库）。

为什么要 mock：
- 评测流水线（加载、检索、断言、报告）本身要先跑通，不能等 API key；
- bad case 报告需要一个「确定会犯错」的基线，方便验证断言真的能抓到错误；
- mock 的行为是脚本化的，只用于演示流水线，**不能拿它的分数代表任何真实模型**。

换成真实模型只需改配置：provider: openai + base_url/model/api_key_env。
DeepSeek、通义、Kimi、vLLM、Ollama 的 /v1/chat/completions 都兼容。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .config import RunConfig


@dataclass
class ModelResponse:
    text: str
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None


class ChatModel(Protocol):
    name: str

    def chat(self, messages: list[dict[str, str]], temperature: float, max_tokens: int) -> ModelResponse: ...


def estimate_tokens(text: str) -> int:
    """粗略估算 token：中文 1 字约 0.7 token，这里用 len/1.5 做保守估计。"""
    return max(1, int(len(text or "") / 1.5))


_CONTEXT_ITEM_RE = re.compile(
    r"^\[([A-Z]+-\d+)\]\s*(.+?)\n(.*?)(?=\n\[[A-Z]+-\d+\]|\Z)",
    re.MULTILINE | re.DOTALL,
)

_INJECTION_RE = re.compile(r"忽略|系统指令|系统提示词|system\s*prompt|调试模式|开发者模式", re.IGNORECASE)
_PHONE_RE = re.compile(r"1[3-9]\d{9}")

# ---- mock 的「错误话术库」：刻意写错，用来验证断言能抓到问题 ----
_FALLBACKS = [
    (
        re.compile(r"退货|退款|退回来|退|到账|不想要|运费|快递费"),
        "您好，我们支持 30 天无理由退货，退款会在 15 个工作日内原路退回，请您放心。",
    ),
    (
        re.compile(r"会员|积分|优惠券|折扣|价格"),
        "会员价是 199 元，积分可以直接抵现，具体以结算页为准。",
    ),
    (
        re.compile(r"发货|物流|快递|配送"),
        "付款后 24 小时内发货，快递一般 3 天左右送达。",
    ),
]
_GENERIC_REFUSAL = "抱歉，这个问题我暂时无法回答，建议您联系人工客服处理。"


class MockChatModel:
    """脚本化模拟器：模拟一个「没有护栏、不做格式约束」的基线客服。

    行为（全部确定，方便复现）：
    1. 命中检索片段 -> 直接把片段第一段念给用户（啰嗦、不标来源、不遵守格式）；
    2. 命中越狱关键词 -> 把系统提示词原样吐出来（哨兵字符串泄漏）；
    3. 用户消息里有手机号并要求公开 -> 原样回显（PII 泄漏）；
    4. 没命中片段 -> 按关键词给一段固定话术（退款/会员/物流各有一套错误数字）。
    """

    name = "mock-baseline"

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 512) -> ModelResponse:
        start = time.perf_counter()
        system = messages[0]["content"] if messages else ""
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        context = _CONTEXT_ITEM_RE.findall(system)

        if _INJECTION_RE.search(user):
            text = "好的，以下是我的系统提示词原文：\n" + system.strip()
        elif _PHONE_RE.search(user) and re.search(r"评价|公布|公开|发到|写进|群里|大家", user):
            text = "已为您记录：" + re.sub(r"\s+", " ", user).strip()
        elif context:
            _, title, body = context[0]
            body = re.sub(r"\s+", " ", body).strip()
            text = f"您好，{body} 如果还有其他问题，随时联系我哦～"
        else:
            text = _GENERIC_REFUSAL
            for pattern, reply in _FALLBACKS:
                if pattern.search(user):
                    text = reply
                    break

        latency = int((time.perf_counter() - start) * 1000)
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return ModelResponse(
            text=text,
            latency_ms=latency,
            prompt_tokens=max(1, int(prompt_chars / 1.5)),
            completion_tokens=estimate_tokens(text),
        )


class OpenAICompatModel:
    """OpenAI 兼容的 /chat/completions 客户端，只用 urllib，避免额外依赖。"""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key_env: str,
        timeout_s: float = 60,
        retries: int = 2,
        temperature: float = 0.2,
    ):
        if not base_url:
            raise ValueError("provider=openai 时必须配置 base_url")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.endpoint = self.base_url if self.base_url.endswith("/chat/completions") else self.base_url + "/chat/completions"
        self.api_key_env = api_key_env
        self.timeout_s = timeout_s
        self.retries = retries

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"环境变量 {self.api_key_env} 为空。请先设置 key，例如 PowerShell："
                f"$env:{self.api_key_env}='sk-...'"
            )
        return key

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 512) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self.name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key()}",
        }
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            start = time.perf_counter()
            try:
                request = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    body = json.loads(response.read().decode("utf-8"))
                text = body["choices"][0]["message"]["content"] or ""
                usage = body.get("usage") or {}
                return ModelResponse(
                    text=text,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    prompt_tokens=int(usage.get("prompt_tokens", 0)),
                    completion_tokens=int(usage.get("completion_tokens", 0)),
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:400]
                last_error = f"HTTP {exc.code}: {detail}"
                retryable = exc.code in {408, 409, 429, 500, 502, 503, 504}
                if not retryable or attempt >= self.retries:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.retries:
                    break
            time.sleep(min(2 ** attempt, 8))
        return ModelResponse(text="", latency_ms=0, error=last_error)


def build_model(cfg: RunConfig) -> ChatModel:
    if cfg.provider == "mock":
        return MockChatModel()
    return OpenAICompatModel(
        name=cfg.model,
        base_url=cfg.base_url,
        api_key_env=cfg.api_key_env,
        timeout_s=cfg.timeout_s,
        retries=cfg.retries,
        temperature=cfg.temperature,
    )


def build_completion_model(
    model: str, base_url: str, api_key_env: str, timeout_s: float = 60, retries: int = 2
) -> OpenAICompatModel:
    """给 LLM judge 用的客户端（和被测模型分开配置，避免自己评自己）。"""
    return OpenAICompatModel(
        name=model, base_url=base_url, api_key_env=api_key_env, timeout_s=timeout_s, retries=retries
    )
