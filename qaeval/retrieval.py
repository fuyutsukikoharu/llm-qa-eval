"""极简 RAG 检索：BM25 + 可选同义词扩展。

为什么用 BM25 而不是余弦：
- 中文 query 往往很长（"退货要满足什么条件？请用不超过 3 条要点回答"），
  余弦会被大量无关词稀释；BM25 有词频饱和和文档长度归一化，短知识片段更占优；
- BM25 只用词频和 IDF，完全可解释：可以打印每个 query 词贡献了多少分；
- 不引入 API 依赖，评测可以离线跑。

真实项目里把 Retriever 换成 bge/m3e + FAISS 即可，接口保持不变。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

from .text import tokenize

# 口语 -> 知识库用词。只在 retrieval.expand_synonyms=true 时启用。
# 这是「v2 加固」的核心改动之一：不改模型，先把召回修好。
SYNONYMS: dict[str, str] = {
    "不想要": "无理由退货 退货",
    "退回来": "退款 退款到账",
    "钱": "退款",
    "退款到账": "退款 到账 时效",
    "多久": "时效 几天",
    "什么时候": "时效",
    "快递费": "运费",
    "邮费": "运费",
    "快递多少钱": "运费 标准",
    "谁出": "承担",
    "有货": "现货 库存",
    "货": "商品",
    "坏了": "保修 非人为损坏",
}

_HEADER_RE = re.compile(r"^##\s+(?P<id>[A-Z]+-\d+)\s+(?P<title>.+)$", re.MULTILINE)

# 输出格式指令不是知识检索的语义内容。如果直接拿去检索，
# 「不超过 3 条要点」「不超过 30 天」里的词会污染召回（曾把 SHIP-1 排到 I-03 第一）。
# 做法：先取「问题：」之后的部分，再剥掉格式约束子句；护栏仍用完整原文判断注入。
_INSTRUCTION_NOISE = [
    re.compile(r"请只输出\s*JSON[^。？?]{0,40}"),
    re.compile(r"请严格按[^。？?]{0,40}"),
    re.compile(r"用不超过\s*\d+\s*个?字回答[：:]?"),
    re.compile(r"请用不超过\s*\d+\s*条要点回答[，,]?"),
    re.compile(r"每条不超过\s*\d+\s*个?字"),
    re.compile(r"请只回答[^。？?]{0,30}"),
    re.compile(r"回答里不要出现[^。？?]{0,40}"),
    re.compile(r"回答时请标注来源编号[^。？?]{0,40}"),
    re.compile(r"例如\s*\[[A-Z]+-\d+\]"),
    re.compile(r"(并且)?必须同时提到[^。？?]{0,40}"),
]


def prepare_query(text: str) -> str:
    """把「用户问题」从格式指令里剥出来，专供检索使用。"""
    query = text or ""
    for marker in ("用户问题：", "问题：", "用户："):
        if marker in query:
            query = query.rsplit(marker, 1)[1]
            break
    for pattern in _INSTRUCTION_NOISE:
        query = pattern.sub(" ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query.strip(" ，,。；;、")


@dataclass(frozen=True)
class Chunk:
    id: str
    doc: str
    title: str
    text: str


def load_kb(kb_dir: str | Path) -> list[Chunk]:
    """把 Markdown 知识库按 "## REFUND-1 标题" 切成 chunk。

    chunk = 检索的最小单位，也是引用编号的粒度。
    一个 chunk 只讲一件事，长度控制在 200 字内，召回和引用都更准。
    """
    chunks: list[Chunk] = []
    for path in sorted(Path(kb_dir).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        matches = list(_HEADER_RE.finditer(raw))
        for i, match in enumerate(matches):
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
            body = raw[start:end].strip()
            body = "\n".join(line for line in body.splitlines() if line.strip())
            chunks.append(
                Chunk(
                    id=match.group("id"),
                    doc=match.group("id").split("-")[0],
                    title=match.group("title").strip(),
                    text=body,
                )
            )
    if not chunks:
        raise ValueError(f"知识库目录为空或格式不对：{kb_dir}")
    return chunks


class Retriever:
    """BM25 检索器。参数 k1/b 用经典默认值，没有针对 28 条用例调参。"""

    def __init__(
        self,
        chunks: list[Chunk],
        synonyms: dict[str, str] | None = None,
        k1: float = 1.2,
        b: float = 0.75,
    ):
        self.chunks = chunks
        self.kb = {chunk.id: chunk for chunk in chunks}
        self.synonyms = synonyms or {}
        self.k1 = k1
        self.b = b
        self._tokens: list[list[str]] = []
        self._df: dict[str, int] = {}
        self._build()

    def _build(self) -> None:
        for chunk in self.chunks:
            # 标题权重通过重复 token 体现：标题里的词更代表这个 chunk 的主题
            tokens = tokenize(chunk.title) * 2 + tokenize(chunk.text)
            self._tokens.append(tokens)
            for token in set(tokens):
                self._df[token] = self._df.get(token, 0) + 1
        self._n = len(self.chunks)
        self._avgdl = sum(len(tokens) for tokens in self._tokens) / self._n
        self._idf = {
            token: math.log(1 + (self._n - df + 0.5) / (df + 0.5)) for token, df in self._df.items()
        }

    def expand(self, query: str) -> str:
        """同义词扩展：命中口语词时把知识库用词追加到 query 后面。"""
        extra = [target for key, target in self.synonyms.items() if key in query]
        return query + (" " + " ".join(extra) if extra else "")

    def search(self, query: str, top_k: int = 3, min_score: float = 2.0) -> list[tuple[Chunk, float]]:
        """返回分数 >= min_score 的 top_k 个 chunk，按分数降序。

        min_score 是「宁缺毋滥」的阈值：低于阈值就当知识库里没有，
        让模型走「不确定 + 转人工」路径，而不是拿一个弱相关片段硬答。
        """
        expanded = self.expand(prepare_query(query)) if self.synonyms else prepare_query(query)
        query_tokens = tokenize(expanded)
        if not query_tokens:
            return []
        counts: dict[str, int] = {}
        for token in query_tokens:
            counts[token] = counts.get(token, 0) + 1

        scored: list[tuple[Chunk, float]] = []
        for chunk, tokens in zip(self.chunks, self._tokens):
            doc_len = len(tokens)
            tf: dict[str, int] = {}
            for token in tokens:
                tf[token] = tf.get(token, 0) + 1
            score = 0.0
            for token, qtf in counts.items():
                freq = tf.get(token)
                if not freq:
                    continue
                idf = self._idf.get(token, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * doc_len / self._avgdl)
                score += idf * freq * (self.k1 + 1) / denom * min(qtf, 2)
            if score >= min_score:
                scored.append((chunk, round(score, 3)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
