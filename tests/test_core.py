"""纯函数层测试：model（树）/ graph（图）/ ready（就绪）/ context（上下文）。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase

from disflow import context, dispatch, graph, model, ready


def node(node_id, parent=None, *, kind="task", status="pending", requires=None,
         touch=None, ctx=None):
    item = model.new_node(node_id, parent=parent, kind=kind,
                          context=ctx, touch_plan=touch)
    item["status"] = status
    item["requires"] = list(requires or [])
    return item


class ModelTest(DisflowTestCase):
    def test_ancestor_chain_is_root_to_parent(self):
        nodes = {
            "r": node("r", None, kind="container"),
            "a": node("a", "r"),
            "b": node("b", "a"),
        }
        self.assertEqual([n["id"] for n in model.ancestor_chain(nodes, "b")], ["r", "a"])

    def test_ancestor_chain_detects_cycle(self):
        nodes = {"a": node("a", "b"), "b": node("b", "a")}
        with self.assertRaises(ValueError):
            model.ancestor_chain(nodes, "a")

    def test_container_done_derives_from_children(self):
        nodes = {
            "c": node("c", None, kind="container"),
            "t1": node("t1", "c", status="done"),
            "t2": node("t2", "c", status="done"),
        }
        self.assertTrue(model.is_container_done(nodes["c"], nodes))
        self.assertEqual(model.effective_status(nodes["c"], nodes), "done")
        nodes["t2"]["status"] = "pending"
        self.assertFalse(model.is_container_done(nodes["c"], nodes))

    def test_next_id_is_deterministic(self):
        nodes = {"n0": {}, "n2": {}}
        self.assertEqual(model.next_id(nodes), "n1")

    def test_validate_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            model.validate_node_spec({"goal": "x", "bogus": 1})


class GraphTest(DisflowTestCase):
    def test_would_cycle(self):
        nodes = {"a": node("a"), "b": node("b", requires=["a"])}
        self.assertTrue(graph.would_cycle(nodes, "a", "b"))
        self.assertFalse(graph.would_cycle(nodes, "b", "c") if "c" in nodes
                         else graph.would_cycle(nodes, "b", "a"))

    def test_find_cycles(self):
        nodes = {
            "a": node("a", requires=["b"]),
            "b": node("b", requires=["a"]),
            "c": node("c"),
        }
        cycles = graph.find_cycles(nodes)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(set(cycles[0]), {"a", "b"})

    def test_topo_waves_and_residual(self):
        nodes = {"a": node("a"), "b": node("b", requires=["a"]), "c": node("c")}
        waves, residual = graph.topo_waves(["a", "b", "c"], nodes)
        self.assertEqual(waves[0], ["a", "c"])
        self.assertEqual(waves[1], ["b"])
        self.assertEqual(residual, [])

    def test_unmet_requires_reports_dangling(self):
        nodes = {"a": node("a", requires=["ghost"])}
        self.assertEqual(graph.unmet_requires(nodes["a"], nodes), ["ghost"])


class ReadyTest(DisflowTestCase):
    def test_ready_requires_deps_done(self):
        nodes = {"a": node("a"), "b": node("b", requires=["a"])}
        self.assertTrue(ready.is_ready(nodes, "a"))
        self.assertFalse(ready.is_ready(nodes, "b"))
        nodes["a"]["status"] = "done"
        self.assertTrue(ready.is_ready(nodes, "b"))

    def test_container_never_ready(self):
        nodes = {"c": node("c", None, kind="container")}
        self.assertFalse(ready.is_ready(nodes, "c"))

    def test_lease_blocks_ready(self):
        nodes = {"a": node("a")}
        nodes["a"]["lease"] = {"holder": "w1"}
        self.assertFalse(ready.is_ready(nodes, "a"))

    def test_pack_waves_splits_on_touch_conflict(self):
        nodes = {
            "a": node("a", touch=["src/one.py"]),
            "b": node("b", touch=["src/one.py"]),
            "c": node("c", touch=["src/two.py"]),
        }
        waves = dispatch.pack_waves(nodes, ["a", "b", "c"])
        self.assertEqual(waves[0], ["a", "c"])
        self.assertEqual(waves[1], ["b"])

    def test_dir_prefix_counts_as_overlap(self):
        nodes = {"a": node("a", touch=["src"]), "b": node("b", touch=["src/x.py"])}
        waves = dispatch.pack_waves(nodes, ["a", "b"])
        self.assertEqual(len(waves), 2)

    def test_stuck_detection(self):
        nodes = {"a": node("a"), "b": node("b", requires=["a"])}
        nodes["a"]["status"] = "failed"
        self.assertTrue(ready.is_stuck(nodes))
        nodes["b"]["status"] = "done"
        self.assertFalse(ready.is_stuck(nodes))


class ContextTest(DisflowTestCase):
    def test_inherited_context_near_overrides_far(self):
        nodes = {
            "r": node("r", None, kind="container", ctx={"background": "系统", "shared": "root"}),
            "a": node("a", "r", ctx={"shared": "mid"}),
            "b": node("b", "a"),
        }
        merged = context.inherited_context(nodes, "b")
        self.assertEqual(merged["background"], "系统")
        self.assertEqual(merged["shared"], "mid")

    def test_brief_includes_done_prereq_summary(self):
        nodes = {
            "r": node("r", None, kind="container"),
            "a": node("a", "r", status="done"),
            "b": node("b", "r", requires=["a"]),
        }
        nodes["a"]["produces"] = ["thing"]
        nodes["a"]["goal"] = "造 thing"
        payload = context.brief(nodes, "b")
        self.assertEqual(payload["from_requires"][0]["id"], "a")
        self.assertEqual(payload["from_requires"][0]["produces"], ["thing"])


if __name__ == "__main__":
    unittest.main()
