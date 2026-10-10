"""端到端验证（V1）：跨子树依赖 + `drive` 确定性。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine


class EndToEndTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        e = self.engine
        e.init_tree("e2e", "跨子树依赖")
        # 两个分支；B1 依赖另一分支的 A2 —— 树表达不了，必须靠图
        e.expand("e2e", "root", [{"id": "A", "kind": "container", "stage": "dev", "goal": "分支A"}])
        e.expand("e2e", "A", [
            {"id": "A1", "goal": "A1", "acceptance": acceptance(), "touch_plan": ["f/a1"]},
            {"id": "A2", "goal": "A2", "acceptance": acceptance(), "touch_plan": ["f/a2"]},
        ])
        e.expand("e2e", "root", [{"id": "B1", "goal": "B1", "acceptance": acceptance(),
                                  "touch_plan": ["g/b1"]}])
        e.require("e2e", "A2", "A1")
        e.require("e2e", "B1", "A2")           # 跨子树依赖

    def _finish(self, node: str) -> None:
        self.engine.start("e2e", node, "w")
        self.engine.collect("e2e", node, {"status": "ok", "summary": node,
                                          "changed_files": []})
        self.engine.done("e2e", node)

    def test_cross_subtree_dependency_orders_waves(self):
        self.assertEqual(self.engine.drive("e2e")["ready_wave"], ["A1"])
        self._finish("A1")
        self.assertEqual(self.engine.drive("e2e")["ready_wave"], ["A2"])
        self._finish("A2")
        self.assertEqual(self.engine.drive("e2e")["ready_wave"], ["B1"])
        self._finish("B1")
        self.assertEqual(self.engine.drive("e2e")["live"], [])
        self.assertFalse(self.engine.drive("e2e")["stuck"])

    def test_drive_is_bit_identical(self):
        first = self.engine.drive("e2e")
        second = self.engine.drive("e2e")
        self.assertEqual(first, second)

    def test_drive_json_serializable(self):
        import json
        json.dumps(self.engine.drive("e2e"))


if __name__ == "__main__":
    unittest.main()
