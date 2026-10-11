"""`--dry-run` 预演测试（FR-13）：门照跑，**一个事件也不落**。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance, always_fails

from disflow import vocab as vocab_mod
from disflow import store
from disflow.engine import Engine, GateReject


class DryRunTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.events = store.events_path(vocab_mod.STORAGE)

    def _rows(self) -> int:
        return len(store.read_jsonl(self.events))

    def _node(self, tree: str, node: str) -> dict:
        return self.engine.load()["trees"][tree]["nodes"][node]

    def _seed_running(self, accept: dict | None = None) -> None:
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活",
                                          "acceptance": accept or acceptance()}])
        self.engine.start("t", "a", "w")

    # ── done 预演 ─────────────────────────────────────────────────────────

    def test_done_dry_run_passes_and_writes_nothing(self):
        self._seed_running()
        before = self._rows()
        out = self.engine.done("t", "a", dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["kind"], "node.done")
        self.assertIn("evidence", out["extra"])
        self.assertEqual(self._rows(), before, "预演不许落事件")
        self.assertEqual(self._node("t", "a")["status"], "running", "预演不许改状态")

    def test_done_dry_run_still_rejects(self):
        self._seed_running(always_fails())
        before = self._rows()
        with self.assertRaises(GateReject) as ctx:
            self.engine.done("t", "a", dry_run=True)
        self.assertTrue(any("验收未通过" in m for m in ctx.exception.missing))
        self.assertEqual(self._rows(), before, "预演被拒也不许落事件")
        self.assertEqual(self._node("t", "a")["status"], "running")

    def test_done_dry_run_then_real_done(self):
        """预演不改状态，真 done 仍能成功（预演不消耗节点）。"""
        self._seed_running()
        self.engine.done("t", "a", dry_run=True)
        self.engine.done("t", "a")
        self.assertEqual(self._node("t", "a")["status"], "done")

    # ── start 预演 ────────────────────────────────────────────────────────

    def test_start_dry_run_writes_nothing(self):
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [{"id": "a", "goal": "干活",
                                          "acceptance": acceptance()}])
        before = self._rows()
        out = self.engine.start("t", "a", "w", dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertIsNone(self._node("t", "a")["lease"], "预演不许占租约")
        self.assertEqual(self._node("t", "a")["status"], "pending")
        self.assertEqual(self._rows(), before)

    # ── close 预演 ────────────────────────────────────────────────────────

    def test_close_dry_run_writes_nothing(self):
        engine = self.engine
        engine.init_tree("t", "g")
        engine.annotate("t", "root", {"closure": "not_applicable: 测试用"})
        before = self._rows()
        out = engine.close("t", dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertFalse(engine.load()["trees"]["t"].get("closed"))
        self.assertEqual(self._rows(), before)

    def test_close_dry_run_still_rejects_when_coverage_missing(self):
        engine = self.engine
        engine.init_tree("t", "g")
        engine.annotate("t", "root", {"closure": "not_applicable: 测试用"})
        engine.expand("t", "root", [{"id": "bare", "goal": "没判据"}])
        before = self._rows()
        with self.assertRaises(GateReject):
            engine.close("t", dry_run=True)
        self.assertEqual(self._rows(), before)

    # ── drive 显式只读 ────────────────────────────────────────────────────

    def test_drive_is_read_only_by_construction(self):
        self._seed_running()
        before = self._rows()
        self.engine.drive("t")
        self.engine.plan("t")
        self.assertEqual(self._rows(), before)


if __name__ == "__main__":
    unittest.main()
