"""报告生成：Markdown（给 GitHub / 评审者看）+ HTML 仪表盘（截图用）+ JSONL（二次分析）。

报告不是「把结果贴出来」，而是回答四个问题：
1. 现在能不能上线？（P0 通过率、总体通过率）
2. 差在哪？（分类通过率、失败断言分布）
3. 为什么差？（自动归因 + 原始输入输出）
4. 怎么修、怎么证明修好了？（修复建议 + 复跑命令 + 基线对比）
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .metrics import category_label, compare_summaries

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cell(text: Any, limit: int = 220) -> str:
    if text is None:
        return ""
    text = str(text).replace("\r", "").strip()
    text = text.replace("|", "\\|").replace("\n", "<br>")
    return text if len(text) <= limit else text[:limit] + "…"


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_cell(item) for item in row) + " |")
    return "\n".join(lines)


def _sort_failures(results: list[Any]) -> list[Any]:
    return sorted(
        [result for result in results if not result.passed],
        key=lambda result: (SEVERITY_ORDER.get(result.case.severity, 9), result.score, result.case.id),
    )


def _bad_case_section(result: Any, threshold: float = 0.8) -> str:
    case = result.case
    lines = [f"### {case.severity} · {case.id} · {case.title}", ""]
    lines.append(f"- **得分**：{result.score:.2f}（通过门槛 {threshold:.2f}，以下按断言拆开）")
    retrieved = ", ".join(result.samples[-1].retrieved_ids) or "空"
    if case.source:
        hit_text = "是" if result.retrieval_hit else "否"
        hit_line = f"**检索命中**：{hit_text}（top-k: {retrieved}）"
    else:
        hit_line = "**检索命中**：不适用（无唯一来源）"
    lines.append(f"- **分类**：`{case.category}` ｜ **期望来源**：`{case.source or '无'}` ｜ " + hit_line)
    lines.append(f"- **设计意图**：{case.rationale}")
    for index, turn in enumerate(case.turns, start=1):
        lines.append(f"- **用户输入 {index}**：{turn}")
    lines.append(f"- **期望行为**：{case.expected}")
    answer = (result.samples[-1].answer or "").strip()
    lines.append(f"- **实际回答**：\n\n```text\n{answer[:700] or '（空）'}\n```")
    if len(result.samples) > 1:
        lines.append(f"- **重复采样**：共 {len(result.samples)} 次，用于稳定性比对")
    lines.append("- **失败断言**：")
    lines.append("")
    failed = [check for check in result.checks if check.score < 1.0]
    rows = []
    for check in failed:
        rows.append([check.check_type, check.weight, f"{check.score:.2f}", check.detail, "是" if check.hard else ""])
    lines.append(_md_table(["断言", "权重", "得分", "证据", "一票否决"], rows))
    causes = result.cause_tags
    if causes:
        lines.append("- **自动归因**：")
        for cause in causes:
            lines.append(f"  - **{cause['name']}**：{cause['evidence']}")
            lines.append(f"    - 修复建议：{cause['fix']}")
    lines.append(f"- **复跑命令**：`python -m qaeval run --config configs/baseline.yaml --only {case.id}`")
    lines.append("")
    return "\n".join(lines)


def render_markdown(run: Any, summary: dict[str, Any], baseline: dict[str, Any] | None = None) -> tuple[str, str]:
    cfg = run.config
    results = run.case_results
    failures = _sort_failures(results)
    warnings = getattr(run, "warnings", [])

    lines = [
        f"# LLM 客服评测报告 · {summary['run_name']}",
        "",
        f"> 生成时间：{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S')} ｜ "
        f"被测模型：`{summary['model']}`（provider={summary['provider']}）｜ "
        f"用例：{summary['total']} 条 ｜ 通过门槛：{summary['threshold']:.2f} ｜ 耗时：{summary['duration_s']}s",
        "",
    ]
    if cfg.provider == "mock":
        lines += [
            "> ⚠️ **本轮使用脚本化 mock 模型**，只用于验证评测流水线本身；"
            "分数不代表任何真实模型能力。接真实模型请改 `provider: openai`。",
            "",
        ]

    lines += [
        "## 0. 一分钟结论",
        "",
        _md_table(
            ["指标", "数值", "说明"],
            [
                ["总体通过率", f"{summary['passed']}/{summary['total']} = {_pct(summary['pass_rate'])}", "加权分 ≥ 门槛且无 hard 失败"],
                ["P0 通过率", f"{summary['p0']['passed']}/{summary['p0']['total']} = {_pct(summary['p0']['pass_rate'])}", "致命问题单独看，不能被平均分稀释"],
                ["平均加权分", f"{summary['avg_score']:.3f}", "各用例加权得分的平均"],
                ["检索命中率", f"{summary['retrieval']['hits']}/{summary['retrieval']['eligible']} = "
                              f"{_pct(summary['retrieval']['recall_at_k'])}", f"期望文档进入 top-{summary['retrieval']['top_k']} 的比例"],
                ["调用失败", summary["error_count"], "超时 / 限流 / 解析失败"],
                ["时延 p50 / p95", f"{summary['latency_ms']['p50']:.0f} / {summary['latency_ms']['p95']:.0f} ms", "mock 模式接近 0"],
                ["Token 消耗", f"{summary['tokens']['prompt']} / {summary['tokens']['completion']}", "prompt / completion"],
            ],
        ),
        "",
    ]

    top_causes = "、".join(f"{name}（{count} 条）" for name, count in list(summary["causes"].items())[:3]) or "无"
    lines += [
        f"**一句话结论**：{summary['total']} 条用例通过 {summary['passed']} 条（{_pct(summary['pass_rate'])}），"
        f"P0 通过率 {_pct(summary['p0']['pass_rate'])}；主要失败原因是 {top_causes}。",
        "",
        "## 1. 分类通过率",
        "",
        _md_table(
            ["分类", "通过/总数", "通过率", "平均分"],
            [
                [category_label(category), f"{data['passed']}/{data['total']}", _pct(data["pass_rate"]), f"{data['avg_score']:.2f}"]
                for category, data in summary["by_category"].items()
            ],
        ),
        "",
        "| 严重级别 | 通过/总数 | 通过率 |",
        "|---|---|---|",
    ]
    for severity, data in summary["by_severity"].items():
        lines.append(f"| {severity} | {data['passed']}/{data['total']} | {_pct(data['pass_rate'])} |")

    if baseline:
        lines += ["", "## 2. 与基线对比", "", _md_table(
            ["指标", "基线", "本次", "变化"],
            [[row["metric"], f"{row['baseline']:.3f}", f"{row['current']:.3f}",
              f"{row['delta']:+.3f}"] for row in compare_summaries(baseline, summary)],
        )]
    else:
        lines += ["", "## 2. 与基线对比", "", "本轮未提供基线文件（`--baseline`），跳过对比。"]

    lines += ["", "## 3. 失败归因", ""]
    cause_rows = []
    for name, count in summary["causes"].items():
        example = next(
            (cause for result in results for cause in result.cause_tags if cause["name"] == name),
            {"fix": ""},
        )
        cause_rows.append([name, count, example.get("fix", "")])
    if cause_rows:
        lines.append(_md_table(["根因", "涉及用例数", "修复建议"], cause_rows))
    else:
        lines.append("本轮没有失败用例。")
    lines += ["", "**失败断言分布**（同一用例可能命中多条）：", ""]
    if summary["check_failures"]:
        lines.append(_md_table(["断言", "失败次数"], [[name, count] for name, count in summary["check_failures"].items()]))
    else:
        lines.append("无。")

    if warnings:
        lines += ["", "**用例集警告**：", ""] + [f"- {warning}" for warning in warnings]

    lines += ["", f"## 4. Bad case 明细（{len(failures)} 条）", ""]
    lines += [_bad_case_section(result, cfg.pass_threshold) for result in failures] or ["本轮没有 bad case。"]

    lines += ["", "## 5. 全部用例结果", ""]
    rows = []
    for result in results:
        failed_types = sorted({check.check_type for check in result.checks if check.score < 1.0})
        rows.append(
            [
                result.case.id,
                result.case.severity,
                category_label(result.case.category),
                result.case.title,
                f"{result.score:.2f}",
                "✅" if result.passed else "❌",
                ", ".join(failed_types) or "-",
            ]
        )
    lines.append(_md_table(["ID", "级别", "分类", "用例", "得分", "结果", "失败断言"], rows))

    lines += [
        "",
        "## 6. 评测口径与局限",
        "",
        f"- **通过判定**：加权得分 ≥ {cfg.pass_threshold:.2f}，且没有 `hard=True` 断言失败，且无调用错误。",
        "- **权重**：事实/安全类关键断言权重 2，格式类 1-1.5，judge 只占 0.5-1，避免主观分主导结论。",
        "- **检索命中率**：只统计有 `source` 的用例，口径为 recall@k（期望文档是否进入 top-k），不含排序质量。",
        "- **自动归因**：基于失败断言类型的启发式规则，需人工复核，不是根因证明。",
        "- **mock 模型**：脚本化行为，仅验证流水线；真实结论必须用 `provider: openai` 重跑。",
        "- **judge 局限**：heuristic judge 只是占位；真实 judge 需用 30-50 条人工标注做一致性校准（准确率/κ）。",
        "- **单轮局限**：本轮最多 2 轮对话；真实客服的长对话、上下文遗忘、工具调用未覆盖。",
        "",
        "## 7. 复现",
        "",
        "```bash",
        f"python -m qaeval run --config configs/{cfg.run_name}.yaml",
        "# 对比两轮：",
        "python -m qaeval run --config configs/hardened.yaml --baseline reports/baseline/summary.json",
        "```",
        "",
    ]

    bad_cases = "\n".join([f"# Bad cases · {summary['run_name']}", ""] + [
        _bad_case_section(result, cfg.pass_threshold) for result in failures
    ])
    return "\n".join(lines), bad_cases


def _html_table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>" for row in rows
    )
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def render_html(run: Any, summary: dict[str, Any]) -> str:
    cfg = run.config
    cards = [
        ("总体通过率", _pct(summary["pass_rate"]), f"{summary['passed']}/{summary['total']}"),
        ("P0 通过率", _pct(summary["p0"]["pass_rate"]), f"{summary['p0']['passed']}/{summary['p0']['total']}"),
        ("平均加权分", f"{summary['avg_score']:.2f}", f"门槛 {summary['threshold']:.2f}"),
        ("检索命中率", _pct(summary["retrieval"]["recall_at_k"]), f"recall@{summary['retrieval']['top_k']}"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="card-label">{html.escape(label)}</div>'
        f'<div class="card-value">{html.escape(value)}</div>'
        f'<div class="card-sub">{html.escape(sub)}</div></div>'
        for label, value, sub in cards
    )
    category_rows = [
        [category_label(category), data["passed"], data["total"], _pct(data["pass_rate"]), f"{data['avg_score']:.2f}"]
        for category, data in summary["by_category"].items()
    ]
    cause_rows = [[name, count, example.get("fix", "")] for name, count in summary["causes"].items()
                  if (example := next((cause for result in run.case_results for cause in result.cause_tags
                                       if cause["name"] == name), {}))]
    case_rows = [
        [result.case.id, result.case.severity, category_label(result.case.category), result.case.title,
         f"{result.score:.2f}", "PASS" if result.passed else "FAIL"]
        for result in run.case_results
    ]
    bad_cards = []
    for result in _sort_failures(run.case_results):
        failed = "".join(
            f'<li><code>{html.escape(check.check_type)}</code>：{html.escape(check.detail)}</li>'
            for check in result.checks
            if check.score < 1.0
        )
        bad_cards.append(
            f'<article class="bad"><header><span class="tag {result.case.severity}">{result.case.severity}</span>'
            f'<strong>{html.escape(result.case.id)}</strong> {html.escape(result.case.title)}'
            f'<span class="score">{result.score:.2f}</span></header>'
            f'<p class="q">用户：{html.escape(" / ".join(result.case.turns))}</p>'
            f'<p class="a">回答：{html.escape((result.samples[-1].answer or "")[:500])}</p>'
            f'<ul>{failed}</ul></article>'
        )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>评测报告 · {html.escape(summary['run_name'])}</title>
<style>
:root {{ color-scheme: light; --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --brand:#2563eb; --bg:#f8fafc; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; color:var(--ink); background:var(--bg); }}
main {{ max-width:1080px; margin:0 auto; padding:40px 24px 64px; }}
h1 {{ font-size:26px; margin:0 0 8px; }}
.meta {{ color:var(--muted); font-size:13px; margin-bottom:24px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin:24px 0; }}
.card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px; }}
.card-label {{ color:var(--muted); font-size:12px; }}
.card-value {{ font-size:28px; font-weight:700; margin:4px 0; color:var(--brand); }}
.card-sub {{ color:var(--muted); font-size:12px; }}
h2 {{ font-size:18px; margin:32px 0 12px; }}
table {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:10px; overflow:hidden; font-size:13px; }}
th,td {{ padding:8px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
th {{ background:#f1f5f9; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}
.bad {{ background:#fff; border:1px solid var(--line); border-left:4px solid #dc2626; border-radius:10px; padding:14px 16px; margin:12px 0; }}
.bad header {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
.bad .score {{ margin-left:auto; font-weight:700; color:#dc2626; }}
.tag {{ font-size:11px; padding:2px 6px; border-radius:4px; background:#fee2e2; color:#991b1b; }}
.tag.P1 {{ background:#fef3c7; color:#92400e; }}
.tag.P2 {{ background:#e0f2fe; color:#075985; }}
.q, .a {{ font-size:13px; margin:8px 0; color:#374151; white-space:pre-wrap; }}
.a {{ color:#111827; }}
ul {{ margin:6px 0 0 18px; font-size:12px; color:#4b5563; }}
footer {{ margin-top:40px; color:var(--muted); font-size:12px; }}
</style></head>
<body><main>
<h1>LLM 客服评测报告 · {html.escape(summary['run_name'])}</h1>
<div class="meta">{html.escape(summary['model'])} ｜ {summary['total']} 条用例 ｜ 门槛 {summary['threshold']:.2f} ｜
开始 {html.escape(summary['started_at'])} ｜ 耗时 {summary['duration_s']}s</div>
<div class="cards">{card_html}</div>
<h2>分类通过率</h2>
{_html_table(["分类", "通过", "总数", "通过率", "平均分"], category_rows)}
<h2>失败归因</h2>
{_html_table(["根因", "用例数", "修复建议"], cause_rows) if cause_rows else "<p>无失败用例。</p>"}
<h2>Bad cases</h2>
{"".join(bad_cards) or "<p>无。</p>"}
<h2>全部用例</h2>
{_html_table(["ID", "级别", "分类", "用例", "得分", "结果"], case_rows)}
<footer>由 qaeval 生成 · provider={html.escape(cfg.provider)} · mock 结果不代表真实模型能力</footer>
</main></body></html>
"""


