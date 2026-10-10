"""门测试：`size_budget`（规模预算）、`diff_size`（改动规模读数）与 `tree_closure`（树终止条件）。"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest

from base import DisflowTestCase, acceptance, git_repo

from disflow import gates
from disflow.engine import Engine, GateReject


def write_lines(path: str, count: int) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x = 1\n" * count)


class SizeBudgetTest(DisflowTestCase):
    def test_over_budget_rejected_then_waiver_passes(self):
        engine, repo = git_repo(self, {})
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "touch_plan": ["big.py"],
                                     "context": {"size_budget": 10},
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        write_lines(os.path.join(repo, "big.py"), 50)
        with self.assertRaises(GateReject) as ctx:
            engine.done("t", "n")
        self.assertTrue(any("规模超预算" in m for m in ctx.exception.missing))
        engine.done("t", "n", {"size_waiver": "确实需要 50 行"})
        node = engine.load()["trees"]["t"]["nodes"]["n"]
        self.assertEqual(node["status"], "done")
        self.assertEqual(node["evidence"]["size"]["total"], 50)

    def test_within_budget_passes(self):
        engine, repo = git_repo(self, {})
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "touch_plan": ["small.py"],
                                     "context": {"size_budget": 100},
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        write_lines(os.path.join(repo, "small.py"), 3)
        engine.done("t", "n")
        node = engine.load()["trees"]["t"]["nodes"]["n"]
        self.assertEqual(node["status"], "done")
        self.assertEqual(node["evidence"]["size"]["total"], 3)


class TreeClosureTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def _add_done(self, node_id: str, entry: str) -> None:
        self.engine.expand("t", "root", [{"id": node_id, "goal": "x", "entry": entry,
                                          "acceptance": acceptance()}])
        self.engine.start("t", node_id, "w")
        self.engine.done("t", node_id)

    def test_close_rejects_without_eval_and_sediment(self):
        engine = self.engine
        engine.init_tree("t", "g")
        self._add_done("a", "implement")
        with self.assertRaises(GateReject) as ctx:
            engine.close("t")
        self.assertTrue(any("收益验证" in m for m in ctx.exception.missing))
        self.assertTrue(any("经验沉淀" in m for m in ctx.exception.missing))

    def test_close_passes_with_eval_and_sediment(self):
        engine = self.engine
        engine.init_tree("t", "g")
        self._add_done("ev", "evaluate")
        self._add_done("rf", "reflect")
        engine.close("t")
        self.assertTrue(engine.load()["trees"]["t"]["closed"])

    def test_close_not_applicable_with_reason(self):
        engine = self.engine
        engine.init_tree("t", "g")
        engine.annotate("t", "root", {"closure": "not_applicable: 纯文档改动，无收益可验证"})
        engine.close("t")
        self.assertTrue(engine.load()["trees"]["t"]["closed"])

    def test_close_not_applicable_empty_reason_rejected(self):
        engine = self.engine
        engine.init_tree("t", "g")
        engine.annotate("t", "root", {"closure": "not_applicable:"})
        with self.assertRaises(GateReject):
            engine.close("t")


class DiffSizeTest(DisflowTestCase):
    """`diff_size` 的读数分支。此前**只**被未跟踪新文件走到「数整文件行数」的兜底分支，
    已跟踪文件的 `git diff --numstat` 分支从未被直达测试（真缺口 B3）。

    补测时查出真缺陷：已跟踪文件把改动 `git add` 后，`git diff --numstat` 返回**空**
    （它比的是「工作区 vs 暂存区」），于是落到兜底分支 ⇒ **按整文件计**：
    一个 200 行文件改 1 行、暂存后被判 200 行，规模门会误报超预算。
    """

    def _committed(self, body: str):
        return git_repo(self, {"f.py": body}, commit=True)

    @staticmethod
    def _total(repo: str, paths: list[str]) -> int:
        """取读数（并把 `dict | None` 收窄成 dict）。"""
        size = gates.diff_size(repo, paths)
        assert size is not None, "git 仓里不该读到 None"
        return size["total"]

    def test_tracked_change_is_measured_as_a_diff_not_the_whole_file(self):
        _engine, repo = self._committed("x = 1\n" * 200)
        write_lines(os.path.join(repo, "f.py"), 201)          # 加 1 行
        self.assertEqual(self._total(repo, ["f.py"]), 1,
                         "必须算 diff（1 行），不是整文件（201 行）")

    def test_staged_change_is_still_measured_as_a_diff(self):
        """真缺陷回归：已暂存的改动不得被当成整文件。"""
        _engine, repo = self._committed("x = 1\n" * 200)
        write_lines(os.path.join(repo, "f.py"), 201)
        subprocess.run(["git", "add", "f.py"], cwd=repo, check=True)
        self.assertEqual(self._total(repo, ["f.py"]), 1,
                         "暂存后仍应算 diff（1 行），不是 201")

    def test_net_change_is_measured_against_head(self):
        """既暂存又未暂存时，读数要算**净改动**（工作区 vs HEAD），不把两个状态相加。

        实测过的反例：200 行文件 → 暂存 +5 行 → 又把工作区改回 200 行（净 0）。
        `git diff HEAD` 给 0（对）；「未暂存 + 已暂存」相加给 10（虚高，会误触预算门）。
        这条同时钉住了「为何先查 HEAD」——否则那段代码是**未验证的声明**。
        """
        _engine, repo = self._committed("x = 1\n" * 200)
        path = os.path.join(repo, "f.py")
        write_lines(path, 205)
        subprocess.run(["git", "add", "f.py"], cwd=repo, check=True)
        write_lines(path, 200)                     # 净改动回到 0 行
        self.assertEqual(self._total(repo, ["f.py"]), 0,
                         "净改动是 0，不能把暂存与未暂存相加成 10")

    def test_per_file_agrees_with_total(self):
        """证据必须自洽：`total` == `sum(per_file.values())`。

        为何单独要这一条：只断言 `total` 时，一个“重复计数/覆盖记数”的实现仍能凑对 `total`，
        但 `per_file` 已经错了——而 `per_file` 是落盘证据的一部分，错读无法审计。
        （变异测试实测：不断言 `per_file` 时，这类变异漏抓过一次。）
        """
        _engine, repo = self._committed("x = 1\n" * 100)
        write_lines(os.path.join(repo, "f.py"), 101)          # 已跟踪：改 1 行
        write_lines(os.path.join(repo, "new.py"), 3)          # 未跟踪：新文件
        size = gates.diff_size(repo, ["f.py", "new.py"])
        assert size is not None
        self.assertEqual(size["per_file"], {"f.py": 1, "new.py": 3})
        self.assertEqual(size["total"], sum(size["per_file"].values()))
        self.assertEqual(size["total"], 4)

    def test_tracked_staged_new_file_counts_its_lines(self):
        """已暂存的**新**文件（从无到有）：整文件就是它的改动量。"""
        _engine, repo = git_repo(self, {}, commit=True)
        write_lines(os.path.join(repo, "fresh.py"), 7)
        subprocess.run(["git", "add", "fresh.py"], cwd=repo, check=True)
        self.assertEqual(self._total(repo, ["fresh.py"]), 7)

    def test_untracked_new_file_counts_its_lines(self):
        """兜底分支（未跟踪新文件）行为不变。"""
        _engine, repo = git_repo(self, {}, commit=True)
        write_lines(os.path.join(repo, "new.py"), 7)
        self.assertEqual(self._total(repo, ["new.py"]), 7)

    def test_no_baseline_is_none_and_empty_is_zero(self):
        _engine, repo = self._committed("x = 1\n")
        self.assertIsNone(gates.diff_size(repo, None))
        self.assertEqual(gates.diff_size(repo, []), {"total": 0, "per_file": {}})

    def test_non_git_dir_is_none(self):
        """非 git 仓不假装有读数（非 None 会被当成「读到 0 行」而误放行）。"""
        self.assertIsNone(gates.diff_size(self.tmp, ["a.py"]))


class ExpectSemanticsTest(DisflowTestCase):
    """`acceptance.expect` 三分支的语义（真缺口 B4）。

    此前 `contains:` 只被间接用到（`base.acceptance()`），**失败语义从未断言**；
    `regex:` 在测试里**零出现**；非法 expect 的报错路径也没测。

    两条易错语义（容易写成“只是检查子串/正则”而漏掉退出码）：
    `contains:` 与 `regex:` 都要求 **exit == 0 且匹配成功**——其中一个不成立就拒收。
    """

    def _run(self, cmd: str, expect: str = "exit0", **extra) -> tuple[bool, str, dict]:
        acceptance = {"cmd": cmd, "expect": expect, **extra}
        return gates.run_acceptance(acceptance, self.tmp)

    @staticmethod
    def _echo(text: str) -> str:
        return f'{sys.executable} -c "print({text!r})"'

    # ── exit0 ────────────────────────────────────────────────────────────

    def test_exit0_accepts_zero_and_rejects_nonzero(self):
        ok, _why, meta = self._run(f'{sys.executable} -c "pass"')
        self.assertTrue(ok)
        self.assertEqual(meta["exit"], 0)
        ok, why, meta = self._run(f'{sys.executable} -c "import sys; sys.exit(7)"')
        self.assertFalse(ok)
        self.assertEqual(meta["exit"], 7)
        self.assertIn("7", why)

    def test_exit0_is_the_default_expect(self):
        ok, _why, meta = gates.run_acceptance({"cmd": f'{sys.executable} -c "pass"'}, self.tmp)
        self.assertTrue(ok)
        self.assertEqual(meta["expect"], "exit0")

    # ── contains: ────────────────────────────────────────────────────────

    def test_contains_accepts_a_present_needle(self):
        ok, _why, _meta = self._run(self._echo("hello world"), "contains:world")
        self.assertTrue(ok)

    def test_contains_rejects_a_missing_needle(self):
        ok, why, _meta = self._run(self._echo("hello"), "contains:world")
        self.assertFalse(ok)
        self.assertIn("world", why)

    def test_contains_requires_exit_zero_even_if_needle_present(self):
        """关键语义：输出里有子串但**退出码非零** ⇒ 拒收。

        漏了这条，验收命令就能“打印一句 ok 然后失败”而照样过门。
        """
        cmd = (f'{sys.executable} -c "import sys; print(\'ok\'); sys.exit(1)"')
        ok, _why, meta = self._run(cmd, "contains:ok")
        self.assertFalse(ok, "exit=1 时即使包含子串也必须拒收")
        self.assertEqual(meta["exit"], 1)

    def test_contains_looks_at_stderr_too(self):
        """验收输出是 stdout+stderr 合并（否则测试框架写 stderr 的断言永远看不到）。"""
        cmd = f'{sys.executable} -c "import sys; print(\'boom\', file=sys.stderr)"'
        ok, _why, _meta = self._run(cmd, "contains:boom")
        self.assertTrue(ok)

    # ── regex: ───────────────────────────────────────────────────────────

    def test_regex_accepts_a_match(self):
        ok, _why, _meta = self._run(self._echo("build 42 ok"), r"regex:build \d+ ok")
        self.assertTrue(ok)

    def test_regex_rejects_a_non_match(self):
        ok, why, _meta = self._run(self._echo("build xx ok"), r"regex:build \d+ ok")
        self.assertFalse(ok)
        self.assertIn("build", why)

    def test_regex_requires_exit_zero(self):
        cmd = (f'{sys.executable} -c "import sys; print(\'v1.2.3\'); sys.exit(2)"')
        ok, _why, _meta = self._run(cmd, r"regex:v\d+\.\d+\.\d+")
        self.assertFalse(ok, "匹配成立但 exit=2 ⇒ 必须拒收")

    def test_regex_is_a_search_not_a_full_match(self):
        ok, _why, _meta = self._run(self._echo("abc42xyz"), r"regex:\d+")
        self.assertTrue(ok)

    # ── 非法 / 缺声明 / 报错路径 ──────────────────────────────────────────

    def test_unknown_expect_raises_instead_of_passing(self):
        """非法 expect 必须**报错**，不能默默当通过（静默放行比报错危险得多）。"""
        with self.assertRaises(ValueError) as ctx:
            self._run(self._echo("x"), "startswith:x")
        self.assertIn("expect 非法", str(ctx.exception))

    def test_missing_cmd_is_rejected(self):
        ok, why, _meta = gates.run_acceptance({"expect": "exit0"}, self.tmp)
        self.assertFalse(ok)
        self.assertIn("缺少 acceptance.cmd", why)

    def test_unparseable_cmd_is_rejected_not_crashed(self):
        ok, why, _meta = gates.run_acceptance({"cmd": 'echo "未闭合', "expect": "exit0"}, self.tmp)
        self.assertFalse(ok)
        self.assertIn("argv", why)

    def test_missing_executable_is_rejected_not_crashed(self):
        ok, why, _meta = gates.run_acceptance({"cmd": "definitely-not-a-real-binary",
                                               "expect": "exit0"}, self.tmp)
        self.assertFalse(ok)
        self.assertIn("无法执行", why)

    def test_timeout_is_rejected_not_hung(self):
        cmd = f'{sys.executable} -c "import time; time.sleep(5)"'
        ok, why, meta = gates.run_acceptance({"cmd": cmd, "expect": "exit0"}, self.tmp, timeout=1)
        self.assertFalse(ok)
        self.assertIn("超时", why)
        self.assertEqual(meta["timeout"], 1)

    def test_waiver_passes_without_running_and_is_recorded(self):
        """显式豁免：不跑命令、放行，但理由进证据（可审计，不是静默放行）。"""
        ok, why, meta = gates.run_acceptance(
            {"cmd": "definitely-not-a-real-binary", "waiver": "本机无该工具"}, self.tmp)
        self.assertTrue(ok)
        self.assertIn("显式豁免", why)
        self.assertEqual(meta["waiver"], "本机无该工具")

    def test_output_tail_is_capped(self):
        """证据里的 output_tail 必须封顶（否则单条证据能把投影撞爆）。"""
        _ok, _why, meta = self._run(self._echo("z" * 5000))
        self.assertEqual(len(meta["output_tail"]), 2000)

    def test_failing_acceptance_surfaces_the_reason_in_the_gate(self):
        """集成：验收不过 ⇒ `done` 拒收，且缺项里带得出原因。"""
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "g", "touch_plan": [],
                                     "acceptance": {"cmd": f'{sys.executable} -c "pass"',
                                                    "expect": "contains:NEEDLE"}}])
        engine.start("t", "n", "w")
        with self.assertRaises(GateReject) as ctx:
            engine.done("t", "n")
        self.assertTrue(any("验收未通过" in m for m in ctx.exception.missing))
        self.assertTrue(any("NEEDLE" in m for m in ctx.exception.missing))


if __name__ == "__main__":
    unittest.main()
