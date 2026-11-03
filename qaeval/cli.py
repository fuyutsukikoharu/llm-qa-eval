"""命令行入口：run / validate / cases / compare。

用法：
    python -m qaeval run --config configs/baseline.yaml
    python -m qaeval run --config configs/hardened.yaml --baseline reports/baseline/summary.json
    python -m qaeval validate --config configs/baseline.yaml
    python -m qaeval cases --out docs/cases.md
    python -m qaeval compare reports/baseline/summary.json reports/hardened/summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import load_config
from .evaluator import Evaluator
from .judges import build_judge
from .loader import load_cases, validate_cases
from .metrics import compare_summaries, summarize
from .models import build_model
from .report import write_reports
from .retrieval import SYNONYMS, Retriever, load_kb


def _load_cases(cfg, only: str | None, limit: int | None):
    kb_ids = {chunk.id for chunk in load_kb(cfg.kb_dir)}
    cases = load_cases(cfg.cases_path)
    warnings = validate_cases(cases, kb_ids)
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        unknown = wanted - {case.id for case in cases}
        if unknown:
            raise SystemExit(f"未知用例 id：{sorted(unknown)}")
        cases = [case for case in cases if case.id in wanted]
    if limit:
        cases = cases[:limit]
    return cases, warnings


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.out:
        cfg.out_dir = Path(args.out).resolve()
    cases, warnings = _load_cases(cfg, args.only, args.limit)

    kb = load_kb(cfg.kb_dir)
    retriever = Retriever(kb, SYNONYMS if cfg.retrieval.expand_synonyms else None)
    model = build_model(cfg)
    judge = build_judge(cfg)

    print(
        f"▶ run={cfg.run_name} model={cfg.model} provider={cfg.provider} "
        f"cases={len(cases)} concurrency={cfg.concurrency} "
        f"retrieval(min_score={cfg.retrieval.min_score}, synonyms={cfg.retrieval.expand_synonyms}) "
        f"guardrails={cfg.guardrails.enabled}",
        flush=True,
    )
    run = Evaluator(cfg, cases, retriever, model, judge).run()
    run.warnings = warnings
    summary = summarize(run)

    baseline = None
    if args.baseline:
        baseline_path = Path(args.baseline)
        if baseline_path.exists():
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        else:
            print(f"⚠ 基线文件不存在，跳过对比：{baseline_path}")

    paths = write_reports(run, summary, cfg.out_dir, baseline)
    print(
        f"\n通过率 {summary['passed']}/{summary['total']} = {summary['pass_rate']:.1%} ｜ "
        f"P0 {summary['p0']['passed']}/{summary['p0']['total']} = {summary['p0']['pass_rate']:.1%} ｜ "
        f"平均分 {summary['avg_score']:.3f} ｜ 检索命中 {summary['retrieval']['recall_at_k']:.1%} ｜ "
        f"耗时 {summary['duration_s']}s"
    )
    for category, data in summary["by_category"].items():
        print(f"  - {category:<22} {data['passed']}/{data['total']}  {data['pass_rate']:.1%}")
    if summary["causes"]:
        print("  失败归因：" + "，".join(f"{name}×{count}" for name, count in summary["causes"].items()))
    print(f"\n📄 {paths['report']}")
    print(f"🖥  {paths['html']}")
    print(f"🧾 {paths['bad_cases']}")
    print(f"📦 {paths['summary']}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    kb = load_kb(cfg.kb_dir)
    cases = load_cases(cfg.cases_path)
    warnings = validate_cases(cases, {chunk.id for chunk in kb})
    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    print(f"✅ 用例校验通过：{len(cases)} 条 ｜ 知识库 {len(kb)} 个 chunk")
    for category, count in sorted(counts.items()):
        print(f"  - {category:<22} {count}")
    for warning in warnings:
        print(f"⚠ {warning}")
    return 0


def cmd_cases(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    cases = load_cases(cfg.cases_path)
    lines = [
        "# 用例清单（自动生成）",
        "",
        "| ID | 分类 | 级别 | 场景 | 用户输入 | 关键断言 | 期望 | 来源 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case in cases:
        turns = "<br>".join(turn.replace("|", "\\|") for turn in case.turns)
        checks = ", ".join(
            f"{check.type}({check.weight:g})" + ("*" if check.hard else "") for check in case.checks
        )
        # 注意：反斜杠不能直接写在 f-string 的表达式里（Python 3.11 及以下会报错），
        # 所以先算好变量再拼，保证项目在 3.10/3.11 上也能跑。
        expected = case.expected.replace("|", "\\|")
        lines.append(
            f"| {case.id} | {case.category} | {case.severity} | {case.title} | {turns} | "
            f"{checks} | {expected} | {case.source or '-'} |"
        )
    lines += ["", "> 权重标注在括号里，`*` 表示 hard=True（一票否决）。", ""]
    text = "\n".join(lines)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    print(f"基线：{baseline.get('run_name')}  本次：{current.get('run_name')}\n")
    print("| 指标 | 基线 | 本次 | 变化 |")
    print("|---|---|---|---|")
    for row in compare_summaries(baseline, current):
        print(f"| {row['metric']} | {row['baseline']:.3f} | {row['current']:.3f} | {row['delta']:+.3f} |")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qaeval", description="LLM 客服问答评测流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="跑一轮评测并生成报告")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--baseline", help="基线 summary.json，用于生成对比表")
    run_parser.add_argument("--out", help="覆盖配置里的输出目录")
    run_parser.add_argument("--only", help="只跑指定用例，逗号分隔，如 F-01,S-01")
    run_parser.add_argument("--limit", type=int, help="只跑前 N 条，用于冒烟测试")
    run_parser.set_defaults(func=cmd_run)

    validate_parser = sub.add_parser("validate", help="校验用例集")
    validate_parser.add_argument("--config", required=True)
    validate_parser.set_defaults(func=cmd_validate)

    cases_parser = sub.add_parser("cases", help="导出用例清单 Markdown")
    cases_parser.add_argument("--config", required=True)
    cases_parser.add_argument("--out")
    cases_parser.set_defaults(func=cmd_cases)

    compare_parser = sub.add_parser("compare", help="对比两份 summary.json")
    compare_parser.add_argument("baseline")
    compare_parser.add_argument("current")
    compare_parser.set_defaults(func=cmd_compare)
    return parser


def _load_dotenv(path: Path = Path(".env")) -> None:
    """加载项目根目录的 .env，方便本地开发；不覆盖已有的环境变量。

    .env 已在 .gitignore 中，key 不会进入仓库。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
