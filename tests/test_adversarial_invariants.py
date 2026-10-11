"""对抗判据 · lane a —— 量化 / 迭代不变量（常跑面固化）。

来源：已删除的 `scripts/independent_checks.py`（HEAD 70969fc^）里 lane a 的对抗用例
（原 `disflow_gap3_verifyA.py` 的合入版）。逐条搬进 `unittest discover` 常跑面：每条原始
判据 → 一个用例，用例名带原 id（`a1`/`a1b`/…/`a9c`），docstring 保留原始中文判据原文，
断言**同一个可观测量**，不是「能跑过」。

设置沿用仓内约定（`tests/base.py::DisflowTestCase`）：每个用例独立临时 state dir、退出清理。
需要真 git 仓 / Engine 的用例（A6 / A6b）把临时仓搬进 `setUp`，不依赖进程 cwd 之外的现场。
"""
from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from typing import Any

from base import DisflowTestCase, git_repo

from disflow import store
from disflow.metrics import (
    NO_START_REASON,
    burndown,
    parse_deadline,
    summary,
    tree_start_ts,
)
from disflow.model import validate_points


def _iso(year: int, month: int, day: int) -> str:
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat()


def _ep(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, tzinfo=timezone.utc).timestamp()


def _ev(kind: str, ts: str, slug: str = "t", node: str | None = None) -> dict:
    event = {"kind": kind, "slug": slug, "ts": ts}
    if node:
        event["node"] = node
    return event


def _node(node_id: str, status: str = "pending", points: float | None = None,
          kind: str = "task") -> dict:
    return {"id": node_id, "kind": kind, "status": status, "points": points}


