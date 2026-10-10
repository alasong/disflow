"""`requires` 边的**增**与**删**（`require` / `unrequire`）。

为何删边需要一个动词：建错依赖时此前只能 `skip` 整个节点——一次方向搞反就废掉一个节点
（真跑循环里 `C7` 就是这样废掉的）。但删边不能把加边的校验搞松，所以两者**故意不对称**：

- 加边：两端必须存在 + 拒自环 + 拒成环（不许造出非法图）
- 删边：只要求「这条边确实存在」；**不要求 `dep` 节点存在**——
  否则悬空边反而清不掉（`doctor` 报得出、没人修得了）
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow import spec, store
from disflow.engine import Engine, EngineError
from disflow.ready import blocked_by, ready_tasks
from disflow.state import project


class EdgeTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)      # 非 git 仓：本文件不测作用域门
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [
            {"id": name, "goal": name, "touch_plan": [], "acceptance": acceptance()}
            for name in ("A", "B", "C")
        ])

    def _tree(self) -> dict:
        return self.engine.load()["trees"]["t"]

    def _events(self) -> int:
        return len(store.load_events(spec.SPEC))

    def _ready(self) -> list[str]:
        return sorted(ready_tasks(self._tree()["nodes"], self.engine.closed_trees()))

    # ── 删边的正确用法 ───────────────────────────────────────────────────

    def test_unrequire_removes_the_edge_and_unblocks_the_node(self):
        self.engine.require("t", "B", "A")
        self.assertEqual(blocked_by(self._tree()["nodes"], "B",
                                    self.engine.closed_trees()), ["A"])
        self.engine.unrequire("t", "B", "A")
        self.assertEqual(self._tree()["nodes"]["B"]["requires"], [])
        self.assertEqual(blocked_by(self._tree()["nodes"], "B", self.engine.closed_trees()), [])
        self.assertIn("B", self._ready())

    def test_unrequire_only_touches_the_named_edge(self):
        self.engine.require("t", "C", "A")
        self.engine.require("t", "C", "B")
        self.engine.unrequire("t", "C", "A")
        self.assertEqual(self._tree()["nodes"]["C"]["requires"], ["B"])

    def test_unrequire_can_clear_a_dangling_edge(self):
        """悬空边必须可清（这正是删边**不**要求 dep 节点存在的原因）。

        悬空边不可能由 `require` 造成（它要求两端存在），只能由导入 / 手工修复路径写进来；
        所以这里直接 append 事件来模拟那种来源。
        """
        store.append_event(spec.SPEC, "node.require", project=project,
                           slug="t", node="A", extra={"dep": "ghost"})
        self.assertEqual(self._tree()["nodes"]["A"]["requires"], ["ghost"])
        self.engine.unrequire("t", "A", "ghost")
        self.assertEqual(self._tree()["nodes"]["A"]["requires"], [])

    # ── 删边的拒绝路径 ───────────────────────────────────────────────────

    def test_unrequire_rejects_a_missing_edge(self):
        with self.assertRaises(EngineError) as ctx:
            self.engine.unrequire("t", "B", "A")
        self.assertIn("没有 requires", str(ctx.exception))

    def test_unrequire_rejects_an_unknown_node(self):
        with self.assertRaises(EngineError):
            self.engine.unrequire("t", "不存在", "A")

    def test_rejected_unrequire_writes_nothing(self):
        """被拒的写入不得进事件流（`store._persist` 的先试算契约）。"""
        self.engine.require("t", "B", "A")
        before = self._events()
        for _ in range(2):                              # 幂等重试也不行
            with self.assertRaises(EngineError):
                self.engine.unrequire("t", "C", "A")
        self.assertEqual(self._events(), before)
        matched, diffs = store.projection_matches(spec.SPEC, project=project)
        self.assertTrue(matched, diffs)                 # 事件流仍可重放

    # ── 加边的校验没有被放松（反向保护）─────────────────────────────────

    def test_require_still_rejects_cycles_and_self_loops(self):
        self.engine.require("t", "B", "A")
        self.engine.require("t", "C", "B")
        with self.assertRaises(EngineError):
            self.engine.require("t", "A", "C")          # 成环
        with self.assertRaises(EngineError):
            self.engine.require("t", "A", "A")          # 自环

    def test_adding_back_a_removed_edge_is_allowed(self):
        """删错了能加回来（删边不是单向门）。"""
        self.engine.require("t", "B", "A")
        self.engine.unrequire("t", "B", "A")
        self.engine.require("t", "B", "A")
        self.assertEqual(self._tree()["nodes"]["B"]["requires"], ["A"])

    def test_replay_reconstructs_the_net_edges(self):
        self.engine.require("t", "B", "A")
        self.engine.require("t", "B", "C")
        self.engine.unrequire("t", "B", "A")
        self.engine.unrequire("t", "B", "C")
        self.engine.require("t", "B", "C")
        self.assertEqual(self._tree()["nodes"]["B"]["requires"], ["C"])
        matched, diffs = store.projection_matches(spec.SPEC, project=project)
        self.assertTrue(matched, diffs)


if __name__ == "__main__":
    unittest.main()
