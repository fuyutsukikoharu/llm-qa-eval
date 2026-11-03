"""pytest 不在依赖里，用标准库 unittest 就够：
    python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import unittest

from qaeval.text import (
    cjk_ratio,
    compact,
    extract_fact_tokens,
    extract_numbers,
    has_all,
    normalize,
    strip_citations,
    strip_code_fences,
    tokenize,
)


class NormalizeTest(unittest.TestCase):
    def test_fullwidth_and_space_are_ignored(self):
        self.assertEqual(normalize("７ 个自然日"), "7个自然日")
        self.assertIn("7个自然日", normalize("自签收次日起 7 个自然日"))

    def test_dash_variants(self):
        self.assertEqual(normalize("1–3 个工作日"), "1-3个工作日")

    def test_has_all_returns_missing(self):
        ok, missing = has_all("满 99 元包邮", ["99", "包邮", "运费"])
        self.assertFalse(ok)
        self.assertEqual(missing, ["运费"])

    def test_compact_keeps_case(self):
        self.assertEqual(compact("【结论】 有货"), "【结论】有货")


class TokenizeTest(unittest.TestCase):
    def test_chinese_bigrams_and_ascii_words(self):
        tokens = tokenize("退款到账 JSON")
        self.assertIn("退款", tokens)
        self.assertIn("款到", tokens)
        self.assertIn("到账", tokens)
        self.assertIn("json", tokens)


class NumberTest(unittest.TestCase):
    def test_citation_number_is_ignored(self):
        self.assertEqual(extract_numbers("1-3 个工作日 [REFUND-2]"), ["1", "3"])

    def test_fact_tokens_normalize_unit(self):
        self.assertEqual(extract_fact_tokens("1–3 个工作日，最长 7 个工作日"), ["1-3工作日", "7工作日"])
        self.assertEqual(extract_fact_tokens("12 元 [REFUND-3]"), ["12元"])

    def test_strip_citations(self):
        self.assertEqual(strip_citations("依据 [SHIP-1]").strip(), "依据")

    def test_cjk_ratio(self):
        self.assertGreater(cjk_ratio("你好呀 world"), 0.3)
        self.assertLess(cjk_ratio("hello world"), 0.1)


class CodeFenceTest(unittest.TestCase):
    def test_strip_fence(self):
        self.assertEqual(strip_code_fences('```json\n{"a": 1}\n```'), '{"a": 1}')


if __name__ == "__main__":
    unittest.main()
