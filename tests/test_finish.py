"""收尾门 `scripts/finish.py` 的守卫：命令组合与摘要解析要**可测且不跑全量**。

只钉**纯逻辑**（argv 组合 / 摘要解析 / `--dry-run`），不真跑收尾门——
真跑会拉起 76s 全量套件，塞进 `unittest discover` 是自套娃。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_finish", ROOT / "scripts" / "finish.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != __main__` ⇒ 不跑 main
    return module


class FinishGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.finish = _load()

    def test_dry_run_default_is_parallel_and_lists_both_steps(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finish.py"), "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for token in ("test_changed.py", "--all", "--jobs 0", "assets_scan.py check"):
            self.assertIn(token, proc.stdout)
        self.assertNotIn("assets_scan.py scan", proc.stdout,
                         "收尾门不得写盘（scan 会重写 index，提交后再跑必留 diff）")

    def test_dry_run_jobs_1_falls_back_to_serial_discover(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finish.py"), "--jobs", "1", "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for token in ("unittest", "discover", "assets_scan.py check"):
            self.assertIn(token, proc.stdout)

    def test_test_argv_matches_the_readme_command(self):
        argv = self.finish.test_argv()
        self.assertEqual(argv[1:], ["-m", "unittest", "discover", "-s", "tests", "-t", "tests"])

    def test_parallel_argv_delegates_to_test_changed(self):
        argv = self.finish.parallel_test_argv(8)
        self.assertEqual(argv[1:], ["scripts/test_changed.py", "--all", "--jobs", "8"])

    def test_dry_run_jobs_switches_to_parallel_command(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finish.py"), "--jobs", "8", "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("test_changed.py --all --jobs 8", proc.stdout)
        self.assertIn("assets_scan.py check", proc.stdout)

    def test_assets_argv_rejects_unknown_action(self):
        with self.assertRaises(ValueError):
            self.finish.assets_argv("rm-rf")

    def test_stale_lines_extracts_entries_and_summary(self):
        out = "⚠ 2 条 stale（依据物已变）：\n  - dev-board ← requirements, pkg, tests\n  - lessons ← pkg\n"
        self.assertEqual(self.finish._stale_lines(out),
                         ["⚠ 2 条 stale（依据物已变）：",
                          "  - dev-board ← requirements, pkg, tests",
                          "  - lessons ← pkg"])

    def test_tests_summary_reads_count_and_verdict(self):
        out = "....\nRan 758 tests in 72.1s\n\nOK\n"
        self.assertEqual(self.finish._tests_summary(out), "OK（758 tests）")
        bad = "F.\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n"
        self.assertEqual(self.finish._tests_summary(bad), "FAILED（3 tests）")

    def test_tests_summary_reads_parallel_summary(self):
        ok = "[并行 jobs=8] 51 个测试文件 · 51 通过 · 0 失败\n"
        self.assertEqual(self.finish._tests_summary(ok), "OK（51 文件 · 51 通过 · 0 失败）")
        bad = "[并行 jobs=8] 51 个测试文件 · 50 通过 · 1 失败\n"
        self.assertEqual(self.finish._tests_summary(bad), "FAILED（51 文件 · 50 通过 · 1 失败）")


class FailureExcerptTest(unittest.TestCase):
    """失败摘要必须带**详情**（不只是名字）—— 实测踩到：只说 `FAIL: <名字>`
    时读者得重跑一遍全量（90–103s）才能定位红在哪。判据：摘要里能看到 traceback 与断言行。
    """

    def setUp(self) -> None:
        self.finish = _load()

    def test_excerpt_carries_traceback_not_just_the_name(self):
        out = (
            "....F\n"
            "======================================================================\n"
            "FAIL: test_x (mod.Case.test_x)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            '  File "/r/tests/t.py", line 42, in test_x\n'
            "    self.assertEqual(1, 2)\n"
            "AssertionError: 1 != 2 : 不同步\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 830 tests in 103.4s\n\nFAILED (failures=1)\n"
        )
        text = "\n".join(self.finish._failure_excerpt(out))
        self.assertIn("FAIL: test_x", text)
        self.assertIn("AssertionError: 1 != 2", text)
        self.assertIn("line 42", text)

    def test_excerpt_handles_multiple_failures(self):
        out = (
            "FAIL: test_a (m.A)\n"
            "AssertionError: a\n"
            "======================================================================\n"
            "ERROR: test_b (m.B)\n"
            "ValueError: b\n"
            "\n----------------------------------------------------------------------\n"
            "Ran 2 tests\n\nFAILED\n"
        )
        text = "\n".join(self.finish._failure_excerpt(out))
        self.assertIn("test_a", text)
        self.assertIn("test_b", text)

    def test_excerpt_is_capped_and_says_so(self):
        """刷屏保护：超上限要**说明截断**（而不是静默丢），全量在日志里。"""
        out = "FAIL: test_x (m.X)\n" + "\n".join(f"行{i}" for i in range(200)) + "\n"
        lines = self.finish._failure_excerpt(out)
        self.assertLessEqual(len(lines), self.finish._FAILURE_HEAD + 1)
        self.assertIn("截断", lines[-1])

    def test_clean_output_yields_nothing(self):
        self.assertEqual(
            self.finish._failure_excerpt("....\nRan 4 tests\n\nOK\n"), [])


if __name__ == "__main__":
    unittest.main()
