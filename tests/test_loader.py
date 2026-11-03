from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qaeval.loader import CaseValidationError, load_cases, validate_cases
from qaeval.retrieval import load_kb

ROOT = Path(__file__).resolve().parents[1]
MINIMAL = """cases:
  - id: F-01
    category: factuality
    severity: P1
    title: 测试
    rationale: 因为要测
    turns: ["退款几天到账？"]
    checks:
      - {type: contains_all, values: ["7"], weight: 1}
    expected: 7 天
"""


def load_from_text(text: str):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        path = handle.name
    try:
        return load_cases(path)
    finally:
        Path(path).unlink(missing_ok=True)


class RealCaseSetTest(unittest.TestCase):
    def test_eight_twenty_eight_cases_are_valid(self):
        cases = load_cases(ROOT / "data" / "cases.yaml")
        self.assertEqual(len(cases), 28)
        self.assertEqual(len({case.id for case in cases}), 28)
        counts = {}
        for case in cases:
            counts[case.category] = counts.get(case.category, 0) + 1
        self.assertEqual(counts["factuality"], 7)
        self.assertEqual(counts["instruction_following"], 8)
        self.assertEqual(counts["consistency"], 6)
        self.assertEqual(counts["safety"], 7)

    def test_sources_exist_in_kb(self):
        kb_ids = {chunk.id for chunk in load_kb(ROOT / "data" / "kb")}
        cases = load_cases(ROOT / "data" / "cases.yaml")
        validate_cases(cases, kb_ids)  # 不抛异常即通过
        self.assertTrue(all(case.source in kb_ids for case in cases if case.source))


class CaseValidationTest(unittest.TestCase):
    def test_unknown_check_type_is_rejected(self):
        with self.assertRaises(CaseValidationError):
            load_from_text(MINIMAL.replace("contains_all", "contains_everything"))

    def test_unknown_param_is_rejected(self):
        with self.assertRaises(CaseValidationError):
            load_from_text(MINIMAL.replace('values: ["7"]', 'value: ["7"]'))

    def test_missing_rationale_is_rejected(self):
        with self.assertRaises(CaseValidationError):
            load_from_text(MINIMAL.replace("    rationale: 因为要测\n", ""))

    def test_duplicate_id_is_rejected(self):
        duplicate = """  - id: F-01
    category: safety
    severity: P1
    title: 重复 id
    rationale: 重复
    turns: ["q"]
    checks:
      - {type: must_refuse, weight: 1}
    expected: e
"""
        with self.assertRaises(CaseValidationError):
            load_from_text(MINIMAL + duplicate)

    def test_bad_source_is_rejected_with_kb(self):
        cases = load_cases(ROOT / "data" / "cases.yaml")
        cases[0].source = "REFUND-99"
        with self.assertRaises(CaseValidationError):
            validate_cases(cases, {chunk.id for chunk in load_kb(ROOT / "data" / "kb")})

    def test_judge_rubric_is_validated(self):
        bad = MINIMAL.replace(
            'contains_all, values: ["7"], weight: 1',
            'llm_judge, rubric: not_exist, min_score: 3',
        )
        with self.assertRaises(CaseValidationError):
            load_from_text(bad)


if __name__ == "__main__":
    unittest.main()
