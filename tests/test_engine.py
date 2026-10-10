"""引擎端到端测试：建树、建图、就绪、分发、门、证据、确定性。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from base import DisflowTestCase, acceptance, always_fails

from disflow import gates, spec, state, store
from disflow.engine import Engine, EngineError, GateReject


def make_engine(test: DisflowTestCase) -> Engine:
    return Engine(cwd=test.tmp)          # 非 git 仓 → 作用域检查跳过，测试隔离


class EngineFlowTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = make_engine(self)

    def _seed(self):
        e = self.engine
        e.init_tree("t1", "交付 v0.1")
        e.expand("t1", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "touch_plan": ["src/a.py"],
             "acceptance": acceptance("A")},
            {"id": "b", "stage": "dev", "goal": "做 B", "touch_plan": ["src/b.py"],
             "acceptance": acceptance("B")},
        ])
        e.require("t1", "b", "a")

    def test_drive_lists_ready_wave(self):
        self._seed()
        result = self.engine.drive("t1")
        self.assertEqual(result["ready_wave"], ["a"])
        self.assertEqual(result["blocked"]["b"], ["a"])

    def test_drive_is_deterministic(self):
        self._seed()
        self.assertEqual(self.engine.drive("t1"), self.engine.drive("t1"))

    def test_full_lifecycle_unlocks_dependent(self):
        self._seed()
        self.engine.start("t1", "a", "w1")
        self.engine.collect("t1", "a", {"status": "ok", "summary": "完成",
                                        "changed_files": ["src/a.py"]})
        self.engine.done("t1", "a", {"note": "A 完成"})
        state = self.engine.load()
        self.assertEqual(state["trees"]["t1"]["nodes"]["a"]["status"], "done")
        self.assertEqual(self.engine.drive("t1")["ready_wave"], ["b"])

    def test_done_attaches_evidence(self):
        self._seed()
        self.engine.done("t1", "a")
        node = self.engine.load()["trees"]["t1"]["nodes"]["a"]
        self.assertTrue(node["evidence"]["acceptance_ok"])
        self.assertIn("output_tail", node["evidence"]["acceptance"])

    def test_gate_rejects_failing_acceptance(self):
        e = self.engine
        e.init_tree("t2", "g")
        e.expand("t2", "root", [{"id": "x", "acceptance": always_fails()}])
        with self.assertRaises(GateReject) as ctx:
            e.done("t2", "x")
        self.assertTrue(any("验收未通过" in m for m in ctx.exception.missing))
        self.assertEqual(ctx.exception.contract["node"], "x")

    def test_gate_rejects_missing_acceptance(self):
        e = self.engine
        e.init_tree("t3", "g")
        e.expand("t3", "root", [{"id": "y", "goal": "无验收"}])
        with self.assertRaises(GateReject):
            e.done("t3", "y")

    def test_scope_violation_rejected_in_git_repo(self):
        repo = tempfile.mkdtemp(prefix="disflow-git-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q", repo], check=True)
        with open(os.path.join(repo, "other.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        e = Engine(cwd=repo)                 # 自建脏 git 仓，不依赖本仓状态
        e.init_tree("t4", "g")
        e.expand("t4", "root", [{"id": "z", "touch_plan": ["src/z.py"],
                                 "acceptance": acceptance("Z")}])
        with self.assertRaises(GateReject) as ctx:
            e.done("t4", "z")
        self.assertTrue(any("超出 touch_plan" in m for m in ctx.exception.missing))

    def test_require_cycle_rejected(self):
        e = self.engine
        e.init_tree("t5", "g")
        e.expand("t5", "root", [{"id": "p"}, {"id": "q"}])
        e.require("t5", "p", "q")
        with self.assertRaises(EngineError):
            e.require("t5", "q", "p")

    def test_consume_unregistered_rejected(self):
        e = self.engine
        e.init_tree("t6", "g")
        e.expand("t6", "root", [{"id": "m"}])
        with self.assertRaises(EngineError):
            e.consume("t6", "m", "ghost")
        e.produce("t6", "m", "thing")
        e.consume("t6", "m", "thing")   # 登记后可消费

    def test_start_blocked_node_rejected(self):
        self._seed()
        with self.assertRaises(EngineError):
            self.engine.start("t1", "b", "w1")

    def test_collect_result_contract(self):
        self._seed()
        self.engine.start("t1", "a", "w1")
        with self.assertRaises(EngineError):
            self.engine.collect("t1", "a", {"status": "ok"})   # 缺 summary/changed_files
        self.engine.collect("t1", "a", {"status": "ok", "summary": "s",
                                        "changed_files": []})

    def test_fail_then_retry(self):
        self._seed()
        self.engine.start("t1", "a", "w1")
        self.engine.fail("t1", "a", "boom")
        self.assertEqual(self.engine.load()["trees"]["t1"]["nodes"]["a"]["status"], "failed")
        self.engine.retry("t1", "a")
        self.assertEqual(self.engine.drive("t1")["ready_wave"], ["a"])

    def test_brief_carries_ancestor_context(self):
        e = self.engine
        e.init_tree("t7", "g")
        e.expand("t7", "root", [{"id": "p", "kind": "container",
                                 "context": {"background": "系统级背景"}}])
        e.expand("t7", "p", [{"id": "leaf", "acceptance": acceptance()}])
        payload = e.brief("t7", "leaf")
        self.assertEqual(payload["context"]["background"], "系统级背景")

    def test_projection_rebuild_after_writes(self):
        self._seed()
        self.engine.done("t1", "a")
        matched, diffs = store.projection_matches(spec.SPEC, project=state.project)
        self.assertTrue(matched, diffs)
    def test_baseline_excludes_preexisting_dirt(self):
        repo = tempfile.mkdtemp(prefix="disflow-git-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q", repo], check=True)
        with open(os.path.join(repo, "other.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        e = Engine(cwd=repo)
        e.init_tree("t8", "g")
        e.expand("t8", "root", [{"id": "n", "touch_plan": ["src/n.py"],
                                  "acceptance": acceptance("N")}])
        e.start("t8", "n", "w")
        e.done("t8", "n")
        node = e.load()["trees"]["t8"]["nodes"]["n"]
        self.assertEqual(node["evidence"]["introduced"], [])
        self.assertIn("other.py", node["evidence"]["baseline"])

    def test_scope_dot_covers_all_and_prefix_matches(self):
        self.assertEqual(gates.scope_check(["."], ["a.py", "dir/b.py"])["violations"], [])
        self.assertEqual(gates.scope_check(["src"], ["src/a.py", "other/b.py"])["violations"],
                         ["other/b.py"])
        self.assertFalse(gates.scope_check([], ["x"])["checked"])


if __name__ == "__main__":
    unittest.main()
