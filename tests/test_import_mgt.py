"""mgt 导入器测试（D90）：映射正确、contains 丢弃、环/悬空不崩。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from base import DisflowTestCase

from disflow.engine import Engine
from disflow.import_mgt import apply_import, build_import, load_mgt


def write_mgt(root: str, todos: list[dict], registry: dict | None = None) -> None:
    dev = os.path.join(root, ".fat", "dev")
    os.makedirs(dev, exist_ok=True)
    with open(os.path.join(dev, "state.json"), "w", encoding="utf-8") as handle:
        json.dump({"todos": todos}, handle)
    if registry is not None:
        with open(os.path.join(root, ".fat", "registry.json"), "w", encoding="utf-8") as handle:
            json.dump(registry, handle)


class ImportMgtTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = tempfile.mkdtemp(prefix="mgt-repo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.engine = Engine(cwd=self.tmp)

    def _import(self, todos, registry=None, tree="m"):
        write_mgt(self.repo, todos, registry)
        plan = build_import(load_mgt(self.repo))
        stats = apply_import(self.engine, tree, plan)
        return self.engine.tree(tree), stats

    def test_mapping_and_edges(self):
        target, stats = self._import([
            {"id": "R1", "title": "需求", "type": "R"},
            {"id": "T1", "title": "做1", "type": "T", "parent": "R1", "status": "done",
             "files": ["a.py"], "org": "dev"},
            {"id": "T2", "title": "做2", "type": "T", "parent": "R1",
             "deps": [{"id": "T1", "type": "blocks"}]},
            {"id": "T3", "title": "做3", "type": "T",
             "deps": [{"id": "T1", "type": "informs"}]},
            {"id": "T4", "title": "做4", "type": "T",
             "deps": [{"id": "T2", "type": "contains"}]},
        ])
        nodes = target["nodes"]
        self.assertEqual(nodes["R1"]["kind"], "container")
        self.assertEqual(nodes["T1"]["parent"], "R1")
        self.assertEqual(nodes["T1"]["touch_plan"], ["a.py"])
        self.assertEqual(nodes["T1"]["status"], "done")
        self.assertTrue(nodes["T1"]["imported"])
        self.assertEqual(nodes["T2"]["requires"], ["T1"])
        self.assertEqual(nodes["T3"]["relates"], ["T1"])
        self.assertEqual(nodes["T4"]["requires"], [])          # contains 被丢弃
        self.assertTrue(any("T4" in d for d in stats["dropped_deps"]))

    def test_registry_global_status_marks_done(self):
        target, _ = self._import(
            [{"id": "T1", "title": "做1", "type": "T", "status": "pending"}],
            registry={"todos": {"T1": {"global_status": "approved"}}})
        self.assertEqual(target["nodes"]["T1"]["status"], "done")
        self.assertEqual(target["nodes"]["T1"]["stage"], "verify")

    def test_ready_equivalence_after_import(self):
        target, _ = self._import([
            {"id": "T1", "title": "做1", "type": "T", "status": "done"},
            {"id": "T2", "title": "做2", "type": "T",
             "deps": [{"id": "T1", "type": "blocks"}]},
            {"id": "T3", "title": "做3", "type": "T"},
        ])
        from disflow.ready import ready_tasks
        # T1 已 done，T2 解除阻塞；T3 无依赖 → 两者都就绪
        self.assertEqual(sorted(ready_tasks(target["nodes"])), ["T2", "T3"])

    def test_cycle_edge_skipped_not_crash(self):
        _target, stats = self._import([
            {"id": "T5", "title": "5", "type": "T", "deps": [{"id": "T6", "type": "blocks"}]},
            {"id": "T6", "title": "6", "type": "T", "deps": [{"id": "T5", "type": "blocks"}]},
        ])
        self.assertTrue(any("requires" in item for item in stats["skipped_edges"]))

    def test_dangling_dep_skipped(self):
        _target, stats = self._import([
            {"id": "T7", "title": "7", "type": "T", "deps": [{"id": "GHOST", "type": "blocks"}]},
        ])
        self.assertTrue(any("GHOST" in item for item in stats["skipped_edges"]))

    def test_load_mgt_reports_source(self):
        write_mgt(self.repo, [{"id": "T1", "title": "x", "type": "T"}])
        mgt = load_mgt(self.repo)
        self.assertEqual(len(mgt["todos"]), 1)
        self.assertTrue(mgt["source"].endswith("state.json"))


if __name__ == "__main__":
    unittest.main()
