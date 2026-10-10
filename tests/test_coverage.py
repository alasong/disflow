"""树级覆盖门（`tree_coverage`）测试：需求已登记 + 有可跑判据 + 容器不空壳。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine, GateReject
from disflow.gates import run_acceptance, tree_coverage_check


class CoverageGateTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "g")

    # ── 纯函数层 ──────────────────────────────────────────────────────────

    def test_clean_tree_passes(self):
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活",
                                          "acceptance": acceptance()}])
        tree = self.engine.load()["trees"]["t"]
        ok, missing = tree_coverage_check(tree)
        self.assertTrue(ok, missing)

    def test_leaf_without_goal_rejected(self):
        self.engine.expand("t", "root", [{"id": "a", "acceptance": acceptance()}])
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertFalse(ok)
        self.assertTrue(any("未登记目标" in m for m in missing))

    def test_leaf_without_acceptance_rejected(self):
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活"}])
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertFalse(ok)
        self.assertTrue(any("无可跑判据" in m for m in missing))

    def test_waiver_covers_leaf(self):
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "人工审批", "acceptance": {"waiver": "由人判定"}}])
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertTrue(ok, missing)

    def test_empty_nonroot_container_rejected(self):
        self.engine.expand("t", "root", [{"id": "c", "kind": "container", "goal": "空壳"}])
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertFalse(ok)
        self.assertTrue(any("空壳" in m for m in missing))

    def test_unexpanded_root_is_allowed(self):
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertTrue(ok, missing)

    def test_skipped_leaf_exempt(self):
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活"}])
        self.engine.skip("t", "a", "不做了")
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertTrue(ok, missing)

    def test_root_coverage_escape_hatch(self):
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活"}])
        self.engine.annotate("t", "root", {"coverage": "not_applicable: 纯探索，无判据"})
        ok, missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertTrue(ok, missing)

    def test_root_coverage_hatch_requires_reason(self):
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活"}])
        self.engine.annotate("t", "root", {"coverage": "not_applicable:"})
        ok, _missing = tree_coverage_check(self.engine.load()["trees"]["t"])
        self.assertFalse(ok)

    # ── waiver 放行 + 证据可审计 ───────────────────────────────────────────

    def test_waiver_makes_acceptance_pass_and_records_reason(self):
        ok, why, meta = run_acceptance({"waiver": "文档改动无需命令"}, self.tmp)
        self.assertTrue(ok)
        self.assertIn("文档改动无需命令", why)
        self.assertEqual(meta, {"waiver": "文档改动无需命令"})

    def test_waivered_node_can_be_done(self):
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "人工审批", "acceptance": {"waiver": "由人判定"}}])
        self.engine.start("t", "a", "w")
        self.engine.done("t", "a")
        node = self.engine.load()["trees"]["t"]["nodes"]["a"]
        self.assertEqual(node["status"], "done")
        self.assertIn("waiver", node["evidence"]["acceptance"])

    # ── 必填声明 ──────────────────────────────────────────────────────────

    def test_require_declarations_blocks_done(self):
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "批准", "acceptance": dict(acceptance(), require_declarations=["approver"])}])
        self.engine.start("t", "a", "w")
        with self.assertRaises(GateReject) as ctx:
            self.engine.done("t", "a")
        self.assertTrue(any("必填声明" in m for m in ctx.exception.missing))
        self.engine.done("t", "a", {"approver": "song"})
        self.assertEqual(self.engine.load()["trees"]["t"]["nodes"]["a"]["status"], "done")

    # ── close 两门串联 ────────────────────────────────────────────────────

    def test_close_rejects_when_coverage_missing(self):
        engine = self.engine
        for node_id, entry in (("ev", "evaluate"), ("rf", "reflect")):
            engine.expand("t", "root", [{"id": node_id, "goal": "x", "entry": entry,
                                         "acceptance": acceptance()}])
            engine.start("t", node_id, "w")
            engine.done("t", node_id)
        engine.expand("t", "root", [{"id": "bare", "goal": "无判据"}])   # 覆盖缺口
        with self.assertRaises(GateReject) as ctx:
            engine.close("t")
        self.assertTrue(any("无可跑判据" in m for m in ctx.exception.missing))
        self.assertFalse(engine.load()["trees"]["t"].get("closed"))


if __name__ == "__main__":
    unittest.main()
