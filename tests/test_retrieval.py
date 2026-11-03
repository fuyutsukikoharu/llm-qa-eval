from __future__ import annotations

import unittest
from pathlib import Path

from qaeval.retrieval import SYNONYMS, Retriever, load_kb, prepare_query

ROOT = Path(__file__).resolve().parents[1]
KB = load_kb(ROOT / "data" / "kb")


class KbTest(unittest.TestCase):
    def test_chunking(self):
        self.assertEqual(len(KB), 16)
        ids = {chunk.id for chunk in KB}
        self.assertIn("REFUND-1", ids)
        self.assertTrue(all(chunk.doc == chunk.id.split("-")[0] for chunk in KB))


class QueryCleanTest(unittest.TestCase):
    def test_instruction_suffix_is_removed(self):
        query = "现货商品多久发货？回答时请标注来源编号，例如 [SHIP-1]。"
        self.assertEqual(prepare_query(query), "现货商品多久发货？")

    def test_question_marker(self):
        query = "请只输出 JSON，不要任何多余文字。用户问题：我的快递三天没动了，怎么办？"
        self.assertEqual(prepare_query(query), "我的快递三天没动了，怎么办？")

    def test_bullet_instruction_is_removed(self):
        query = "退货要满足什么条件？请用不超过 3 条要点回答，每条不超过 25 个字。"
        self.assertEqual(prepare_query(query), "退货要满足什么条件？")


class SearchTest(unittest.TestCase):
    def test_hits_refund_timing(self):
        retriever = Retriever(KB)
        results = retriever.search("退款一般多久到账", top_k=3, min_score=3.0)
        self.assertEqual(results[0][0].id, "REFUND-2")

    def test_min_score_filters_everything(self):
        retriever = Retriever(KB)
        self.assertEqual(retriever.search("退款多久到账", top_k=3, min_score=999), [])

    def test_synonyms_improve_recall(self):
        query = "东西退回去的话，快递费谁出？"
        baseline = [chunk.id for chunk, _ in Retriever(KB).search(query, top_k=3, min_score=3.0)]
        hardened = [
            chunk.id
            for chunk, _ in Retriever(KB, SYNONYMS).search(query, top_k=3, min_score=3.0)
        ]
        self.assertNotIn("REFUND-3", baseline)
        self.assertIn("REFUND-3", hardened)


if __name__ == "__main__":
    unittest.main()
