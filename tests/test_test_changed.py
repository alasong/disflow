"""`scripts/test_changed.py` 的守卫：**映射必须覆盖引用面**，且子集模式**绝不假装全量**。

为什么要有这个文件：`test_changed.py` 把迭代期的测试面缩成子集，一旦映射写漏，
「绿」就退化成「我恰好没跑那条」。故本文件用**注入式假改动**（不依赖工作区真实脏文件）
断言两件事：

  正向：若模块 X 被改，**引用 X 的每个测试文件**都必须在子集里（命名约定 + grep 引用面）；
  反向：与 X 无关的测试文件**不进**子集（固定元测试除外）——否则子集等于全量，优化失效。

判据全部是**显式可读**的：命名约定 `tests/test_<stem>.py`、grep `disflow.<stem>` 形状、
固定元测试表 `META_TESTS`。允许的边界（诚实标注）：只能看**文本引用**；靠子进程跑 CLI
而不 import 的测试（如 `test_cli.py` 跑 `board`）不在 grep 命中内——它们会出现在
「未覆盖」醒目清单里，由横幅保证人不会把它读成全量。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "test_changed.py"
TESTS_DIR = ROOT / "tests"


def _load_script():
    """按路径装载被测脚本（`__name__ != __main__` ⇒ 不会执行 main）。"""
    spec = importlib.util.spec_from_file_location("_d1c_test_changed", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT_MODULE = _load_script()

# 独立于被测脚本的 grep 尺子：哪些测试文件引用了 `disflow.<stem>`。
_REF_PATTERNS = (
    r"\bdisflow\.{stem}\b",
    r"\bfrom disflow import\b[^\n]*\b{stem}\b",
)


def _referencing_tests(stem: str) -> set[str]:
    """独立尺子：文本里引用 `disflow.<stem>` 的测试文件（不调用被测脚本的匹配器）。"""
    patterns = [p.format(stem=re.escape(stem)) for p in _REF_PATTERNS]
    out: set[str] = set()
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        if any(re.search(p, text) for p in patterns):
            out.add(path.name)
    return out


class MappingGuardTest(unittest.TestCase):
    """映射判据的正/反向断言（注入式假改动）。"""

    def _selected(self, changed: list[str]) -> set[str]:
        return set(SCRIPT_MODULE.map_changed_to_tests(changed)["selected"])

    def test_every_referencing_test_is_selected(self):
        """**正向**：引用被改模块的每个测试文件都必须进子集（覆盖面不许漏）。"""
        for stem in ("views", "metrics", "gates"):
            with self.subTest(stem=stem):
                references = _referencing_tests(stem)
                self.assertTrue(
                    references,
                    f"独立尺子没抓到任何引用 {stem} 的测试文件（守卫形同虚设）")
                selected = self._selected([f"disflow/{stem}.py"])
                missing = sorted(references - selected)
                self.assertEqual(
                    missing, [],
                    f"改了 disflow/{stem}.py，但引用它的测试没进子集：{missing}")
                # 命名约定那半也要在（即使没有文本引用）
                self.assertIn(f"test_{stem}.py", selected,
                              f"命名约定 tests/test_{stem}.py 未被选入")

    def test_unrelated_tests_do_not_enter_the_subset(self):
        """**反向**：与改动模块无关的测试不进子集（除固定元测试）——否则优化失效。"""
        selected = self._selected(["disflow/metrics.py"])
        expected = (set(SCRIPT_MODULE.META_TESTS)
                    | {"test_metrics.py"}
                    | _referencing_tests("metrics"))
        self.assertEqual(
            selected, expected,
            "子集里混进了与 disflow/metrics.py 无关的测试："
            f"{sorted(selected - expected)}")
        self.assertNotIn("test_worktree.py", selected,
                         "与 metrics 无关的 test_worktree.py 不该进子集")

    def test_uncovered_is_the_exact_complement_of_selected(self):
        """覆盖读数必须自洽：`selected` ∪ `uncovered` == 全库，且不相交。"""
        plan = SCRIPT_MODULE.map_changed_to_tests(["disflow/views.py"])
        selected = set(plan["selected"])
        uncovered = set(plan["uncovered"])
        everything = set(SCRIPT_MODULE.all_test_files())
        self.assertEqual(selected & uncovered, set(), "selected 与 uncovered 不该相交")
        self.assertEqual(selected | uncovered, everything,
                         "selected ∪ uncovered 必须恰好是全库测试文件")
        self.assertTrue(uncovered, "改动一个模块后不该是零未覆盖（除非改动等于全仓）")

    def test_document_change_selects_the_document_guards(self):
        """改文档必须并上文档守卫（README 代码块会被真跑）。"""
        selected = self._selected(["README.md"])
        self.assertIn("test_docs.py", selected)
        self.assertIn("test_readme_walkthrough.py", selected)

    def test_unmappable_change_falls_back_to_full_not_silent_skip(self):
        """**绝不静默跳过**：无法映射的改动 ⇒ 保守改跑全量，并显式列出该路径。"""
        plan = SCRIPT_MODULE.map_changed_to_tests(["LICENSE"])
        self.assertTrue(plan["fallback_full"], "无法映射的改动必须触发跑全量")
        self.assertEqual(plan["unmapped"], ["LICENSE"])
        self.assertEqual(plan["selected"], plan["all"],
                         "回退全量时 selected 必须是全库")

    def test_shared_test_infrastructure_selects_everything(self):
        """`tests/base.py` 是共享基建 ⇒ 全体测试都可能在用，必须跑全量。"""
        plan = SCRIPT_MODULE.map_changed_to_tests(["tests/base.py"])
        self.assertEqual(plan["selected"], plan["all"])


class AllModeEquivalenceTest(unittest.TestCase):
    """`--all` 必须与既有全量命令**逐字等价**（命令相同 ⇒ 用例数相同）。"""

    def test_built_all_command_equals_the_full_discover_command(self):
        canonical = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"]
        self.assertEqual(SCRIPT_MODULE.build_command([], run_all=True), canonical)
        self.assertEqual(SCRIPT_MODULE.FULL_COMMAND, canonical)

    def test_all_dry_run_prints_the_full_discover_command(self):
        """端到端：`--all --dry-run` 打印的命令就是既有全量命令（README 那条仍可用）。"""
        proc = subprocess.run(
            [sys.executable, "scripts/test_changed.py", "--all", "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("-m unittest discover -s tests -t tests", proc.stdout)
        self.assertIn("全量", proc.stdout)


class SubsetBannerTest(unittest.TestCase):
    """子集模式必须**醒目**声明它不是全量，并给出覆盖读数与收尾提示。"""

    def _run_dry(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "scripts/test_changed.py", *args, "--dry-run"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_subset_banner_is_loud_and_lists_uncovered(self):
        proc = self._run_dry("--changed", "disflow/views.py")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("不是全量", proc.stdout, "子集必须打印「不是全量」横幅")
        self.assertIn("未覆盖", proc.stdout, "子集必须打印未覆盖清单")
        self.assertIn("覆盖", proc.stdout)
        self.assertIn("收尾必须跑全量", proc.stdout, "结尾必须提示收尾跑全量")
        self.assertIn("-m unittest discover -s tests -t tests", proc.stdout)

    def test_unmappable_change_is_surfaced_and_falls_back(self):
        proc = self._run_dry("--changed", "LICENSE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("LICENSE", proc.stdout)
        self.assertIn("保守改跑全量", proc.stdout)

    def test_empty_change_set_falls_back_to_full(self):
        """空改动集（例如已 git commit 却没给 --base）⇒ 跑全量，不假绿。"""
        proc = self._run_dry("--changed", "")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout + proc.stderr
        self.assertIn("改跑全量", out)

    def test_json_plan_is_machine_readable(self):
        proc = self._run_dry("--changed", "disflow/views.py", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = json.loads(proc.stdout)
        self.assertEqual(plan["mode"], "subset")
        self.assertIn("test_views.py", plan["selected"])
        self.assertIn("test_docs.py", plan["selected"])


if __name__ == "__main__":
    unittest.main()
