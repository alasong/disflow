"""视图层测试（D80）：三视图同源、渲染正确。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine
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

    def test_board_stage_filter(self):
        lines = board_lines(self.tree, stage="verify")
        self.assertIn("[verify] 0/1", lines)
        self.assertFalse(any(line.startswith("[dev]") for line in lines))

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
