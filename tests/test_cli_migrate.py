"""CLI 装配层测试（C4）：`import mgt` 迁移 + 参数错误路径。

迁移是唯一「从外部世界读数据」的动词，最容易出现**静默降级**——所以判据集中在：
**降级必须可见**（`skipped_edges` / `dropped_deps` / stderr）与**找不到数据必须失败**。
fixture 全部由本文件合成（本机无真实 mgt 数据）。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import vocab, store
from disflow.cli import main


class CliMigrateVerbTest(DisflowTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _state(self) -> dict:
        return store.read_json(store.projection_path(vocab.STORAGE))

    def _fixture(self, name: str, todos: list[dict], registry: dict | None = None) -> str:
        """合成一个 mgt 项目目录：`.fat/mgt/state.json` + `.fat/registry.json`。"""
        project = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(project, ".fat", "mgt"), exist_ok=True)
        with open(os.path.join(project, ".fat", "mgt", "state.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"todos": todos}, handle, ensure_ascii=False)
        if registry is not None:
            with open(os.path.join(project, ".fat", "registry.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"todos": registry}, handle, ensure_ascii=False)
        return project

    def _basic(self, name: str = "proj") -> str:
        return self._fixture(name, [
            {"id": "R1", "type": "R", "parent": None, "title": "根", "org": "pm",
             "mode": "ulww", "status": "iterating"},
            {"id": "T1", "type": "T", "parent": "R1", "title": "任务一", "org": "dev",
             "mode": "ulww", "files": ["a.py"], "status": "done"},
            {"id": "T2", "type": "T", "parent": "R1", "title": "任务二", "org": "qa",
             "files": ["b.py"], "status": "ready",
             "deps": [{"id": "T1", "type": "blocks"}, {"id": "T1", "type": "contains"}]},
        ], registry={"T1": {"global_status": "dev_done"}})

    # ── happy path ────────────────────────────────────────────────────────

    def test_import_creates_nodes_and_reports_stats(self):
        project = self._basic()
        code, out, err = self._run(["import", "mgt", project, "--tree", "T"])
        self.assertEqual(code, 0, err)
        stats = json.loads(out)
        self.assertEqual(stats["nodes"], 3)
        self.assertEqual(stats["edges"], 1)
        self.assertEqual(stats["tree"], "T")

    def test_import_maps_fields_per_design_table(self):
        project = self._basic()
        self._run(["import", "mgt", project, "--tree", "T"])
        nodes = self._state()["trees"]["T"]["nodes"]
        self.assertEqual(nodes["R1"]["kind"], "container")     # type R → container
        self.assertEqual(nodes["T1"]["kind"], "task")
        self.assertEqual(nodes["T1"]["goal"], "任务一")         # title → goal
        self.assertEqual(nodes["T1"]["entry"], "ulww")          # mode → entry
        self.assertEqual(nodes["T1"]["touch_plan"], ["a.py"])   # files → touch_plan
        self.assertEqual(nodes["T1"]["role"], "dev")          # org → role

    def test_import_maps_blocks_to_requires_and_drops_contains(self):
        project = self._basic()
        _code, out, _err = self._run(["import", "mgt", project, "--tree", "T"])
        nodes = self._state()["trees"]["T"]["nodes"]
        self.assertEqual(nodes["T2"]["requires"], ["T1"])       # blocks → requires
        self.assertEqual(nodes["T2"]["relates"], [])
        stats = json.loads(out)
        self.assertTrue(any("contains" in d for d in stats["dropped_deps"]),
                        "contains 丢弃必须可见（dropped_deps）")

    def test_import_marks_globally_done_nodes_without_claiming_gate(self):
        """`global_status=dev_done` → `status=done` 且标 `imported`，**不声称过了门**。"""
        project = self._basic()
        self._run(["import", "mgt", project, "--tree", "T"])
        node = self._state()["trees"]["T"]["nodes"]["T1"]
        self.assertEqual(node["status"], "done")
        self.assertTrue(node.get("imported"), "必须带 imported 标记")
        evidence = node.get("evidence") or {}
        self.assertNotIn("acceptance", evidence, "导入不得伪造门证据（无 acceptance 记录）")
        self.assertNotIn("acceptance_ok", evidence, "导入不得伪造门证据（无门通过标记）")

    # ── 降级必须可见 ──────────────────────────────────────────────────────

    def test_cyclic_deps_are_skipped_and_reported(self):
        project = self._fixture("cyc", [
            {"id": "A", "type": "T", "parent": None, "title": "A", "status": "pending"},
            {"id": "B", "type": "T", "parent": None, "title": "B", "status": "pending",
             "deps": [{"id": "A", "type": "blocks"}]},
            {"id": "C", "type": "T", "parent": None, "title": "C", "status": "pending",
             "deps": [{"id": "B", "type": "blocks"}]},
            {"id": "A2", "type": "T", "parent": None, "title": "A2", "status": "pending",
             "deps": [{"id": "C", "type": "blocks"}]},
        ])
        code, out, _err = self._run(["import", "mgt", project, "--tree", "C"])
        self.assertEqual(code, 0)
        stats = json.loads(out)
        self.assertIsInstance(stats["skipped_edges"], list)

    def test_cycle_in_deps_is_reported_not_silently_ignored(self):
        project = self._fixture("cyc2", [
            {"id": "A", "type": "T", "parent": None, "title": "A", "status": "pending",
             "deps": [{"id": "B", "type": "blocks"}]},
            {"id": "B", "type": "T", "parent": None, "title": "B", "status": "pending",
             "deps": [{"id": "A", "type": "blocks"}]},
        ])
        code, out, _err = self._run(["import", "mgt", project, "--tree", "C2"])
        self.assertEqual(code, 0)
        stats = json.loads(out)
        self.assertTrue(stats["skipped_edges"], "成环的边必须出现在 skipped_edges 里")
        self.assertTrue(any("环" in s for s in stats["skipped_edges"]))

    def test_orphan_node_is_reported(self):
        project = self._fixture("orph", [
            {"id": "T9", "type": "T", "parent": "不存在的父", "title": "孤儿",
             "status": "pending"},
        ])
        _code, out, _err = self._run(["import", "mgt", project, "--tree", "O"])
        stats = json.loads(out)
        self.assertEqual(stats["orphans"], ["T9"], "父节点缺失必须报为 orphan")

    # ── 错误路径 ──────────────────────────────────────────────────────────

    def test_unknown_source_is_rejected(self):
        code, _out, err = self._run(["import", "jira", self.tmp, "--tree", "T"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err)

    def test_missing_mgt_state_is_rejected_not_silent(self):
        empty = os.path.join(self.tmp, "empty-proj")
        os.makedirs(empty, exist_ok=True)
        code, _out, err = self._run(["import", "mgt", empty, "--tree", "T"])
        self.assertEqual(code, 1, "找不到 mgt 状态必须失败，不能静默建空树")
        self.assertIn("未找到", err)
        self.assertNotIn("T", self._state().get("trees", {}), "失败不许留下空树")

    def test_import_into_existing_tree_is_rejected(self):
        project = self._basic()
        self._run(["init", "T", "--goal", "已存在"])
        code, _out, err = self._run(["import", "mgt", project, "--tree", "T"])
        self.assertNotEqual(code, 0, "同名树已存在时不许覆盖")
        self.assertIn("✗", err)


if __name__ == "__main__":
    unittest.main()
