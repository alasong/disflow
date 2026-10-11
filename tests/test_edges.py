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

from disflow import vocab, store
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
        return len(store.load_events(vocab.STORAGE))

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
        store.append_event(vocab.STORAGE, "node.require", project=project,
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
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)                 # 事件流仍可重放

    # ── 删信息边（`unrelate`）──────────────────────────────────────────

    def test_unrelate_removes_the_edge(self):
        self.engine.relate("t", "A", "B")
        self.assertEqual(self._tree()["nodes"]["A"]["relates"], ["B"])
        self.engine.unrelate("t", "A", "B")
        self.assertEqual(self._tree()["nodes"]["A"]["relates"], [])

    def test_unrelate_only_touches_the_named_edge(self):
        self.engine.relate("t", "A", "B")
        self.engine.relate("t", "A", "C")
        self.engine.unrelate("t", "A", "B")
        self.assertEqual(self._tree()["nodes"]["A"]["relates"], ["C"])

    def test_unrelate_does_not_touch_requires(self):
        """两个边种分得很清：删 `relates` 不能碰 `requires`。

        两者语义不同（调度 vs 信息），一个动词不能同时管两边。
        """
        self.engine.require("t", "B", "A")
        self.engine.relate("t", "B", "C")
        self.engine.unrelate("t", "B", "C")
        node = self._tree()["nodes"]["B"]
        self.assertEqual(node["relates"], [])
        self.assertEqual(node["requires"], ["A"], "requires 不该被动到")

    def test_unrelate_can_clear_a_dangling_edge(self):
        """对端不存在也要能删（与 `unrequire` 同样的理由）。"""
        store.append_event(vocab.STORAGE, "node.relate", project=project,
                           slug="t", node="A", extra={"other": "ghost"})
        self.assertEqual(self._tree()["nodes"]["A"]["relates"], ["ghost"])
        self.engine.unrelate("t", "A", "ghost")
        self.assertEqual(self._tree()["nodes"]["A"]["relates"], [])

    def test_unrelate_rejects_a_missing_edge(self):
        self.engine.relate("t", "A", "B")
        with self.assertRaises(EngineError):
            self.engine.unrelate("t", "A", "C")

    def test_unrelate_rejects_an_unknown_node(self):
        with self.assertRaises(EngineError):
            self.engine.unrelate("t", "没有这个节点", "A")

    def test_rejected_unrelate_writes_nothing(self):
        """被拒的写入不得进事件流（`store._persist` 的先试算契约）。"""
        before = self._events()
        for _ in range(2):
            with self.assertRaises(EngineError):
                self.engine.unrelate("t", "A", "B")
        self.assertEqual(self._events(), before)
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)                 # 事件流仍可重放

    def test_relates_never_affects_readiness(self):
        """`unrelate` 优先级低的原因，写成断言：信息边**不参与就绪判定**。

        加/删 `relates` 前后，就绪集合必须一模一样——否则就不叫“信息边”了。
        """
        baseline = self._ready()
        self.engine.relate("t", "B", "A")
        self.assertEqual(self._ready(), baseline, "relate 不该改变就绪集合")
        self.engine.unrelate("t", "B", "A")
        self.assertEqual(self._ready(), baseline, "unrelate 不该改变就绪集合")

    def test_relates_replay_reconstructs_the_net_edges(self):
        self.engine.relate("t", "A", "B")
        self.engine.relate("t", "A", "C")
        self.engine.unrelate("t", "A", "B")
        self.engine.relate("t", "A", "B")              # 删错了能加回来
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)
        self.assertEqual(sorted(self._tree()["nodes"]["A"]["relates"]), ["B", "C"],
                         "删错了能加回来；顺序按追加顺序（删 B 后是 ['C']，再加 B → ['C','B']）")

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
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)


