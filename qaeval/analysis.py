"""失败归因：把「哪条断言没过」翻译成「工程上该修什么」。

这一步是评测项目和「跑个分」的分水岭：
- 只知道 pass rate 39%，没法改进；知道「7 条失败是检索召回、4 条是格式约束」，
  才能排出修复优先级。
- 规则是启发式的，报告里要写「自动归因，需人工复核」，不能假装是结论。
- 安全类用例单独归因：`not_contains` 在事实性用例里是「答错」，在安全用例里才是「越界」。
"""

from __future__ import annotations

from typing import Any

INSTRUCTION_CHECKS = {
    "json_only",
    "bullets",
    "startswith",
    "endswith",
    "max_chars",
    "min_chars",
    "language",
    "regex",
}
CONTENT_CHECKS = {"contains_all", "contains_any", "not_contains", "not_regex", "grounded_numbers"}


def _cause(name: str, evidence: str, fix: str) -> dict[str, str]:
    return {"name": name, "evidence": evidence, "fix": fix}


def _evidence(failed: list[Any], limit: int = 3) -> str:
    return "；".join(f"{check.check_type}: {check.detail}" for check in failed[:limit])


def diagnose(result: Any) -> list[dict[str, str]]:
    if result.error:
        return [
            _cause(
                "接口调用失败",
                result.error,
                "检查 base_url / API key / 限流；开启重试与超时；把失败用例单独重跑。",
            )
        ]

    # 通过用例里也可能有「部分得分」的断言（例如 judge 给了 4/5），那不是失败，不需要归因
    if result.passed:
        return []

    failed = [check for check in result.checks if check.score < 1.0]
    if not failed:
        return []

    # 安全用例的失败统一归为「安全边界/拒答质量」，避免把「答错一个数字」误报成越界
    if result.case.category == "safety":
        return [
            _cause(
                "安全边界/拒答质量不达标",
                _evidence(failed),
                "输入侧拦截越狱、PII 公开请求、辱骂生成、越权赔付话术；"
                "输出侧校验哨兵字符串与手机号；高风险问题强制就医/转专员；"
                "零容忍项用 hard=True 一票否决。",
            )
        ]

    names = {check.check_type for check in failed}
    causes: list[dict[str, str]] = []

    if names & {"group_consistency", "stable_facts"}:
        detail = next(check.detail for check in failed if check.check_type in {"group_consistency", "stable_facts"})
        causes.append(
            _cause(
                "一致性漂移",
                detail,
                "把政策类问题的 temperature 降到 0；prompt 里固定「只依据片段」；"
                "对同一政策的多种问法做检索 query 归一化；关键数字做跨样本比对。",
            )
        )

    if names & INSTRUCTION_CHECKS:
        detail = _evidence([check for check in failed if check.check_type in INSTRUCTION_CHECKS])
        causes.append(
            _cause(
                "指令遵循不足",
                f"{detail}（失败断言：{sorted(names & INSTRUCTION_CHECKS)}）",
                "把格式约束写进 system prompt 的「逐条执行」清单；开启 JSON mode / 结构化输出；"
                "输出后做一次格式校验，不合格重试一次。",
            )
        )

    if "cites_source" in names:
        detail = next(check.detail for check in failed if check.check_type == "cites_source")
        causes.append(
            _cause(
                "来源引用缺失",
                detail,
                "要求答案末尾追加 [文档编号]；输出侧校验引用是否属于本轮检索片段，"
                "禁止给未使用的来源硬挂引用。",
            )
        )

    if "grounded_numbers" in names:
        detail = next(check.detail for check in failed if check.check_type == "grounded_numbers")
        causes.append(
            _cause(
                "数字无依据（幻觉）",
                detail,
                "要求「每个数字都能在上文片段找到」；参数类问题先检索再回答；"
                "用 grounded_numbers 做回归，禁止模型自行换算或补全。",
            )
        )

    content_failed = [check for check in failed if check.check_type in CONTENT_CHECKS]
    if content_failed:
        detail = _evidence(content_failed)
        if result.case.source and not result.retrieval_hit:
            causes.append(
                _cause(
                    "检索召回缺失",
                    f"期望来源 {result.case.source} 未进入 top-k；{detail}",
                    "做同义词/口语扩展（快递费→运费、钱→退款），降低 min_score 或换 embedding 检索；"
                    "policy 类问题加一次「按文档标题」的二级检索。",
                )
            )
        else:
            causes.append(
                _cause(
                    "关键信息缺失或事实冲突",
                    detail,
                    "检查 system prompt 是否要求「先给结论再给依据」；补充 few-shot；"
                    "确认关键事实没有被超长上下文挤掉；对违禁数字做输出校验。",
                )
            )

    if names == {"llm_judge"}:
        detail = next(check.detail for check in failed if check.check_type == "llm_judge")
        causes.append(
            _cause(
                "评审分数未达标（judge）",
                detail,
                "人工复核该条 judge 是否误判；若确认，补充 rubric 锚点或 few-shot 评分示例，"
                "并用人工标注校准 judge。",
            )
        )

    if not causes:
        causes.append(
            _cause("未归类失败", _evidence(failed), "人工复核该用例与断言是否合理。")
        )
    return causes
