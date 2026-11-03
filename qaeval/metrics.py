"""指标汇总：通过率、分类/分级通过率、检索命中率、时延与 token 成本。

口径必须写进报告，否则数字没有意义：
- 通过 = 加权得分 >= pass_threshold 且没有 hard 断言失败 且 无调用错误；
- P0 通过率单独算，因为「平均分 85%」掩盖不了「泄露 system prompt」；
- 检索命中率 = 期望文档出现在 top-k 的比例（recall@k），只统计有 source 的用例。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .loader import CATEGORIES, SEVERITIES


def percentile(values: list[float], ratio: float) -> float:
    """最近秩百分位，足够看 p50/p95，不引入 numpy。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return float(ordered[index])


def summarize(run: Any) -> dict[str, Any]:
    results = run.case_results
    total = len(results)
    passed = sum(1 for result in results if result.passed)
    p0 = [result for result in results if result.case.severity == "P0"]

    by_category: dict[str, dict[str, Any]] = {}
    for category in CATEGORIES:
        items = [result for result in results if result.case.category == category]
        by_category[category] = {
            "total": len(items),
            "passed": sum(1 for result in items if result.passed),
            "pass_rate": round(sum(1 for result in items if result.passed) / len(items), 4) if items else 0.0,
            "avg_score": round(sum(result.score for result in items) / len(items), 4) if items else 0.0,
        }

    by_severity: dict[str, dict[str, Any]] = {}
    for severity in SEVERITIES:
        items = [result for result in results if result.case.severity == severity]
        by_severity[severity] = {
            "total": len(items),
            "passed": sum(1 for result in items if result.passed),
            "pass_rate": round(sum(1 for result in items if result.passed) / len(items), 4) if items else 0.0,
        }

    check_failures = Counter(
        check.check_type
        for result in results
        for check in result.checks
        if not check.passed
    )
    causes = Counter(
        cause["name"] for result in results for cause in result.cause_tags
    )

    latencies = [sample.latency_ms for result in results for sample in result.samples if sample.latency_ms]
    retrieval_cases = [result for result in results if result.case.source]
    retrieval_hits = sum(1 for result in retrieval_cases if result.retrieval_hit)
    hard_fail_cases = [
        result.case.id
        for result in results
        if any(check.hard and check.score < 1.0 for check in result.checks)
    ]

    return {
        "run_name": run.config.run_name,
        "model": run.config.model,
        "provider": run.config.provider,
        "started_at": run.started_at,
        "duration_s": run.duration_s,
        "threshold": run.config.pass_threshold,
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "avg_score": round(sum(result.score for result in results) / total, 4) if total else 0.0,
        "p0": {
            "total": len(p0),
            "passed": sum(1 for result in p0 if result.passed),
            "pass_rate": round(sum(1 for result in p0 if result.passed) / len(p0), 4) if p0 else 0.0,
        },
        "by_category": by_category,
        "by_severity": by_severity,
        "retrieval": {
            "eligible": len(retrieval_cases),
            "hits": retrieval_hits,
            "recall_at_k": round(retrieval_hits / len(retrieval_cases), 4) if retrieval_cases else 0.0,
            "top_k": run.config.retrieval.top_k,
        },
        "error_count": sum(1 for result in results if result.error),
        "hard_fail_cases": hard_fail_cases,
        "check_failures": dict(check_failures.most_common()),
        "causes": dict(causes.most_common()),
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        },
        "tokens": {
            "prompt": sum(sample.prompt_tokens for result in results for sample in result.samples),
            "completion": sum(sample.completion_tokens for result in results for sample in result.samples),
        },
        "cases": [
            {
                "id": result.case.id,
                "title": result.case.title,
                "category": result.case.category,
                "severity": result.case.severity,
                "score": result.score,
                "passed": result.passed,
                "retrieval_hit": result.retrieval_hit,
                "causes": [cause["name"] for cause in result.cause_tags],
            }
            for result in results
        ],
    }


def compare_summaries(baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """生成「基线 vs 本次」的对比行，用于报告里的回归表。"""
    rows = [
        {
            "metric": "总体通过率",
            "baseline": baseline.get("pass_rate", 0.0),
            "current": current.get("pass_rate", 0.0),
        },
        {
            "metric": "P0 通过率",
            "baseline": (baseline.get("p0") or {}).get("pass_rate", 0.0),
            "current": (current.get("p0") or {}).get("pass_rate", 0.0),
        },
        {
            "metric": "平均加权分",
            "baseline": baseline.get("avg_score", 0.0),
            "current": current.get("avg_score", 0.0),
        },
        {
            "metric": "检索命中率",
            "baseline": (baseline.get("retrieval") or {}).get("recall_at_k", 0.0),
            "current": (current.get("retrieval") or {}).get("recall_at_k", 0.0),
        },
    ]
    categories = set((baseline.get("by_category") or {}).keys()) | set((current.get("by_category") or {}).keys())
    for category in sorted(categories):
        rows.append(
            {
                "metric": f"分类 · {category}",
                "baseline": (baseline.get("by_category") or {}).get(category, {}).get("pass_rate", 0.0),
                "current": (current.get("by_category") or {}).get(category, {}).get("pass_rate", 0.0),
            }
        )
    for row in rows:
        row["delta"] = round(row["current"] - row["baseline"], 4)
    return rows


def category_label(category: str) -> str:
    return {
        "factuality": "事实性",
        "instruction_following": "指令遵循",
        "consistency": "一致性",
        "safety": "安全/拒答",
    }.get(category, category)
