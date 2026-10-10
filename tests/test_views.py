"""视图层测试（D80）：三视图同源、渲染正确；看板列 = 配置的四个开发阶段。

看板列的判据（2026-10-11 用户裁决：板上 需求/设计/开发/验证 四列）：
1. **空列也出现**（`[design] 0/0`）——只打出现过的列时，读者看不出这是开发流程，
   也看不出「某阶段为空」这个事实；
2. **列顺序 == 配置顺序**（配置是唯一来源，视图层不抄字面量）；
3. **非规范 stage 追加而不丢**（旧版本/别的配置写下的历史值仍要看得见，只标注不隐藏）；
4. **`--stage` 过滤不变**（只输出被选中的那一列）；
5. **`stage=done` 被拒收**（`done` 是节点**状态**，不再是阶段——闭集只改了**合法值集合**，
   与 `_STAGE_FROZEN`（终态不许改列，那是 status 轴）**无关**）。
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow import store
from disflow.engine import Engine, EngineError
from disflow.spec import STAGES
from disflow.views import board_lines, graph_edges, graph_lines, to_dot, tree_lines


class ViewsTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "acceptance": acceptance()},
            {"id": "b", "stage": "verify", "goal": "验证 B", "role": "qa"},
        ])
        self.engine.require("t", "b", "a")
        self.engine.relate("t", "b", "a")
        self.tree = self.engine.tree("t")

    def test_board_groups_by_stage(self):
        lines = board_lines(self.tree)
        self.assertIn("[dev] 0/1", lines)
        self.assertIn("[verify] 0/1", lines)
        self.assertTrue(any("a" in line and "做 A" in line for line in lines))

    def test_board_shows_every_configured_column_even_when_empty(self):
        """固定列：需求/设计两列**没有节点也要出现**（0/0）——否则看不出流程与「设计为空」。"""
        lines = board_lines(self.tree)
        self.assertEqual([line for line in lines if line.startswith("[")],
                         ["[req] 0/0", "[design] 0/0", "[dev] 0/1", "[verify] 0/1"])

    def test_board_column_order_follows_the_configured_stages(self):
        """列顺序 == 配置顺序（含自定义配置）——视图层不与配置争主权。"""
        self.assertEqual(STAGES, ("req", "design", "dev", "verify"))
        lines = board_lines(self.tree, stages=("verify", "req", "design", "dev"))
        self.assertEqual([line for line in lines if line.startswith("[")],
                         ["[verify] 0/1", "[req] 0/0", "[design] 0/0", "[dev] 0/1"])

    def test_stage_done_is_not_a_column_and_is_rejected(self):
        """`done` 是**状态**不是列：默认闭集里没有它，写进去必须**响亮拒收且不落事件**。"""
        self.assertNotIn("done", STAGES)
        before = len(store.load_events(self.engine.spec))
        with self.assertRaises(EngineError) as ctx:
            self.engine.set_stage("t", "a", "done", reason="done 是状态不是列")
        self.assertIn("stage 取值非法", str(ctx.exception))
        self.assertEqual(len(store.load_events(self.engine.spec)), before, "拒收不许落事件")
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["stage"], "dev", "拒收不许改投影")

        with self.assertRaises(EngineError) as ctx_expand:
            self.engine.expand("t", "root", [{"id": "c", "stage": "done",
                                               "goal": "建节点也走同一套闭集"}])
        self.assertIn("stage 取值非法", str(ctx_expand.exception))
        self.assertEqual(len(store.load_events(self.engine.spec)), before, "拒收不许落事件")

    def test_non_canonical_stage_is_appended_and_marked(self):
        """不在闭集里的历史 stage **不许丢**：追加在末尾并标注（读时容忍 ≠ 静默隐藏）。"""
        tree = {"root": "root", "nodes": {
            "a": {"id": "a", "kind": "task", "stage": "dev", "goal": "做 A"},
            "f": {"id": "f", "kind": "task", "stage": "未来的列", "goal": "旧版本写的"},
        }}
        lines = board_lines(tree)
        headers = [line for line in lines if line.startswith("[")]
        self.assertEqual(headers, ["[req] 0/0", "[design] 0/0", "[dev] 0/1", "[verify] 0/0",
                                   "[未来的列] 0/1  ⚠ 非规范列（不在配置的 stages 内）"])
        self.assertTrue(any("f" in line and "旧版本写的" in line for line in lines),
                        "非规范列里的节点必须仍然看得见")

    def test_board_stage_filter(self):
        lines = board_lines(self.tree, stage="verify")
        self.assertIn("[verify] 0/1", lines)
        self.assertFalse(any(line.startswith("[dev]") for line in lines))

    def test_board_stage_filter_emits_only_the_selected_column(self):
        """`--stage` 过滤不变：只有被选中的那一列（其余列一个字符都不出现）。"""
        for wanted, header in (("dev", "[dev] 0/1"), ("verify", "[verify] 0/1")):
            with self.subTest(stage=wanted):
                lines = board_lines(self.tree, stage=wanted)
                self.assertEqual([line for line in lines if line.startswith("[")], [header])
        # 被选中的列没有节点时显式报 0（改动前什么都不打，与「列名拼错」分不开）
        self.assertEqual(board_lines(self.tree, stage="design"), ["[design] 0/0"])

    def test_tree_indents_by_parent(self):
        lines = tree_lines(self.tree)
        self.assertTrue(lines[0].startswith("◦ root"))
        self.assertTrue(any(line.startswith("  · a") for line in lines))

    def test_graph_edges_and_dot(self):
        edges = graph_edges(self.tree)
        self.assertIn(("b", "requires", "a"), edges)
        self.assertIn(("b", "relates", "a"), edges)
        self.assertTrue(any("b ─▶ a" in line for line in graph_lines(self.tree)))
        dot = to_dot(self.tree)
        self.assertTrue(dot.startswith("digraph"))
        self.assertIn('"b" -> "a"', dot)

    def test_views_are_same_projection(self):
        # 同一投影：三种视图各自渲染，节点集合一致
        nodes_in_tree = {line.split()[1] for line in tree_lines(self.tree)[1:]}
        nodes_in_board = {line.split()[1] for line in board_lines(self.tree)
                          if not line.startswith("[")}
        self.assertEqual(nodes_in_tree, {"a", "b"})
        self.assertEqual(nodes_in_board, {"a", "b"})


if __name__ == "__main__":
    unittest.main()
