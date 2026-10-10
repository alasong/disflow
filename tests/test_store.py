"""存储层测试：事件流 → 投影、重建一致、坏行报错。"""
from __future__ import annotations

import os
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


if __name__ == "__main__":
    unittest.main()
