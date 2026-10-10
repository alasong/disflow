"""门测试：`size_budget`（规模预算）与 `tree_closure`（树终止条件）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine, GateReject


def git_repo(test: DisflowTestCase) -> str:
    repo = tempfile.mkdtemp(prefix="disflow-gate-")
    test.addCleanup(shutil.rmtree, repo, ignore_errors=True)
    subprocess.run(["git", "init", "-q", repo], check=True)
    return repo


def write_lines(path: str, count: int) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x = 1\n" * count)


class SizeBudgetTest(DisflowTestCase):
    def test_over_budget_rejected_then_waiver_passes(self):
        repo = git_repo(self)
        engine = Engine(cwd=repo)
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
        repo = git_repo(self)
        engine = Engine(cwd=repo)
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


if __name__ == "__main__":
    unittest.main()
