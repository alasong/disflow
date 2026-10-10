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


class BatchAppendTest(DisflowTestCase):
    """`batch_append`：批内逐条 append、批末**恰好一次**写投影。

    **为何直到现在才补测**：它当前**零调用方**（`tree.batch_phase` 那套批处理未落地），
    于是 285 个测试、变异测试、逐文件 LSP 探针全部没碰到它——里面藏了一个**真实 `NameError`**
    （返回行引用了未定义的 `offset`）。它是全仓 pyright 扫出来的。
    讽刺的是它自己的 docstring 写着“仍采用先试算后落盘语义，**以免成为哑雷**”。
    **死路径 + 零测试 = 哑雷**；这条教训写进 `docs/dev-board.md` §0.11。
    """

    def _batch(self, kinds: list[tuple[str, str, dict]]) -> dict:
        records = [store.build_event(kind, slug=slug, extra=extra)
                   for kind, slug, extra in kinds]
        return store.batch_append(spec.SPEC, records, project=state.project)

    def test_returns_the_batch_end_offset(self):
        """**回归**：返回行曾用未定义的 `offset` ⇒ 任何调用方都拿不到结果（`NameError`）。

        断言的是**返回值与盘上现实一致**，而不只是“不报错”。
        """
        result = self._batch([("tree.init", "t", {"goal": "g"}),
                              ("tree.goal", "t", {"goal": "g2"})])
        self.assertEqual(result["appended"], 2)
        self.assertEqual(result["projection_offset"], len(store.load_events(spec.SPEC)))
        self.assertEqual(store.read_projection_offset(spec.SPEC), result["projection_offset"])

    def test_projection_reflects_the_whole_batch(self):
        self._batch([("tree.init", "t", {"goal": "g"}),
                     ("tree.goal", "t", {"goal": "新目标"})])
        self.assertEqual(store.read_json(store.projection_path(spec.SPEC))["trees"]["t"]["goal"],
                         "新目标")
        matched, diffs = store.projection_matches(spec.SPEC, project=state.project)
        self.assertTrue(matched, diffs)

    def test_empty_batch_is_a_noop_that_still_reports_truthfully(self):
        self._batch([("tree.init", "t", {"goal": "g"})])
        before = len(store.load_events(spec.SPEC))
        result = store.batch_append(spec.SPEC, [], project=state.project)
        self.assertEqual(result["appended"], 0)
        self.assertEqual(result["projection_offset"], before)
        self.assertEqual(len(store.load_events(spec.SPEC)), before)

    def test_semantically_illegal_batch_lands_nothing(self):
        """整批试算失败 ⇒ **一条都不落**（与 `_persist` 同款的先试算后落盘）。

        用未知 kind：它无字段模板（`_validate_record` 放行），靠 `project` 里的语义闸门拦下。
        """
        self._batch([("tree.init", "t", {"goal": "g"})])
        events_before = len(store.load_events(spec.SPEC))
        proj_path = store.projection_path(spec.SPEC)
        raw_before = pathlib.Path(proj_path).read_bytes()
        records = [store.build_event("tree.goal", slug="t", extra={"goal": "g2"}),
                   store.build_event("node.不存在", slug="t")]
        with self.assertRaises(ValueError):
            store.batch_append(spec.SPEC, records, project=state.project)
        self.assertEqual(len(store.load_events(spec.SPEC)), events_before,
                         "被拒的批次不许落任何一条")
        self.assertEqual(pathlib.Path(proj_path).read_bytes(), raw_before,
                         "投影也要字节级不变")
        matched, diffs = store.projection_matches(spec.SPEC, project=state.project)
        self.assertTrue(matched, diffs)

    def test_the_returned_offset_agrees_with_disk_after_a_second_batch(self):
        """第二批的偏移必须接着第一批算（不是从 0 重来）。"""
        first = self._batch([("tree.init", "t", {"goal": "g"})])
        second = self._batch([("tree.goal", "t", {"goal": "g2"}),
                              ("tree.goal", "t", {"goal": "g3"})])
        self.assertEqual(second["projection_offset"], len(store.load_events(spec.SPEC)))
        self.assertGreater(second["projection_offset"], first["projection_offset"])


if __name__ == "__main__":
    unittest.main()
