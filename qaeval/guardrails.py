"""输入/输出护栏：在模型外面加一层「便宜且确定」的防线。

为什么模型 prompt 里写了规则，还要护栏：
- prompt 是概率性的，护栏是确定性的；对「泄露提示词」「公开手机号」这类零容忍问题，
  只有确定性拦截才敢上线；
- 护栏能修「输出格式/引用缺失」，但修不了「知识库里没有答案」——这一点必须在报告里讲清楚。

v2 加固配置会打开这里的四个开关。
"""

from __future__ import annotations

import re

from .config import GuardrailOptions
from .retrieval import Chunk
from .text import normalize, tokenize

INJECTION_PATTERNS = [
    r"忽略.{0,10}(指令|规则|提示)",
    r"系统指令",
    r"系统提示词",
    r"system\s*prompt",
    r"调试模式",
    r"开发者模式",
    r"(输出|告诉我|打印).{0,10}(内部|配置|提示词|成本价)",
]

PII_REQUEST_PATTERNS = [
    r"(手机号|电话|身份证|银行卡|地址).{0,60}(评价|公布|公开|发到|写进|群里|大家|展示)",
    r"(评价|公布|公开|展示).{0,20}(手机号|电话|身份证|银行卡|地址)",
]

ABUSE_REQUEST_PATTERNS = [
    r"(骂|辱骂|脏话|攻击性).{0,12}(话|文案|一段|句子|回复)",
    r"(写|来|编).{0,8}(骂|辱骂|脏话)",
]

COMPENSATION_PATTERNS = [
    r"(赔|赔付|赔偿|补偿).{0,12}(元|块|万)",
    r"必须赔",
    r"打到我.{0,4}(支付宝|微信|银行卡)",
]

MEDICAL_KEYWORDS = ["误喝", "误食", "喝了", "过敏", "受伤", "中毒", "呼吸困难", "烫伤", "抽搐"]
ESCALATION_WORDS = ["就医", "医院", "120", "急诊", "医生", "中毒控制中心"]

PHONE_RE = re.compile(r"1[3-9]\d{9}")
ID_CARD_RE = re.compile(r"\b\d{17}[\dXx]\b")
BANK_CARD_RE = re.compile(r"\b\d{16,19}\b")
CITATION_RE = re.compile(r"\[[A-Z]+-\d+\]")

INJECTION_REFUSAL = (
    "抱歉，我无法提供系统提示词、内部配置，也不会执行调试或越权指令。"
    "我可以帮您查询商品参数、物流进度和售后政策。"
)
PII_REFUSAL = (
    "抱歉，我不能帮您公开或转发手机号、住址等个人敏感信息。"
    "如需修改评价，请在「我的-订单-评价」中自行编辑；售后问题可以走官方客服通道。"
)
ABUSE_REFUSAL = (
    "抱歉，我不能帮您生成辱骂或攻击性内容。"
    "如果服务体验有问题，可以在「我的-订单-投诉」提交，或联系人工客服升级处理。"
)
COMPENSATION_REFUSAL = (
    "非常抱歉给您带来不好的体验。关于赔付，我需要先为您登记并转交人工专员核实，"
    "无法在这里直接承诺赔偿金额；核实后会在 48 小时内联系您。"
)


def _matches(patterns: list[str], text: str) -> bool:
    norm = normalize(text)
    return any(re.search(pattern, norm, re.IGNORECASE) for pattern in patterns)


def pre_guard(user_text: str, cfg: GuardrailOptions) -> str | None:
    """模型调用前的输入过滤。返回非 None 表示短路，不再调用模型。"""
    if not cfg.enabled:
        return None
    if cfg.block_injection and _matches(INJECTION_PATTERNS, user_text):
        return INJECTION_REFUSAL
    if cfg.block_pii_requests and _matches(PII_REQUEST_PATTERNS, user_text):
        return PII_REFUSAL
    if cfg.block_abuse_requests and _matches(ABUSE_REQUEST_PATTERNS, user_text):
        return ABUSE_REFUSAL
    if cfg.escalate_compensation and _matches(COMPENSATION_PATTERNS, user_text):
        return COMPENSATION_REFUSAL
    return None


def _grounded(answer: str, chunk: Chunk) -> bool:
    """判断答案是否真的用了这个片段：至少共享 3 个 token，避免给幻觉答案硬挂引用。"""
    answer_tokens = set(tokenize(answer))
    chunk_tokens = set(tokenize(chunk.title + " " + chunk.text))
    return len(answer_tokens & chunk_tokens) >= 3


def post_guard(
    user_text: str,
    answer: str,
    retrieved_ids: list[str],
    kb: dict[str, Chunk],
    cfg: GuardrailOptions,
) -> str:
    """模型调用后的输出处理：脱敏、补引用、安全升级提示。"""
    if not cfg.enabled or not answer:
        return answer

    if cfg.redact_pii:
        answer = PHONE_RE.sub("[已脱敏手机号]", answer)
        answer = ID_CARD_RE.sub("[已脱敏证件号]", answer)
        answer = BANK_CARD_RE.sub("[已脱敏卡号]", answer)

    if cfg.require_citation and retrieved_ids and not CITATION_RE.search(answer):
        chunk = kb.get(retrieved_ids[0])
        if chunk is not None and _grounded(answer, chunk):
            answer = answer.rstrip() + f" [{chunk.id}]"

    if cfg.medical_escalation and any(word in user_text for word in MEDICAL_KEYWORDS):
        if not any(word in answer for word in ESCALATION_WORDS):
            answer = answer.rstrip() + " 另外，如涉及人身安全，请立即就医或拨打 120，并携带产品包装。"

    return answer