class AdversarialMetricsInvariantsTest(DisflowTestCase):
    """纯函数判据（A1–A5b、A7–A9c）：手搓事件序列钉死数值，不读时钟、不建仓。"""

    def test_adv_a1_invalid_points_all_rejected(self):
        """A1 非法点数全部拒绝（NaN/±inf/bool/str/0/负数）"""
        bad: dict[str, object] = {"NaN": float("nan"), "+inf": float("inf"),
                                  "-inf": float("-inf"), "bool True": True, "bool False": False,
                                  "str '3'": "3", "0": 0, "-1": -1, "负数 -0.5": -0.5}
        observed: dict[str, str] = {}
        for label, value in bad.items():
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    validate_points(value)
                observed[label] = "拒绝(ValueError)"
        self.assertEqual(set(observed), set(bad),
                         f"每条非法值都必须被 ValueError 拒：{json.dumps(observed, ensure_ascii=False)}")

    def test_adv_a1b_valid_points_normalized_to_float(self):
        """A1b 合法点数归一为 float"""
        got = {v: validate_points(v) for v in (3, 1.5, 0.5)}
        self.assertEqual(got, {3: 3.0, 1.5: 1.5, 0.5: 0.5})

    def test_adv_a1c_none_is_unestimated(self):
        """A1c None 合法（未估）"""
        self.assertIsNone(validate_points(None))

    def test_adv_a2_unestimated_not_in_denominator(self):
        """A2 未估节点不进分母（不读成『不用干』）"""
        tree = {"slug": "t", "deadline": None,
                "nodes": {"a": _node("a", "done", 3.0), "b": _node("b", "pending"),
                          "c": _node("c", "pending")}}
        events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 1), node="a")]
        s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 11))
        self.assertEqual(s["points_total"], 3.0)
        self.assertEqual(s["points_done"], 3.0)
        self.assertEqual(s["points_unestimated"], 2)
        self.assertEqual(s["points_left"], 0.0)

    def test_adv_a3_container_not_quantified(self):
        """A3 container 不量化"""
        tree = {"slug": "t", "deadline": None,
                "nodes": {"g": _node("g", "pending", 99.0, kind="container"),
                          "a": _node("a", "pending", 2.0)}}
        s = summary(tree, [_ev("tree.init", _iso(2026, 1, 1))], slug="t", now_ts=_ep(2026, 1, 2))
        self.assertEqual(s["points_total"], 2.0, "container 的 99 应被忽略")

    def test_adv_a4_as_of_summary_left_equals_burndown_last_row(self):
        """A4 as-of 一致：summary.points_left == burndown 末行 remaining"""
        tree = {"slug": "t", "deadline": "2026-02-01",
                "nodes": {"a": _node("a", "done", 5.0), "b": _node("b", "pending", 5.0)}}
        events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 10), node="a")]
        for label, now in (("截点在完工日之前", _ep(2026, 1, 5)), ("截点在完工日之后", _ep(2026, 1, 20))):
            with self.subTest(label=label):
                su = summary(tree, events, slug="t", now_ts=now)
                bu = burndown(tree, events, slug="t", now_ts=now)
                self.assertEqual(su["points_left"], bu["series"][-1]["remaining"],
                                 f"{label}：summary.points_left={su['points_left']} "
                                 f"burndown.末行.remaining={bu['series'][-1]['remaining']}")

    def test_adv_a5_parse_deadline_normalization(self):
        """A5 parse_deadline 合法/空值归一"""
        self.assertEqual(parse_deadline("2026-01-01"), "2026-01-01")
        self.assertEqual(parse_deadline(" 2026-01-01 "), "2026-01-01")
        self.assertIsNone(parse_deadline(None))
        self.assertIsNone(parse_deadline(""))
        self.assertIsNone(parse_deadline("-"))

    def test_adv_a5b_parse_deadline_rejects_noncanonical(self):
        """A5b parse_deadline 非规范日期拒绝"""
        for value in ("2026-1-1", "2026-02-30", "01/01/2026"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_deadline(value)

    def test_adv_a7_summary_is_pure_and_does_not_mutate_inputs(self):
        """A7 纯函数：两次调用同值且不改入参"""
        tree = {"slug": "t", "deadline": "2026-03-01",
                "nodes": {"a": _node("a", "done", 3.0), "b": _node("b", "pending")}}
        events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 5), node="a")]
        tree_before, events_before = copy.deepcopy(tree), copy.deepcopy(events)
        r1 = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
        r2 = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
        self.assertEqual(r1, r2)
        self.assertEqual(tree, tree_before)
        self.assertEqual(events, events_before)

    def test_adv_a8_tree_start_ts_slug_boundary(self):
        """A8 tree_start_ts 的 slug 边界"""
        events = [_ev("tree.init", _iso(2026, 1, 1))]
        none_slug: Any = None  # 契约是 str，但边界必须实测 None（运行期容忍、不能崩）
        got = {"nope": tree_start_ts(events, slug="nope"), "空": tree_start_ts(events, slug=""),
               "空白": tree_start_ts(events, slug="   "), "None": tree_start_ts(events, slug=none_slug)}
        self.assertEqual(got, {"nope": None, "空": None, "空白": None, "None": None},
                         json.dumps(got, ensure_ascii=False))

    def test_adv_a8b_no_tree_init_gives_explicit_reason(self):
        """A8b 无 tree.init 时给显式理由（不静默）"""
        tree = {"slug": "t", "deadline": "2026-03-01",
                "nodes": {"a": _node("a", "done", 3.0), "b": _node("b", "pending")}}
        s = summary(tree, [], slug="t", now_ts=_ep(2026, 1, 10))
        self.assertEqual(s["start_reason"], NO_START_REASON)
        self.assertIsNone(s["start_ts"])

    def test_adv_a9_deadline_single_source_tree_field_only(self):
        """A9 deadline 单一入口：只认 tree['deadline']（事件不构成第二事实源）"""
        tree = {"slug": "t", "deadline": None, "nodes": {"a": _node("a", "pending", 1.0)}}
        events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("tree.deadline", _iso(2026, 1, 1))]
        s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
        self.assertIsNone(s["deadline"])
        self.assertIsNone(s["deadline_reason"],
                          "有 tree.deadline 事件但 tree 无字段 → 事件未成事实源")

    def test_adv_a9c_invalid_stored_deadline_reason_not_silent(self):
        """A9c 树里存了非法截止日 → summary+burndown 都显式给理由"""
        events = [_ev("tree.init", _iso(2026, 1, 1))]
        for badval in ("2026-13-01", "不是日期", "2026/01/01"):
            with self.subTest(badval=badval):
                tree = {"slug": "t", "deadline": badval, "nodes": {"a": _node("a", "pending", 1.0)}}
                s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
                b = burndown(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
                self.assertIsNone(s["deadline"])
                self.assertTrue(s["deadline_reason"], "summary 必须显式给理由")
                self.assertIsNone(b["ideal"])
                self.assertTrue(b["ideal_reason"], "burndown 必须显式给理由")


class AdversarialOverdueVerbTest(DisflowTestCase):
    """A6 / A6b：逾期截止日不是闸门，但必须被显式报告。需真 git 仓 + Engine。

    临时 git 仓在 `setUp` 建（`git_repo` 已用仓外 `tempfile.mkdtemp`，不依赖进程 cwd）。
    """

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hi\n"}, commit=True)

    def test_adv_a6_overdue_deadline_blocks_no_verb(self):
        """A6 deadline 逾期不拒任何动词（它不是闸门）"""
        eng = self.engine
        eng.init_tree("t", "g")
        eng.deadline("t", "2020-01-01")              # 早于今天 → 必然逾期
        acc = {"cmd": "true", "expect": "exit0"}
        eng.expand("t", "root", [{"id": "a", "acceptance": dict(acc)},
                                 {"id": "b", "points": 2, "acceptance": dict(acc)}])
        steps: dict[str, str] = {}
        actions = (("start", lambda: eng.start("t", "a")),
                   ("skip", lambda: eng.skip("t", "a", "no need")),
                   ("start b", lambda: eng.start("t", "b")),
                   ("done b", lambda: eng.done("t", "b", {"note": "x"})))
        for label, fn in actions:
            try:
                fn()
                steps[label] = "放行"
            except Exception as exc:  # noqa: BLE001 - 判据就是「必须放行」
                steps[label] = f"拒绝 {type(exc).__name__}: {exc}"
        self.assertEqual(steps, {label: "放行" for label, _ in actions},
                         json.dumps(steps, ensure_ascii=False))

    def test_adv_a6b_overdue_deadline_reported_not_silent(self):
        """A6b 逾期被显式报告（不静默）"""
        eng = self.engine
        eng.init_tree("t", "g")
        eng.deadline("t", "2020-01-01")
        eng.expand("t", "root", [{"id": "a", "acceptance": {"cmd": "true", "expect": "exit0"}}])
        s = summary(eng.tree("t"), store.load_events(eng.storage), slug="t", now_ts=_ep(2026, 6, 1))
        self.assertGreater(s["deadline_overdue_days"], 0)
        self.assertEqual(s["deadline"], "2020-01-01")


if __name__ == "__main__":
    unittest.main()
