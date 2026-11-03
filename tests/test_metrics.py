from __future__ import annotations

import unittest

from qaeval.metrics import category_label, compare_summaries, percentile


class PercentileTest(unittest.TestCase):
    def test_nearest_rank(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        self.assertEqual(percentile(values, 0.5), 50)
        self.assertEqual(percentile(values, 0.95), 100)
        self.assertEqual(percentile([], 0.95), 0.0)


class CompareTest(unittest.TestCase):
    def test_delta_rows(self):
        baseline = {
            "pass_rate": 0.4,
            "avg_score": 0.5,
            "p0": {"pass_rate": 0.2},
            "retrieval": {"recall_at_k": 0.6},
            "by_category": {"safety": {"pass_rate": 0.3}},
        }
        current = {
            "pass_rate": 0.8,
            "avg_score": 0.85,
            "p0": {"pass_rate": 1.0},
            "retrieval": {"recall_at_k": 0.9},
            "by_category": {"safety": {"pass_rate": 1.0}},
        }
        rows = {row["metric"]: row for row in compare_summaries(baseline, current)}
        self.assertAlmostEqual(rows["总体通过率"]["delta"], 0.4)
        self.assertAlmostEqual(rows["P0 通过率"]["delta"], 0.8)
        self.assertAlmostEqual(rows["分类 · safety"]["delta"], 0.7)


class LabelTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(category_label("instruction_following"), "指令遵循")
        self.assertEqual(category_label("unknown"), "unknown")


if __name__ == "__main__":
    unittest.main()
