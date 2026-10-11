"""`mutation_check.py --jobs` 并发：**汇总与退出码必须与串行一致**，且每条变异跑在独立副本上。

**为什么这条值得测**：`--jobs N>1` 把每条变异丢进线程池并发跑。若各条变异都注入到**真工作树**
同一份源码上，A 注入的改动会被 B 正在跑的测试读到——结果不可信，而且进程被杀时变异会残留
（`_Restorer` 拦得住正常退出与 SIGTERM，拦不住彼此踩）。所以并发的正确性建立在
「每条变异一份独立仓副本」这个前提上；本文件把这个前提与「并发不改变结论」一起钉死。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import shutil
import tempfile
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 有牙的针脚：注入后 `test_metrics.py` 必红（与 test_mutation_harness 同一处行为）。
_GOOD = ("良好针脚", "disflow/metrics.py",
         "    return ts <= cutoff_ts and _day(ts) <= cutoff_day",
         "    return ts > cutoff_ts and _day(ts) <= cutoff_day",
         "test_metrics.py")

# 无牙的针脚：改了报错文案但没被断言守住 ⇒ 必报「漏抓」（exit 1）。
_TOOTHLESS = ("无牙针脚", "disflow/metrics.py",
              '''        return (f"未知 slug：{slug!r}；该事件流里出现过的 slug："
                f"{', '.join(sorted(seen))}")''',
              '''        return (f"zzz slug={slug!r} seen={', '.join(sorted(seen))}")''',
              "test_metrics.py")


def _load_harness_from(repo: pathlib.Path) -> Any:
    """从**仓副本**加载 harness（其 `ROOT` = 副本）——注入落在副本上，不碰真工作树。"""
    path = repo / "scripts" / "mutation_check.py"
    spec = importlib.util.spec_from_file_location("mutation_check_parallel", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo_copy() -> pathlib.Path:
    """仓的临时副本（harness 需要的最小集合：三个目录 + 仓根 `README.md`）。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="disflow-mc-par-test-"))
    for name in ("disflow", "tests", "scripts"):
        shutil.copytree(ROOT / name, tmp / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ROOT / "README.md", tmp / "README.md")
    return tmp


def _report_lines(text: str) -> list[str]:
    """逐条结果行（两条空格缩进），**排序后**比较——并发下输出顺序可变，集合必须一致。"""
    return sorted(line for line in text.splitlines() if line.startswith("  "))


class ParallelMatchesSerialTest(unittest.TestCase):
    """同一组变异：`--jobs 1` 与 `--jobs 2` 的**汇总数字与退出码**必须一致。"""

    def setUp(self) -> None:
        repo = _repo_copy()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        self.iso = _load_harness_from(repo)
        self.iso.MUTATIONS = [_GOOD, _TOOTHLESS]

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.iso.main(argv)
        return code, buffer.getvalue()

    def test_parallel_summary_and_exit_code_match_serial(self) -> None:
        serial_code, serial_text = self._run([])
        par_code, par_text = self._run(["--jobs", "2"])

        # 一条有牙 + 一条无牙 ⇒ 串行必然报「漏抓」（1），并行不得因顺序而变
        self.assertEqual(serial_code, 1)
        self.assertEqual(par_code, serial_code, "并发的退出码必须与串行一致")

        # 汇总行逐字一致
        self.assertEqual(
            [ln for ln in par_text.splitlines() if ln.startswith("捕获率")],
            [ln for ln in serial_text.splitlines() if ln.startswith("捕获率")])
        # 每条结果行（排序后）集合一致：无牙那条必须仍被点名为漏抓
        self.assertEqual(_report_lines(par_text), _report_lines(serial_text))
        self.assertIn("漏抓", par_text)

    def test_parallel_reports_an_unapplied_pin_as_harness_error(self) -> None:
        """并发下坏针脚仍走 exit 2 通道，且不得被说成“漏抓”。"""
        self.iso.MUTATIONS = [
            _GOOD,
            ("过期针脚", "disflow/metrics.py", "这段文本根本不存在", "x", "test_metrics.py"),
        ]
        code, text = self._run(["--jobs", "2"])
        self.assertEqual(code, 2, "针脚未应用 = harness 的错（2），并发下也一样")
        self.assertIn("未应用", text)
        self.assertNotIn("漏抓（这些行为没有被测试守住）", text)


class IsolationTest(unittest.TestCase):
    """并发的安全前提：每条变异跑在**自己的**仓副本上。"""

    def test_repo_copy_is_a_fresh_independent_tree(self) -> None:
        """两次 `_repo_copy()` 得到互不相同的目录，且都含注入与跑测试所需的文件。"""
        first = self.iso_copy()
        second = self.iso_copy()
        self.assertNotEqual(first, second, "每次复制必须是独立目录（并发各写各的）")
        for repo in (first, second):
            for rel in ("disflow/metrics.py", "tests/test_metrics.py",
                        "scripts/mutation_check.py", "README.md"):
                self.assertTrue((repo / rel).is_file(), f"副本缺 {rel}")

    def iso_copy(self) -> pathlib.Path:
        repo = _repo_copy()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        module = _load_harness_from(repo)
        copy = module._repo_copy()
        self.addCleanup(shutil.rmtree, copy.parent, ignore_errors=True)
        return copy

    def test_parallel_never_touches_the_real_worktree(self) -> None:
        """真模块（`ROOT` = 真仓）跑 `--jobs 2`：真工作树的源码必须**逐字节不变**。

        这正是「注入落在副本」的可观测后果——并发跑完，真源码不该有任何残留变异。
        """
        module = _load_harness_from(ROOT)          # ROOT = 真仓
        module.MUTATIONS = [_GOOD]
        target = ROOT / "disflow" / "metrics.py"
        before = target.read_bytes()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = module.main(["--jobs", "2"])
        self.assertEqual(code, 0, "有牙针脚在副本上跑：注入红、还原绿 ⇒ 抓到（exit 0）")
        self.assertEqual(target.read_bytes(), before,
                         "并发跑完真工作树被改动了——注入没落在副本上")


if __name__ == "__main__":
    unittest.main()
