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


if __name__ == "__main__":
    unittest.main()
