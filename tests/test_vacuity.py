"""「空洞通过」的记录与报出：门没东西可验时必须可见。

**为什么要有这个**：`disflow-v0.1` 看板被呈现为「v0.1 完成 17/17、五道门」，但实测其中
**16/16 个 done 节点的 `introduced == []`** —— `design_first`（作用域）与 `size_budget`（规模）
两道门其实**什么都没验**。这件事当时没写在任何地方：「门全过」被读成了「门都验了」。

真跑循环里也发生过一次：`C6` 的作用域门空转（同一文件被前一节点弄脏），当时没有任何告知。
所以这不是洁癖，是让一个**静默的空转**变成一行可见的提示。

**边界**：空洞 ≠ 失败。本机制**不改变任何门的通过/拒收结果**，只记录 + 报出。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import gates
from disflow.cli import main


class VacuousReasonsTest(DisflowTestCase):
    """纯函数：哪种情形算空洞、理由是什么。"""

    def test_no_reasons_when_every_gate_had_something_to_check(self):
        reasons = gates.vacuous_reasons(
            acceptance=acceptance(), touch_plan=["src/a.py"],
            introduced=["src/a.py"], size={"total": 12, "per_file": {"src/a.py": 12}})
        self.assertEqual(reasons, {})

    def test_empty_introduced_means_scope_and_size_had_nothing_to_check(self):
        reasons = gates.vacuous_reasons(
            acceptance=acceptance(), touch_plan=["src/a.py"],
            introduced=[], size={"total": 0, "per_file": {}})
        self.assertIn("introduced 为空", reasons["design_first"])
        self.assertIn("改动行数为 0", reasons["size_budget"])
        self.assertNotIn("acceptance", reasons)

    def test_empty_touch_plan_is_reported_as_no_face_to_check(self):
        reasons = gates.vacuous_reasons(
            acceptance=acceptance(), touch_plan=[],
            introduced=["src/a.py"], size={"total": 3, "per_file": {"src/a.py": 3}})
        self.assertIn("touch_plan 为空", reasons["design_first"])
        self.assertNotIn("size_budget", reasons, "规模门有读数，不算空洞")

    def test_waiver_makes_the_acceptance_gate_vacuous(self):
        reasons = gates.vacuous_reasons(
            acceptance={"cmd": "true", "waiver": "本机没有该工具"},
            touch_plan=["a"], introduced=["a"], size={"total": 1, "per_file": {"a": 1}})
        self.assertIn("waiver", reasons["acceptance"])

    def test_non_git_has_no_baseline_so_both_gates_are_vacuous(self):
        reasons = gates.vacuous_reasons(
            acceptance=acceptance(), touch_plan=["a"], introduced=None, size=None)
        self.assertIn("非 git", reasons["design_first"])
        self.assertIn("非 git", reasons["size_budget"])

    def test_reasons_are_never_empty_strings(self):
        """报出的理由必须能读（空理由等于又一处静默）。"""
        cases = [
            {"acceptance": None, "touch_plan": [], "introduced": [], "size": None},
            {"acceptance": acceptance(), "touch_plan": ["a"], "introduced": None, "size": None},
            {"acceptance": {"waiver": "x"}, "touch_plan": [], "introduced": [],
             "size": {"total": 0}},
        ]
        for case in cases:
            for gate, reason in gates.vacuous_reasons(**case).items():
                self.assertTrue(reason.strip(), f"{gate} 的理由为空")


class VacuityDoesNotChangeOutcomesTest(DisflowTestCase):
    """空洞只是记录：门的通过/拒收行为**一个都不能变**。"""

    def test_node_with_no_changes_still_passes_and_is_flagged(self):
        engine, _repo = git_repo(self, {"tracked.py": "x = 1\n"}, commit=True)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "g", "touch_plan": ["tracked.py"],
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        engine.done("t", "n")                      # 没改任何东西
        node = engine.load()["trees"]["t"]["nodes"]["n"]
        self.assertEqual(node["status"], "done")   # 照过
        self.assertEqual(sorted(node["evidence"]["vacuous"]),
                         ["design_first", "size_budget"])

    def test_real_in_scope_change_leaves_no_vacuity_marker(self):
        engine, repo = git_repo(self, {"tracked.py": "x = 1\n"}, commit=True)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "g", "touch_plan": ["tracked.py"],
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        touch(os.path.join(repo, "tracked.py"), "x = 2\n")
        engine.done("t", "n")
        node = engine.load()["trees"]["t"]["nodes"]["n"]
        self.assertEqual(node["evidence"]["vacuous"], {})

    def test_out_of_scope_change_still_rejects(self):
        """反向保护：标记空洞不得把真拒收变成“空洞通过”。"""
        engine, repo = git_repo(self, {"a.py": "1\n", "b.py": "1\n"}, commit=True)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "g", "touch_plan": ["a.py"],
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        touch(os.path.join(repo, "b.py"), "2\n")
        with self.assertRaises(Exception) as ctx:
            engine.done("t", "n")
        self.assertIn("touch_plan", str(ctx.exception))


class VacuityReportingTest(DisflowTestCase):
    """装配层：报告真的打到输出上（否则机制等于不存在）。"""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _tree_with(self, children: list[dict]) -> None:
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   json.dumps(children, ensure_ascii=False)])

    def test_done_prints_a_vacuity_line(self):
        self._tree_with([{"id": "n", "goal": "g", "touch_plan": ["a.py"],
                          "acceptance": acceptance()}])
        self._run(["start", "t", "n", "--holder", "me"])
        self._run(["collect", "t", "n", "--result",
                   '{"status":"ok","summary":"s","changed_files":[]}'])
        code, out, _err = self._run(["done", "t", "n"])
        self.assertEqual(code, 0)
        self.assertIn("⚠️", out)
        self.assertIn("design_first", out)
        self.assertIn("size_budget", out)

    def test_close_summarises_vacuous_gates_across_the_tree(self):
        self._tree_with([
            {"id": "n", "goal": "g", "touch_plan": [], "acceptance": acceptance()},
            {"id": "E1", "goal": "e", "entry": "evaluate", "touch_plan": [],
             "acceptance": acceptance()},
            {"id": "S1", "goal": "s", "entry": "standardize", "touch_plan": [],
             "acceptance": acceptance()},
        ])
        for node in ("n", "E1", "S1"):
            self._run(["start", "t", node, "--holder", "me"])
            self._run(["collect", "t", node, "--result",
                       '{"status":"ok","summary":"s","changed_files":[]}'])
            self._run(["done", "t", node])
        code, out, _err = self._run(["close", "t"])
        self.assertEqual(code, 0)
        self.assertIn("空洞通过", out)
        self.assertIn("3 个 done 节点", out)


if __name__ == "__main__":
    unittest.main()
