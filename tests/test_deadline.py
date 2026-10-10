"""树级 `deadline`（迭代边界）：写时校验、事件重放、engine 动词。

deadline 是**迭代**的边界（`docs/design.md §9`：iteration = 有 deadline 的树）。
它必须落成事件（事件流是唯一真相），非法值**响亮拒绝且不落盘**——静默丢弃会让逾期永远发现不了。
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase

from disflow import spec, state, store
from disflow.engine import Engine
from disflow.metrics import parse_deadline
from disflow.state import project


class ParseDeadlineTest(DisflowTestCase):
    def test_none_empty_and_dash_mean_no_deadline(self):
        for blank in (None, "", "   ", "-"):
            self.assertIsNone(parse_deadline(blank), f"{blank!r} 应表示无 deadline")

    def test_valid_date_passes_through_unchanged(self):
        self.assertEqual(parse_deadline("2025-01-31"), "2025-01-31")
        self.assertEqual(parse_deadline("2030-12-01"), "2030-12-01")

    def test_surrounding_whitespace_is_stripped(self):
        """docstring 曾写「原样返回」但实现会 `strip()`——此处把实际行为钉住（FIX-A3）。"""
        self.assertEqual(parse_deadline(" 2025-01-31 "), "2025-01-31")
        self.assertEqual(parse_deadline("2025-01-31\n"), "2025-01-31")

    def test_invalid_date_raises_with_expected_format(self):
        for bad in ("not-a-date", "2025-13-01", "2025-1-1", "20250131", "31-01-2025"):
            with self.subTest(bad=bad), self.assertRaises(ValueError) as ctx:
                parse_deadline(bad)
            self.assertIn("YYYY-MM-DD", str(ctx.exception))


class EngineDeadlineTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "g")

    def test_deadline_is_stored_on_the_tree(self):
        self.engine.deadline("t", "2030-01-01")
        self.assertEqual(self.engine.tree("t")["deadline"], "2030-01-01")

    def test_none_clears_a_previous_deadline(self):
        self.engine.deadline("t", "2030-01-01")
        self.engine.deadline("t", None)
        self.assertIsNone(self.engine.tree("t")["deadline"])

    def test_invalid_deadline_is_rejected_loudly_and_does_not_write(self):
        """非法日期必须抛错且**一条事件都不落**（不是落一半、也不是静默当成 None）。"""
        before = len(store.load_events(spec.SPEC))
        with self.assertRaises(ValueError):
            self.engine.deadline("t", "2025-13-40")
        self.assertEqual(len(store.load_events(spec.SPEC)), before)
        self.assertIsNone(self.engine.tree("t")["deadline"])

    def test_deadline_survives_replay(self):
        self.engine.deadline("t", "2030-01-01")
        replayed = project(store.load_events(spec.SPEC))
        self.assertEqual(replayed["trees"]["t"]["deadline"], "2030-01-01")
        matched, diffs = store.projection_matches(spec.SPEC, project=project)
        self.assertTrue(matched, diffs)

    def test_kind_is_registered_in_both_declarations(self):
        """`spec.EVENT_KINDS` 与 `state._HANDLERS` 必须同步（结构性守卫，见 test_declarations）。"""
        self.assertIn("tree.deadline", spec.EVENT_KINDS)
        self.assertIn("tree.deadline", state._HANDLERS)


if __name__ == "__main__":
    unittest.main()
