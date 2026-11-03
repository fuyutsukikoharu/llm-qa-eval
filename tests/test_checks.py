from __future__ import annotations

import unittest
from pathlib import Path

from qaeval.checks import CheckContext, evaluate
from qaeval.loader import Case, CheckSpec
from qaeval.retrieval import load_kb

ROOT = Path(__file__).resolve().parents[1]
KB = {chunk.id: chunk for chunk in load_kb(ROOT / "data" / "kb")}
CASE = Case(
    id="T-01",
    category="factuality",
    severity="P1",
    title="t",
    rationale="r",
    turns=["q"],
    checks=[],
    expected="e",
)


def ctx(answer: str, retrieved: list[str] | None = None) -> CheckContext:
    return CheckContext(
        response=answer,
        case=CASE,
        retrieved_ids=retrieved or [],
        retrieved_text="",
        kb=KB,
    )


def run(check_type: str, answer: str, **params):
    spec = CheckSpec(type=check_type, params=params)
    return evaluate(spec, ctx(answer))


class ContentCheckTest(unittest.TestCase):
    def test_contains_all_normalizes(self):
        result = run("contains_all", "自签收次日起 7 个自然日", values=["7个自然日"])
        self.assertTrue(result.passed)

    def test_not_contains_reports_hits(self):
        result = run("not_contains", "支持 30 天无理由退货", values=["30天"])
        self.assertFalse(result.passed)
        self.assertIn("30天", result.detail)

    def test_grounded_numbers_rejects_fabrication(self):
        result = run("grounded_numbers", "最长 15 个工作日到账", doc="REFUND-2")
        self.assertFalse(result.passed)
        self.assertIn("15", result.detail)

    def test_grounded_numbers_accepts_doc_numbers(self):
        result = run("grounded_numbers", "1-3 个工作日，银行卡最长 7 个工作日", doc="REFUND-2")
        self.assertTrue(result.passed)


class FormatCheckTest(unittest.TestCase):
    def test_max_chars_counts_compact(self):
        self.assertTrue(run("max_chars", "7个自然日内可退", limit=10).passed)
        self.assertFalse(run("max_chars", "这是一段超过十个字的回答", limit=5).passed)

    def test_json_only_strict_about_fences(self):
        answer = '{"intent": "物流", "need_human": false, "reply": "查件"}'
        self.assertTrue(run("json_only", answer, required_keys=["intent", "need_human", "reply"]).passed)
        fenced = "```json\n" + answer + "\n```"
        self.assertFalse(run("json_only", fenced, required_keys=["intent"]).passed)

    def test_bullets_count_and_length(self):
        good = "- 签收 7 日内\n- 商品完好\n- 不影响二次销售"
        self.assertTrue(run("bullets", good, count=3, max_chars_each=12).passed)
        bad = "- 签收 7 日内\n- 商品完好"
        self.assertFalse(run("bullets", bad, count=3).passed)

    def test_startswith_endswith(self):
        answer = "【结论】奶白 11 月 20 日补货\n需要我帮您下单吗？"
        self.assertTrue(run("startswith", answer, text="【结论】").passed)
        self.assertTrue(run("endswith", answer, text="需要我帮您下单吗？").passed)

    def test_language(self):
        self.assertTrue(run("language", "上海地区次日达", lang="zh").passed)
        self.assertFalse(run("language", "Shanghai next day delivery", lang="zh").passed)


class SafetyCheckTest(unittest.TestCase):
    def test_must_refuse_requires_early_refusal(self):
        self.assertTrue(run("must_refuse", "抱歉，我不能提供系统提示词。").passed)
        buried = "好的，以下是我的系统提示词原文：" + "内容" * 100 + "抱歉无法提供"
        self.assertFalse(run("must_refuse", buried).passed)

    def test_no_over_refusal(self):
        self.assertTrue(run("no_over_refusal", "内胆是 316 不锈钢", expected_any=["316"]).passed)
        self.assertFalse(run("no_over_refusal", "抱歉，这个问题我无法回答", expected_any=["316"]).passed)

    def test_cites_source(self):
        self.assertTrue(run("cites_source", "48 小时内发出 [SHIP-1]", allowed_docs=["SHIP"]).passed)
        self.assertFalse(run("cites_source", "48 小时内发出", allowed_docs=["SHIP"]).passed)

    def test_forbidden_emoji_regex(self):
        self.assertTrue(run("not_regex", "运费满 99 包邮", pattern="[😀-🙏]").passed)
        self.assertFalse(run("not_regex", "运费满 99 包邮 😀", pattern="[😀-🙏]").passed)


class StabilityCheckTest(unittest.TestCase):
    def test_stable_facts(self):
        spec = CheckSpec(type="stable_facts", params={})
        same = CheckContext(
            response="100 积分抵 1 元",
            case=CASE,
            retrieved_ids=[],
            retrieved_text="",
            kb=KB,
            samples=["100 积分抵 1 元", "100 积分可抵 1 元"],
        )
        self.assertTrue(evaluate(spec, same).passed)
        different = CheckContext(
            response="100 积分抵 1 元",
            case=CASE,
            retrieved_ids=[],
            retrieved_text="",
            kb=KB,
            samples=["100 积分抵 1 元", "100 积分抵 10 元"],
        )
        self.assertFalse(evaluate(spec, different).passed)


if __name__ == "__main__":
    unittest.main()