class ExpandDeclarationTest(DisflowTestCase):
    """回归（真缺陷，第二次真跑循环撞上）：`expand` 声明的边字段曾被**静默丢弃**。

    `validate_node_spec` 接受 `requires`/`relates`/`produces`/`consumes`（都在 `NODE_FIELDS` 里），
    但 `new_node` 根本没这四个参数 → 声明通过了校验、然后什么都不发生。

    后果不是「少个字段」而是**调度乱序**：`requires` 丢了，评估节点在它所评估的工作
    未完工时就被推进首波——直接毁灭「只推不跑」的前提。
    """

    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "g")

    def _tree(self) -> dict:
        return self.engine.load()["trees"]["t"]

    def _events(self) -> int:
        return len(store.load_events(vocab.STORAGE))

    @staticmethod
    def _spec(node_id: str, **extra) -> dict:
        spec: dict = {"id": node_id, "goal": node_id, "acceptance": acceptance()}
        spec.update(extra)
        return spec

    # ── 声明必须生效 ────────────────────────────────────────────────────

    def test_declared_requires_is_not_silently_dropped(self):
        self.engine.expand("t", "root", [self._spec("A"),
                                          self._spec("B", requires=["A"])])
        self.assertEqual(self._tree()["nodes"]["B"]["requires"], ["A"])

    def test_declared_requires_actually_blocks_dispatch(self):
        """真正的判据：**就绪波次**必须反映出声明的依赖。"""
        self.engine.expand("t", "root", [self._spec("A"), self._spec("B", requires=["A"]),
                                          self._spec("E", requires=["A", "B"])])
        nodes = self._tree()["nodes"]
        ready = sorted(ready_tasks(nodes, self.engine.closed_trees()))
        self.assertEqual(ready, ["A"], "只应有 A 就绪；B/E 被依赖阻塞")
        self.assertEqual(blocked_by(nodes, "E", self.engine.closed_trees()), ["A", "B"])

    def test_declared_relates_produces_consumes_land_in_the_projection(self):
        self.engine.expand("t", "root", [
            self._spec("A", produces=["X"]),
            self._spec("B", consumes=["X"], relates=["A"]),
        ])
        tree = self._tree()
        self.assertEqual(tree["nodes"]["A"]["produces"], ["X"])
        self.assertEqual(tree["nodes"]["B"]["consumes"], ["X"])
        self.assertEqual(tree["nodes"]["B"]["relates"], ["A"])
        self.assertEqual(tree["assets"], {"X": "A"})

    def test_sibling_declaration_order_does_not_matter(self):
        """同批里「谁先声明」不能影响结果：consumer 写在 producer 前面也要成立。"""
        self.engine.expand("t", "root", [
            self._spec("consumer", consumes=["Y"]),
            self._spec("producer", produces=["Y"]),
        ])
        self.assertEqual(self._tree()["nodes"]["consumer"]["consumes"], ["Y"])

    # ── 声明非法必须报错（不是静默丢弃）───────────────────────────────────

    def test_declared_dangling_requires_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.expand("t", "root", [self._spec("E", requires=["没有这个"])])
        self.assertIn("requires 悬空", str(ctx.exception))

    def test_declared_self_requires_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.expand("t", "root", [self._spec("F", requires=["F"])])
        self.assertIn("不能依赖自身", str(ctx.exception))

    def test_declared_cycle_within_one_batch_is_rejected(self):
        """同批两条声明互相成环 —— 必须在**建齐节点后**的接线阶段拦住。"""
        with self.assertRaises(ValueError) as ctx:
            self.engine.expand("t", "root", [self._spec("G", requires=["H"]),
                                              self._spec("H", requires=["G"])])
        self.assertIn("成环", str(ctx.exception))

    def test_declared_unregistered_consumes_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.expand("t", "root", [self._spec("L", consumes=["没登记"])])
        self.assertIn("未登记资产", str(ctx.exception))

    def test_declared_dangling_relates_is_rejected(self):
        """`relates` 是软边、可成环，但**不能悬空**。

        为何单独要这一条：`new_node` 会把 `relates` 写进节点（所以「字段存在」不需要接线代码），
        接线代码对 `relates` 的**唯一**贡献就是这个悬空校验。不测它，
        接线函数整个被删也测不出来（变异测试实测：这一条漏抓过一次）。
        """
        with self.assertRaises(ValueError) as ctx:
            self.engine.expand("t", "root", [self._spec("N", relates=["没有这个"])])
        self.assertIn("relates 悬空", str(ctx.exception))

    def test_rejected_declaration_writes_nothing(self):
        before = self._events()
        for _ in range(2):
            with self.assertRaises(ValueError):
                self.engine.expand("t", "root", [self._spec("E", requires=["没有这个"])])
        self.assertEqual(self._events(), before)
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)

    def test_declared_edges_are_idempotent_with_the_verbs(self):
        """声明与动词两条路径不冲突（重复不产生重复边）。"""
        self.engine.expand("t", "root", [self._spec("A"), self._spec("B", requires=["A"])])
        self.engine.require("t", "B", "A")
        self.assertEqual(self._tree()["nodes"]["B"]["requires"], ["A"])


if __name__ == "__main__":
    unittest.main()
