"""检索标定脚本：打印每条用例首轮 query 的 top-k 命中与分数。

min_score 不是拍脑袋定的：先看「正确文档」和「无关文档」的分数分布，
再选一个能分开两者的阈值。这个脚本就是用来做这件事的。

    python scripts/inspect_retrieval.py --config configs/baseline.yaml
    python scripts/inspect_retrieval.py --config configs/hardened.yaml --synonyms
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qaeval.config import load_config  # noqa: E402
from qaeval.loader import load_cases  # noqa: E402
from qaeval.retrieval import SYNONYMS, Retriever, load_kb  # noqa: E402


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--synonyms", action="store_true")
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()

    cfg = load_config(args.config)
    retriever = Retriever(load_kb(cfg.kb_dir), SYNONYMS if args.synonyms else None)
    cases = load_cases(cfg.cases_path)

    print(f"synonyms={args.synonyms}  top_k={args.top_k}")
    print(f"{'ID':<5} {'期望来源':<10} {'命中':<6} top-{args.top_k}（分数）")
    print("-" * 100)
    hit = 0
    total = 0
    for case in cases:
        results = retriever.search(case.turns[0], top_k=args.top_k, min_score=0.0)
        top = ", ".join(f"{chunk.id}:{score:.2f}" for chunk, score in results[: args.top_k])
        if case.source:
            total += 1
            docs = [chunk.id.split("-")[0] for chunk, _ in results[: args.top_k]]
            ok = case.source.split("-")[0] in docs
            hit += ok
            flag = "✅" if ok else "❌"
        else:
            flag = "-"
        print(f"{case.id:<5} {case.source or '-':<10} {flag:<6} {top}")
    print("-" * 100)
    print(f"recall@{args.top_k} = {hit}/{total} = {hit / total:.1%}")
    for threshold in (1.5, 2.0, 2.5, 3.0, 4.0):
        counts = [len(retriever.search(case.turns[0], top_k=args.top_k, min_score=threshold)) for case in cases]
        print(f"min_score={threshold:<4} 平均上下文 {sum(counts) / len(counts):.1f} 段，"
              f"无上下文用例 {sum(1 for c in counts if c == 0)} 条")


if __name__ == "__main__":
    main()
