"""用例加载与校验。

用例是评测的「测试代码」，写错了整个评测就失真。因此这里做严格校验：
- 断言类型必须已注册，参数名必须拼写正确（否则会出现「检查永远通过」的假绿）；
- 用例 id 不能重复（报告和回归对比都靠 id 定位）；
- source 必须指向真实存在的知识库条目（否则检索命中率无从统计）；
- 同一个 group 至少 2 条（否则组内一致性没有意义）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .checks import CHECK_PARAMS, validate_judge_params

CATEGORIES = ("factuality", "instruction_following", "consistency", "safety")
SEVERITIES = ("P0", "P1", "P2")
ID_RE = re.compile(r"^(?:F|I|C|S)-\d{2}$")


class CaseValidationError(ValueError):
    pass


@dataclass(frozen=True)
class CheckSpec:
    type: str
    weight: float = 1.0
    hard: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Case:
    id: str
    category: str
    severity: str
    title: str
    rationale: str
    turns: list[str]
    checks: list[CheckSpec]
    expected: str
    source: str | None = None
    group: str | None = None
    group_weight: float = 1.5
    repeat: int = 1


def _build_check(case_id: str, raw: dict[str, Any]) -> CheckSpec:
    if not isinstance(raw, dict) or "type" not in raw:
        raise CaseValidationError(f"{case_id}: 每条 check 必须是含 type 的对象")
    check_type = str(raw["type"])
    if check_type not in CHECK_PARAMS:
        raise CaseValidationError(
            f"{case_id}: 未知断言 {check_type}，可用：{', '.join(sorted(CHECK_PARAMS))}"
        )
    weight = float(raw.get("weight", 1.0))
    if weight <= 0:
        raise CaseValidationError(f"{case_id}: 断言 {check_type} 的 weight 必须 > 0")
    params = {k: v for k, v in raw.items() if k not in {"type", "weight", "hard"}}
    spec = CheckSpec(type=check_type, weight=weight, hard=bool(raw.get("hard", False)), params=params)

    allowed = set(CHECK_PARAMS[check_type].get("required", [])) | set(
        CHECK_PARAMS[check_type].get("optional", [])
    )
    missing = [name for name in CHECK_PARAMS[check_type].get("required", []) if name not in params]
    unknown = [name for name in params if name not in allowed]
    if missing:
        raise CaseValidationError(f"{case_id}: {check_type} 缺少参数 {missing}")
    if unknown:
        raise CaseValidationError(f"{case_id}: {check_type} 未定义参数 {unknown}")
    if check_type == "llm_judge":
        try:
            validate_judge_params(spec)
        except ValueError as exc:
            raise CaseValidationError(f"{case_id}: {exc}") from exc
    if check_type in {"contains_all", "contains_any", "not_contains", "no_over_refusal"}:
        values = params["values"] if "values" in params else params.get("expected_any")
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise CaseValidationError(f"{case_id}: {check_type} 的 values 必须是非空字符串列表")
    if check_type in {"regex", "not_regex"}:
        try:
            re.compile(str(params["pattern"]))
        except re.error as exc:
            raise CaseValidationError(f"{case_id}: 正则非法 {params['pattern']}：{exc}") from exc
    return spec


def load_cases(path: str | Path) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items = raw.get("cases")
    if not isinstance(items, list) or not items:
        raise CaseValidationError(f"{path} 里没有 cases 列表")

    cases: list[Case] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise CaseValidationError(f"第 {index + 1} 条用例不是对象")
        case_id = str(item.get("id", "")).strip()
        if not ID_RE.match(case_id):
            raise CaseValidationError(f"第 {index + 1} 条用例 id 非法：{case_id!r}（应为 F-01 / I-01 / C-01 / S-01）")
        turns = item.get("turns")
        if not isinstance(turns, list) or not turns or not all(isinstance(t, str) and t.strip() for t in turns):
            raise CaseValidationError(f"{case_id}: turns 必须是非空字符串列表")
        checks_raw = item.get("checks")
        if not isinstance(checks_raw, list) or not checks_raw:
            raise CaseValidationError(f"{case_id}: checks 不能为空")
        checks = [_build_check(case_id, check) for check in checks_raw]
        category = item.get("category")
        if category not in CATEGORIES:
            raise CaseValidationError(f"{case_id}: category 必须是 {CATEGORIES}")
        severity = item.get("severity", "P1")
        if severity not in SEVERITIES:
            raise CaseValidationError(f"{case_id}: severity 必须是 {SEVERITIES}")
        repeat = int(item.get("repeat", 1))
        if not 1 <= repeat <= 5:
            raise CaseValidationError(f"{case_id}: repeat 必须在 1-5 之间")
        rationale = str(item.get("rationale", "")).strip()
        if not rationale:
            raise CaseValidationError(f"{case_id}: 必须写 rationale（为什么设计这条用例）")
        cases.append(
            Case(
                id=case_id,
                category=category,
                severity=severity,
                title=str(item.get("title", "")).strip(),
                rationale=rationale,
                turns=turns,
                checks=checks,
                expected=str(item.get("expected", "")).strip(),
                source=item.get("source"),
                group=item.get("group"),
                group_weight=float(item.get("group_weight", 1.5)),
                repeat=repeat,
            )
        )
    validate_cases(cases)
    return cases


def validate_cases(cases: list[Case], kb_ids: set[str] | None = None) -> list[str]:
    """返回 warning 列表；发现致命问题时抛 CaseValidationError（一次列全）。"""
    errors: list[str] = []
    warnings: list[str] = []
    ids = [case.id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        errors.append(f"用例 id 重复：{duplicates}")

    for case in cases:
        if case.source:
            if not re.match(r"^[A-Z]+-\d+$", case.source):
                errors.append(f"{case.id}: source 格式应为 REFUND-1，收到 {case.source}")
            elif kb_ids is not None and case.source not in kb_ids:
                errors.append(f"{case.id}: source {case.source} 不在知识库中")
        if case.group and sum(1 for other in cases if other.group == case.group) < 2:
            warnings.append(f"{case.id}: group={case.group} 只有 1 条用例，一致性检查不会生效")

    for category in CATEGORIES:
        count = sum(1 for case in cases if case.category == category)
        if count < 5:
            warnings.append(f"分类 {category} 只有 {count} 条，覆盖可能不足")

    if not 20 <= len(cases) <= 30:
        warnings.append(f"当前 {len(cases)} 条用例，建议控制在 20-30 条")

    if errors:
        raise CaseValidationError("用例校验失败：\n- " + "\n- ".join(errors))
    return warnings
