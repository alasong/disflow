"""CLI 装配层测试（C1）：建树 / 建图类动词。

**为什么单独有这个文件**：`tests/test_cli.py` 只覆盖了 26 个 `cmd_*` 中的 1 个——
V3 独立验证在 `cmd_close` 上抓到真缺陷（漏传 `dry_run`），说明装配层是薄弱面。
本文件按动词分组覆盖：**每个动词至少验证「接线通了」+「非法输入被拒」两面**。

隔离：`DisflowTestCase` 给每个用例独立 `DISFLOW_STATE_DIR`。
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import spec, store
from disflow.cli import main


class CliBuildVerbTest(DisflowTestCase):
    """init / goal / expand / require / relate / produce / consume / context"""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _state(self) -> dict:
        return store.read_json(store.projection_path(spec.SPEC))

    def _tree(self, tree: str = "t") -> dict:
        return self._state()["trees"][tree]

    def _node(self, node: str, tree: str = "t") -> dict:
        return self._tree(tree)["nodes"][node]

    # ── init / goal ───────────────────────────────────────────────────────

    def test_init_creates_tree_with_goal(self):
        code, _out, _err = self._run(["init", "t", "--goal", "目标一"])
        self.assertEqual(code, 0)
        self.assertEqual(self._tree()["goal"], "目标一")
        self.assertEqual(self._tree()["root"], "root")

    def test_goal_updates_existing_tree(self):
        self._run(["init", "t", "--goal", "旧"])
        code, _out, _err = self._run(["goal", "t", "新"])
        self.assertEqual(code, 0)
        self.assertEqual(self._tree()["goal"], "新")

    def test_init_twice_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, err = self._run(["init", "t", "--goal", "g"])
        self.assertNotEqual(code, 0, "同名树重复 init 必须被拒")
        self.assertIn("✗", err)

    # ── expand ────────────────────────────────────────────────────────────

    def test_expand_creates_children_with_declared_fields(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, _err = self._run(["expand", "t", "root", "--children",
            '[{"id":"a","goal":"干活","stage":"verify","role":"qa",'
            '"touch_plan":["src/a.py"],"awaits":["other"]}]'])
        self.assertEqual(code, 0)
        node = self._node("a")
        self.assertEqual(node["goal"], "干活")
        self.assertEqual(node["stage"], "verify")
        self.assertEqual(node["role"], "qa")
        self.assertEqual(node["touch_plan"], ["src/a.py"])
        self.assertEqual(node["awaits"], ["other"])

    def test_expand_bad_json_reports_error_not_traceback(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, err = self._run(["expand", "t", "root", "--children", "{不是json}"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err)
        self.assertNotIn("Traceback", err)

    # ── require / relate ──────────────────────────────────────────────────

    def test_require_records_dependency_edge(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        code, _out, _err = self._run(["require", "t", "b", "a"])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("b")["requires"], ["a"])

    def test_require_rejects_cycle(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self._run(["require", "t", "b", "a"])
        code, _out, err = self._run(["require", "t", "a", "b"])
        self.assertNotEqual(code, 0, "成环必须写时拒收")
        self.assertIn("环", err)

    def test_require_self_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"a","goal":"A"}]'])
        code, _out, _err = self._run(["require", "t", "a", "a"])
        self.assertNotEqual(code, 0)

    def test_relate_is_soft_and_may_cycle(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self._run(["relate", "t", "a", "b"])
        code, _out, _err = self._run(["relate", "t", "b", "a"])   # 软引用可成环
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["relates"], ["b"])
        self.assertEqual(self._node("b")["relates"], ["a"])

    # ── produce / consume ─────────────────────────────────────────────────

    def test_produce_then_consume_registers_asset(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self.assertEqual(self._run(["produce", "t", "a", "schema"])[0], 0)
        self.assertEqual(self._run(["consume", "t", "b", "schema"])[0], 0)
        self.assertEqual(self._tree()["assets"]["schema"], "a")
        self.assertEqual(self._node("b")["consumes"], ["schema"])

    def test_consume_unregistered_asset_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"b","goal":"B"}]'])
        code, _out, err = self._run(["consume", "t", "b", "不存在的东西"])
        self.assertNotEqual(code, 0, "消费未登记产出必须被拒（FR-10）")
        self.assertIn("✗", err)

    # ── context ───────────────────────────────────────────────────────────

    def test_context_merge_is_cumulative(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"a","goal":"A"}]'])
        self._run(["context", "t", "a", "--set", '{"k": 1, "背景": "x"}'])
        code, _out, _err = self._run(["context", "t", "a", "--set", '{"j": 2}'])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["context"], {"k": 1, "背景": "x", "j": 2})


if __name__ == "__main__":
    unittest.main()
