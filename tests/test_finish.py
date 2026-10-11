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

    def test_there_is_no_serial_switch(self):
        """**不提供串行开关**（刻意）：串行曾是「数字口径」的第二个来源，
        而两个来源就意味着一处要区分二者——实测那处（`_actual_test_count`）连错三轮，
        每次都静默写错 README。取消第二来源 ⇒ 那类 bug 整类消失。

        具体判据：`--jobs 1` **不得**退回 `unittest discover`（旧行为），
        而应与其它 N 一样走 `test_changed.py` 并行（jobs=1 就是单进程）。
        """
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finish.py"), "--jobs", "1", "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("test_changed.py", proc.stdout,
                      "--jobs 1 也必须走 test_changed.py（串行开关已取消）")
        self.assertNotIn("-m unittest", proc.stdout,
                         "不得再有 direct discover 这条路（那是第二个数字来源）")

    def test_changed_mode_drops_all_flag(self):
        """`--changed`（迭代）不带 `--all` ⇒ 走子集；默认（收尾）带 `--all` ⇒ 全量。

        为何打这个钉子（实测需求）：本门全量 ~36s，其中 94% 是 3 个 worktree 测试
        （真建 git 仓 + 真建 worktree）。迭代期不该为一点改动付全量代价，
        但收尾**必须**全量（本仓纪律）—— 两个档不许互相渗透。
        """
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "finish.py"), "--changed", "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("test_changed.py", proc.stdout)
        self.assertNotIn("--all", proc.stdout, "--changed 必须走子集，不得带 --all")

    def test_default_mode_keeps_all_flag(self):
        """默认仍是全量——收尾口径不得因新增 `--changed` 而放宽。"""
        argv = self.finish.parallel_test_argv(0)
        self.assertIn("--all", argv)
        argv_changed = self.finish.parallel_test_argv(0, changed=True)
        self.assertNotIn("--all", argv_changed)

    def test_no_direct_discover_path_remains(self):
        """模块里不得再留 `test_argv`（它只服务串行 discover = 已取消的第二来源）。"""
        self.assertFalse(hasattr(self.finish, "test_argv"),
                         "test_argv 应随串行开关一起删除")

    def test_only_readme_count_failed_is_narrow(self):
        """自动重跑的触发判据必须**窄**：只有唯一失败项是数字门时才算。

        为何是硬判据（实测踩到）：`--sync-readme` 的同步发生在测试**之后**，
        所以「数字不对」那一次必然红——每加一个测试就要跑两轮门（实测白跑 4 轮 36s）。
        加自动重跑解决它，但**触发条件一旦放宽就会把真失败当噪音**：
        那是灾难性的（真 bug 被「重跑一次就好」掩盖）。
        """
        one_bad = ("FAIL: test_readme_test_count_matches_the_loaded_suite (x)\n"
                   "FAILED (failures=1)\n")
        self.assertTrue(self.finish._only_readme_count_failed(one_bad))

        # 关键反例：数字门 **+** 别的失败 ⇒ 不得重跑（真 bug 不能被掩盖）
        mixed = ("FAIL: test_readme_test_count_matches_the_loaded_suite (x)\n"
                 "FAIL: test_real_bug (y)\n")
        self.assertFalse(self.finish._only_readme_count_failed(mixed),
                         "还有真失败时不得重跑——那会把真 bug 当噪音")

        self.assertFalse(self.finish._only_readme_count_failed("FAIL: test_real_bug (y)\n"))
        self.assertFalse(self.finish._only_readme_count_failed("OK\n"), "全绿不需重跑")

    def test_actual_test_count_handles_both_real_layouts(self):
        """取数逻辑：**纯函数测**（不跑真测试，秒级）。

        为何在这里打钉子（实测踩了三轮，每次都是**静默写错文档**）：
        README 声明一个测试数，取数一错 `--sync-readme` 就把错数字写进去，
        且写错后**没人看**（不像报错会拦）。三轮错过的形态：
          ① 取第一个 `Ran` → 并行下取到**失败文件**的值（856 写成 26）。
          ② 先认 `[并行 …]` 摘要行 → 真实串行输出里**也有**一条（某测试自 spawn 的
             子进程输出被 unittest 捕进 stdout），把 858 写成 **25**。
          ③ 「恰好一条 `Ran` ⇒ 串行」 → 并行下恰好 1 个文件失败时也只有一条。
        而两条并行摘要行**文本格式完全一样**（只有数字不同） ⇒ 文本层面无法可靠区分。
        故改用 **`[total-cases=N]` 唯一标记**（由 runner 显式报出，不是从人读文本推的）。
        """
        # 并行：认显式标记（即使只剩一个失败文件的 `Ran` 也不影响）。
        parallel = (
            "===== FAILED: test_x.py =====\n"
            "Ran 26 tests in 14.0s\n"
            "FAILED (failures=1)\n\n"
            "[并行 jobs=10] 57 个测试文件 · 56 通过 · 1 失败 · 858 用例  [total-cases=858]\n"
        )
        self.assertEqual(self.finish._actual_test_count(parallel), 858,
                         "并行必须认标记，不能拿失败文件的 26")

        # 串行：无标记 ⇒ 回落到**唯一**那条 `Ran`（即使输出里混了子进程的并行字样）。
        serial = (
            "[并行 jobs=2] 1 个测试文件 · 1 通过 · 0 失败 · 25 用例\n"
            "....\nRan 858 tests in 115.0s\n\nOK\n"
        )
        self.assertEqual(self.finish._actual_test_count(serial), 858,
                         "串行要取全量的 `Ran 858`，不是子进程产物的 25")

        # 多条无标记的 `Ran` ⇒ 拿不准 ⇒ None（不许猜：猜错会静默写错文档）。
        ambiguous = "Ran 26 tests in 14s\nRan 830 tests in 70s\n"
        self.assertIsNone(self.finish._actual_test_count(ambiguous))
        self.assertIsNone(self.finish._actual_test_count("没有任何数字\n"))

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
