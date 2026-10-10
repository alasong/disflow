"""多块板（多棵树）测试：作用域隔离 + 跨树显式完成信号 `awaits`。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine, EngineError


class MultiTreeTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def _close_a(self) -> None:
        self.engine.annotate("A", "root", {"closure": "not_applicable: 测试用"})
        self.engine.close("A")

    def test_trees_are_isolated(self):
        engine = self.engine
        engine.init_tree("A", "板A")
        engine.init_tree("B", "板B")
        engine.expand("A", "root", [{"id": "x", "acceptance": acceptance()}])
        engine.expand("B", "root", [{"id": "y", "acceptance": acceptance()}])
        # A 的节点不出现在 B 的推进面，反之亦然
        self.assertEqual(engine.drive("A")["live"], ["x"])
        self.assertEqual(engine.drive("B")["live"], ["y"])
        with self.assertRaises(EngineError):          # 跨树 require 不可解析
            engine.require("B", "y", "x")

    def test_awaits_blocks_until_other_tree_closed(self):
        engine = self.engine
        engine.init_tree("A", "板A")
        engine.init_tree("B", "板B")
        engine.expand("B", "root", [{"id": "b1", "awaits": ["A"],
                                     "acceptance": acceptance()}])
        # A 未关闭 → b1 不就绪，且阻塞报告带 @A
        self.assertEqual(engine.drive("B")["ready_wave"], [])
        self.assertEqual(engine.drive("B")["blocked"]["b1"], ["@A"])
        with self.assertRaises(EngineError):
            engine.start("B", "b1", "w")
        # 关闭 A → 信号满足 → b1 就绪
        self._close_a()
        self.assertEqual(engine.drive("B")["ready_wave"], ["b1"])
        engine.start("B", "b1", "w")
        engine.done("B", "b1")
        self.assertEqual(engine.drive("B")["live"], [])

    def test_awaits_with_empty_tree_is_ignored(self):
        engine = self.engine
        engine.init_tree("B", "板B")
        engine.expand("B", "root", [{"id": "b1", "acceptance": acceptance()}])
        self.assertEqual(engine.drive("B")["ready_wave"], ["b1"])

    def test_closed_trees_listing(self):
        engine = self.engine
        engine.init_tree("A", "板A")
        self.assertEqual(engine.closed_trees(), set())
        self._close_a()
        self.assertEqual(engine.closed_trees(), {"A"})
        self.assertTrue(engine.status("A")["closed"])
    def test_plan_threads_closed_like_drive(self):
        engine = self.engine
        engine.init_tree("A", "板A")
        engine.init_tree("B", "板B")
        engine.expand("B", "root", [{"id": "b1", "awaits": ["A"],
                                     "acceptance": acceptance()}])
        self.assertEqual(engine.plan("B"), [])
        self._close_a()
        plan = engine.plan("B")
        self.assertEqual([item["node"] for item in plan], ["b1"])
        # plan 必须与 drive 的 ready_wave 一致（两者同源，不能分叉）
        self.assertEqual([item["node"] for item in plan], engine.drive("B")["ready_wave"])


if __name__ == "__main__":
    unittest.main()
