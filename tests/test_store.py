"""存储层测试：事件流 → 投影、重建一致、坏行报错。"""
from __future__ import annotations

import os
import pathlib
import unittest

from base import DisflowTestCase

from disflow import spec, state, store


class StoreTest(DisflowTestCase):
    def test_append_event_then_projection(self):
        store.append_event(spec.SPEC, "tree.init", project=state.project,
                           slug="t1", extra={"goal": "目标"})
        projection = store.read_json(store.projection_path(spec.SPEC))
        self.assertEqual(projection["trees"]["t1"]["goal"], "目标")
        self.assertEqual(projection["trees"]["t1"]["root"], "root")

    def test_rebuild_matches_projection(self):
        store.append_event(spec.SPEC, "tree.init", project=state.project,
                           slug="t1", extra={"goal": "g"})
        matched, diffs = store.projection_matches(spec.SPEC, project=state.project)
        self.assertTrue(matched, diffs)

    def test_events_are_append_only_and_replayable(self):
        for i in range(3):
            store.append_event(spec.SPEC, "tree.goal", project=state.project,
                               slug="t1", extra={"goal": f"g{i}"})
        events = store.load_events(spec.SPEC)
        self.assertEqual(len(events), 3)
        self.assertEqual(state.project(events)["trees"]["t1"]["goal"], "g2")

    def test_bad_jsonl_line_raises(self):
        path = store.events_path(spec.SPEC)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json}\n")
        with self.assertRaises(ValueError):
            store.load_events(spec.SPEC)

    def test_unknown_event_kind_raises(self):
        with self.assertRaises(ValueError):
            state.project([{"kind": "bogus.kind", "slug": "t1"}])

    # ── 回归：被拒的事件不许落盘 ──────────────────────────────────────────

    def _events(self) -> list[dict]:
        return store.read_jsonl(store.events_path(spec.SPEC))

    def test_semantically_rejected_event_never_lands_in_log(self):
        """回归（真缺陷）：`_persist` 曾**先落盘再重投影**，于是语义非法（重复 id）
        的事件被拒后**仍留在事件流里** —— 此后任何重投影（下一次写 / rebuild）都失败，
        即「一次非法写入 = 状态永久不可恢复」。修法：先试算（project）作语义闸门，再落盘。
        """
        store.append_event(spec.SPEC, "tree.init", project=state.project,
                           slug="t", extra={"goal": "g"})
        store.append_event(spec.SPEC, "node.expand", project=state.project,
                           slug="t", node="root",
                           extra={"children": [{"id": "a", "goal": "A"}]})
        before = self._events()

        with self.assertRaises(ValueError):          # 重复 id：语义校验在 project 里抛
            store.append_event(spec.SPEC, "node.expand", project=state.project,
                               slug="t", node="root",
                               extra={"children": [{"id": "a", "goal": "重复"}]})

        self.assertEqual(len(self._events()), len(before), "被拒事件不许落盘")
        # 关键后果断言：事件流仍然可重放（旧写法下这里会抛）
        recomputed = store.rebuild(spec.SPEC, project=state.project)
        self.assertEqual(sorted(recomputed["trees"]["t"]["nodes"]), ["a", "root"])
        self.assertEqual(store.projection_matches(spec.SPEC, project=state.project)[0], True)

    def test_disk_unchanged_after_rejection(self):
        """被拒时事件文件**字节级**不变（不只是条数不变）。"""
        store.append_event(spec.SPEC, "tree.init", project=state.project,
                           slug="t", extra={"goal": "g"})
        path = store.events_path(spec.SPEC)
        raw = pathlib.Path(path).read_bytes()
        with self.assertRaises(ValueError):
            store.append_event(spec.SPEC, "node.require", project=state.project,
                               slug="t", node="不存在", extra={"dep": "x"})
        self.assertEqual(pathlib.Path(path).read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
