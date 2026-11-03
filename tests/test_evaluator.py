from __future__ import annotations

import contextlib
import io
import json
import shutil
import unittest
from pathlib import Path

from qaeval.checks import CheckResult
from qaeval.config import load_config
from qaeval.evaluator import Evaluator, score_case
from qaeval.judges import HeuristicJudge
from qaeval.loader import load_cases
from qaeval.metrics import summarize
from qaeval.models import MockChatModel
from qaeval.report import write_reports
from qaeval.retrieval import Retriever, load_kb

ROOT = Path(__file__).resolve().parents[1]


class ScoreTest(unittest.TestCase):
    def test_weighted_score(self):
        checks = [
            CheckResult("contains_all", True, 1.0, 2.0, ""),
            CheckResult("not_contains", False, 0.0, 1.0, ""),
        ]
        score, passed = score_case(checks, threshold=0.8)
        self.assertAlmostEqual(score, 0.6667, places=3)
        self.assertFalse(passed)

    def test_hard_check_vetoes_average(self):
        checks = [
            CheckResult("contains_all", True, 1.0, 2.0, ""),
            CheckResult("not_contains", False, 0.0, 1.0, "", hard=True),
        ]
        score, passed = score_case(checks, threshold=0.5)
        self.assertGreater(score, 0.5)
        self.assertFalse(passed)


class EndToEndTest(unittest.TestCase):
    def test_mock_run_and_reports(self):
        cfg = load_config(ROOT / "configs" / "baseline.yaml")
        all_cases = load_cases(cfg.cases_path)
        wanted = {"F-04", "C-01", "C-02", "C-03", "S-01"}
        cases = [case for case in all_cases if case.id in wanted]
        retriever = Retriever(load_kb(cfg.kb_dir))
        with contextlib.redirect_stdout(io.StringIO()):  # 评测器会打印进度，测试里静音
            run = Evaluator(cfg, cases, retriever, MockChatModel(), HeuristicJudge()).run()
        run.warnings = []
        by_id = {result.case.id: result for result in run.case_results}

        self.assertTrue(by_id["F-04"].passed, "杯 Pro 参数应通过")
        self.assertFalse(by_id["S-01"].passed, "越狱应失败（mock 会泄露哨兵）")
        group_checks = [c for c in by_id["C-01"].checks if c.check_type == "group_consistency"]
        self.assertEqual(len(group_checks), 1)
        self.assertFalse(group_checks[0].passed, "C-02 未命中会导致组内不一致")

        summary = summarize(run)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["by_severity"]["P0"]["total"], 1)

        with tempfile_dir() as tmp:
            paths = write_reports(run, summary, tmp)
            self.assertTrue(paths["report"].exists())
            self.assertTrue(paths["html"].exists())
            self.assertTrue(paths["bad_cases"].exists())
            text = paths["report"].read_text(encoding="utf-8")
            self.assertIn("Bad case 明细", text)
            self.assertIn("评测口径与局限", text)
            self.assertIn("mock", text)
            records = [
                json.loads(line)
                for line in paths["results"].read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 5)
            self.assertIn("checks", records[0])


class tempfile_dir:
    """在 workspace 内建临时目录，避免 Windows 沙箱对系统 temp 的权限限制。"""

    def __enter__(self) -> Path:
        self.path = ROOT / ".test_tmp"
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, *exc_info) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
