"""文本处理工具：全半角归一化、中文分词（字符 bigram）、数字与事实抽取。

为什么单独一层：
- 断言（checks）和检索（retrieval）都要做「文本归一化」，如果各写各的，
  会出现「同一个 7 天，在 A 检查里匹配、在 B 检查里不匹配」的诡异现象。
- 中文没有空格，标准分词器（jieba 等）又重又难以解释；字符 bigram 对
  「退款时效 / 退款多久到账」这类短 query 足够好用，而且零依赖。
"""

from __future__ import annotations

import re
import unicodedata

# 中文里的破折号/连接号有五六种写法，统一成 ASCII 的 "-"，否则 "1–3 个工作日"
# 永远匹配不上用例里写的 "1-3个工作日"。
_DASHES = dict.fromkeys(map(ord, "—–−－‒―"), "-")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)

_SPACE_RE = re.compile(r"\s+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_+-]*")
_CITATION_RE = re.compile(r"\[[A-Z]+-\d+\]")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_FACT_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)\s*个?\s*"
    r"(工作日|自然日|天|小时|分钟|毫升|克|千克|元|块|积分|折|年|个月|月|期|%|摄氏度)"
)


def normalize(text: str) -> str:
    """归一化到「可比较」的形态：NFKC、小写、统一连字符、去掉所有空白。

    去掉空白是刻意的：模型经常输出 "7 个自然日"，知识库写的是 "7个自然日"。
    代价是 "a b" 和 "ab" 会被视为相同，对中文短断言来说可以接受。
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_ZERO_WIDTH)
    text = text.lower()
    return _SPACE_RE.sub("", text)


def compact(text: str) -> str:
    """只压缩空白，保留大小写与标点。用于 startswith / 末行比对。"""
    return _SPACE_RE.sub("", text or "").strip()


def has_all(text: str, values: list[str]) -> tuple[bool, list[str]]:
    """全部命中才算通过，返回 (是否通过, 缺失项)。"""
    norm = normalize(text)
    missing = [v for v in values if normalize(v) not in norm]
    return (not missing, missing)


def has_any(text: str, values: list[str]) -> tuple[bool, list[str]]:
    norm = normalize(text)
    hit = [v for v in values if normalize(v) in norm]
    return (bool(hit), hit)


def count_matches(text: str, values: list[str]) -> list[str]:
    norm = normalize(text)
    return [v for v in values if normalize(v) in norm]


def cjk_ratio(text: str) -> float:
    """中文字符占「有效字符」的比例，用于语言断言。数字/标点/英文不计入分母。"""
    if not text:
        return 0.0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    letters = len(re.findall(r"[a-zA-Z]", text))
    total = cjk + letters
    return cjk / total if total else 0.0


def tokenize(text: str) -> list[str]:
    """中文按字符 bigram、英文按单词切分，给检索用。

    "退款到账" -> ["退款", "款到", "到账"]
    单字成段时保留单字，避免 "退" 这类短词被丢掉。
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens.extend(_ASCII_WORD_RE.findall(text))
    return tokens


def strip_citations(text: str) -> str:
    """去掉 [REFUND-1] 这类引用标记，避免把编号里的数字当成业务数字。"""
    return _CITATION_RE.sub(" ", text or "")


def extract_numbers(text: str) -> list[str]:
    """抽取答案中的数字，用于 grounded_numbers（数字必须有知识库出处）。"""
    return _NUMBER_RE.findall(strip_citations(text))


def extract_fact_tokens(text: str) -> list[str]:
    """抽取「数字 + 单位」的事实签名，例如 ["1-3工作日", "7工作日"]。

    用于两类一致性检查：
    - stable_facts：同一问题采样 3 次，签名必须完全相同；
    - group_consistency：同一政策的不同问法，签名必须完全相同。
    """
    tokens = set()
    normalized = normalize(strip_citations(text))
    for number, unit in _FACT_RE.findall(normalized):
        tokens.add(f"{number}{unit}")
    return sorted(tokens)


def strip_code_fences(text: str) -> str:
    """剥掉 ```json ... ``` 代码块，方便判断「是不是纯 JSON」。"""
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def truncate(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"