def write_reports(
    run: Any,
    summary: dict[str, Any],
    out_dir: str | Path,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    markdown, bad_cases = render_markdown(run, summary, baseline)
    paths = {
        "report": out / "report.md",
        "bad_cases": out / "bad_cases.md",
        "html": out / "report.html",
        "summary": out / "summary.json",
        "results": out / "results.jsonl",
    }
    paths["report"].write_text(markdown, encoding="utf-8")
    paths["bad_cases"].write_text(bad_cases, encoding="utf-8")
    paths["html"].write_text(render_html(run, summary), encoding="utf-8")
    paths["summary"].write_text(
        json.dumps({**summary, "config": run.config.to_dict()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with paths["results"].open("w", encoding="utf-8") as handle:
        for result in run.case_results:
            record = {
                "id": result.case.id,
                "category": result.case.category,
                "severity": result.case.severity,
                "title": result.case.title,
                "score": result.score,
                "passed": result.passed,
                "error": result.error,
                "retrieval_hit": result.retrieval_hit,
                "turns": result.case.turns,
                "expected": result.case.expected,
                "answers": [sample.answer for sample in result.samples],
                "retrieved_ids": result.samples[-1].retrieved_ids,
                "checks": [check.__dict__ for check in result.checks],
                "causes": result.cause_tags,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return paths
