"""量化字段 `points` 与指标层（summary / burndown / tree_start_ts）。

指标层是**纯函数**：时间只来自 `now_ts`，完工时刻只来自 `node.done` / `node.skip` 事件。
所以这里能用手搓事件序列把数值钉死——不依赖“跑一下今天的钟”。

统一语义 **as-of `now_ts`**：`summary.points_left` 必须等于 `burndown.series[-1].remaining`，
不论 `now_ts` 落在完工事件之前还是之后（对抗性用例见 AsOfConsistencyTest）。
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from base import DisflowTestCase, acceptance

from disflow import spec, state, store
from disflow.engine import Engine
from disflow.metrics import NO_START_REASON, burndown, summary, tree_start_ts
from disflow.model import DECLARABLE_FIELDS, new_node, validate_points


def _iso(year: int, month: int, day: int) -> str:
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat()


def _epoch(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, tzinfo=timezone.utc).timestamp()


def _event(kind: str, ts: str, *, slug: str = "t", node: str | None = None) -> dict:
    event = {"kind": kind, "slug": slug, "ts": ts}
    if node is not None:
        event["node"] = node
    return event


def _node(node_id: str, *, status: str = "pending", points: float | None = None,
          kind: str = "task") -> dict:
    return {"id": node_id, "kind": kind, "status": status, "points": points}


def _tree(*nodes: dict, deadline: str | None = None) -> dict:
    """投影树的形状（**不含 slug**——slug 是显式参数，不是投影字段）。"""
    return {"nodes": {node["id"]: node for node in nodes}, "deadline": deadline}


class PointsValidationTest(DisflowTestCase):
    def test_positive_numbers_accepted_and_normalized_to_float(self):
        self.assertEqual(validate_points(3), 3.0)
        self.assertEqual(validate_points(2.5), 2.5)
        self.assertIsInstance(validate_points(3), float, "归一为 float，避免 int/float 两套求和")

    def test_none_means_unestimated(self):
        self.assertIsNone(validate_points(None))

    def test_zero_negative_bool_and_str_are_rejected(self):
        """0/负无意义、（bool 是 int 子类会冒充 1 点）、字符串看着像数却错——一律响亮拒。"""
        for bad in (0, -1, -0.5, True, False, "3", "x", [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_points(bad)

    def test_nan_and_infinities_are_rejected(self):
        """`math.isfinite` 一并拒掉 NaN/±inf——只判负会让 NaN/+inf 漏网并污染全部求和。"""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_points(bad)

    def test_nan_from_json_children_is_rejected_end_to_end_without_writing(self):
        """`json.loads` 默认收 `NaN`：带 NaN 的 children 经 `Engine.expand` 必须被拒且不落事件。"""
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        before = len(store.load_events(spec.SPEC))
        children = json.loads('[{"id": "a", "goal": "g", "points": NaN}]')
        with self.assertRaises(ValueError):
            engine.expand("t", "root", children)
        self.assertEqual(len(store.load_events(spec.SPEC)), before,
                         "被拒的点数不得落事件")
        self.assertNotIn("a", engine.tree("t")["nodes"])

    def test_points_is_declarable_and_survives_expand(self):
        """`points` 是**可声明**字段：expand 收下并落到节点上，不是静默丢弃。"""
        self.assertIn("points", DECLARABLE_FIELDS)
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [
            {"id": "a", "goal": "g", "points": 3, "acceptance": acceptance()}])
        self.assertEqual(engine.load()["trees"]["t"]["nodes"]["a"]["points"], 3.0)

    def test_invalid_points_rejected_at_expand_without_writing(self):
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        with self.assertRaises(ValueError):
            engine.expand("t", "root", [
                {"id": "a", "goal": "g", "points": 0, "acceptance": acceptance()}])
        self.assertEqual(engine.load()["trees"]["t"]["nodes"].keys(), {"root"})

    def test_new_node_defaults_points_to_none(self):
        self.assertIsNone(new_node("x")["points"])


class TreeStartTest(DisflowTestCase):
    def test_first_tree_init_ts_wins(self):
        events = [_event("tree.init", _iso(2025, 1, 2)),
                  _event("tree.init", _iso(2025, 1, 5))]
        self.assertEqual(tree_start_ts(events, slug="t"), _epoch(2025, 1, 2))

    def test_other_trees_do_not_leak_in(self):
        events = [_event("tree.init", _iso(2025, 1, 1), slug="other")]
        self.assertIsNone(tree_start_ts(events, slug="t"))

    def test_no_init_event_returns_none(self):
        self.assertIsNone(tree_start_ts([], slug="t"))


class SummaryTest(DisflowTestCase):
    def test_unestimated_nodes_do_not_enter_the_sum(self):
        tree = _tree(_node("A", status="done", points=2),
                     _node("B", points=3),
                     _node("C", points=None))
        events = [_event("tree.init", _iso(2025, 1, 1)),
                  _event("node.done", _iso(2025, 1, 1), node="A")]
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["points_total"], 5.0, "未估的 C 不得进分母")
        self.assertEqual(result["points_done"], 2.0)
        self.assertEqual(result["points_left"], 3.0)
        self.assertEqual(result["points_unestimated"], 1)

    def test_containers_are_not_quantified(self):
        tree = _tree(_node("A", status="done", points=2),
                     _node("C", kind="container", points=99))
        events = [_event("tree.init", _iso(2025, 1, 1)),
                  _event("node.done", _iso(2025, 1, 1), node="A")]
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["points_total"], 2.0, "container 不是工作量")

    def test_same_day_velocity_is_zero_and_eta_is_none(self):
        tree = _tree(_node("A", status="done", points=2), _node("B", points=3))
        events = [_event("tree.init", _iso(2025, 1, 4)),
                  _event("node.done", _iso(2025, 1, 4), node="A")]
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["days_elapsed"], 0)
        self.assertEqual(result["velocity"], 0.0)
        self.assertIsNone(result["eta_days"], "速度为 0 时不得编出一个完工日")

    def test_velocity_and_eta_over_three_days(self):
        tree = _tree(_node("A", status="done", points=2), _node("B", points=3))
        events = [_event("tree.init", _iso(2025, 1, 1)),
                  _event("node.done", _iso(2025, 1, 2), node="A")]
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["days_elapsed"], 3)
        self.assertAlmostEqual(result["velocity"], 2 / 3)
        self.assertAlmostEqual(result["eta_days"], 4.5)

    def test_deadline_signs_both_directions(self):
        events = [_event("tree.init", _iso(2025, 1, 1))]
        ahead = summary(_tree(_node("A", points=1), deadline="2025-01-06"),
                        events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(ahead["deadline_remaining_days"], 2)
        self.assertEqual(ahead["deadline_overdue_days"], 0)
        behind = summary(_tree(_node("A", points=1), deadline="2025-01-02"),
                         events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(behind["deadline_remaining_days"], -2)
        self.assertEqual(behind["deadline_overdue_days"], 2)

    def test_no_deadline_reports_none_not_zero(self):
        tree = _tree(_node("A", points=1))
        result = summary(tree, events=[], slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["deadline"])
        self.assertIsNone(result["deadline_remaining_days"])
        self.assertIsNone(result["deadline_overdue_days"])


class SummaryDeadlineEntryTest(DisflowTestCase):
    """FIX-A8：deadline 的唯一入口是 `tree['deadline']`，不再是调用参数。"""

    def test_deadline_is_read_from_the_tree(self):
        tree = _tree(_node("A", points=1), deadline="2025-01-06")
        events = [_event("tree.init", _iso(2025, 1, 1))]
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["deadline"], "2025-01-06")
        self.assertEqual(result["deadline_remaining_days"], 2)
        self.assertIsNone(result["deadline_reason"])

    def test_unparseable_stored_deadline_is_reported_not_silently_ignored(self):
        """手写事件里的非法 deadline（reducer 不校验）不得被静默忽略——给显式 reason。"""
        events = [
            _event("tree.init", _iso(2025, 1, 1)),
            {"kind": "tree.deadline", "slug": "t", "ts": _iso(2025, 1, 2),
             "deadline": "2026-13-99"},
        ]
        tree = state.project(events)["trees"]["t"]
        self.assertEqual(tree["deadline"], "2026-13-99")
        result = summary(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["deadline"])
        self.assertIsNotNone(result["deadline_reason"],
                             "非法存储值必须给显式 reason，不能静默 None")
        self.assertIn("非法", result["deadline_reason"])


class SummaryStartReasonTest(DisflowTestCase):
    """FIX-A4：起点未知与「起点就是今天」都用 days_elapsed==0，必须靠 start_reason 区分。"""

    def test_unknown_start_sets_start_reason_and_start_ts_none(self):
        result = summary(_tree(_node("A", points=1)), [], slug="t",
                         now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["start_ts"])
        self.assertEqual(result["start_reason"], NO_START_REASON)
        self.assertEqual(result["days_elapsed"], 0)

    def test_start_known_today_has_no_reason_and_is_legit(self):
        events = [_event("tree.init", _iso(2025, 1, 4))]
        result = summary(_tree(_node("A", points=1)), events, slug="t",
                         now_ts=_epoch(2025, 1, 4))
        self.assertEqual(result["start_reason"], None,
                         "起点已知（哪怕就是今天）不得报 reason")
        self.assertEqual(result["days_elapsed"], 0)
        self.assertEqual(result["start_ts"], _epoch(2025, 1, 4))


class UnknownSlugReasonTest(DisflowTestCase):
    """FIX2-A1：传错 slug 与「树真没 init」不得共用同一文案——前者列出现过的 slug。"""

    def test_unknown_slug_reports_distinct_reason_listing_known_slugs(self):
        events = [_event("tree.init", _iso(2025, 1, 1), slug="REAL")]
        result = summary(_tree(_node("A", points=1)), events, slug="bad",
                         now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["start_ts"])
        self.assertIn("bad", result["start_reason"])
        self.assertIn("REAL", result["start_reason"],
                      "未知 slug 的 reason 必须列出流里出现过的 slug，便于立即发现拼错")
        self.assertNotEqual(result["start_reason"], NO_START_REASON)

    def test_known_slug_without_init_keeps_generic_reason(self):
        events = [_event("node.start", _iso(2025, 1, 2), node="A")]
        result = summary(_tree(_node("A", points=1)), events, slug="t",
                         now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["start_ts"])
        self.assertEqual(result["start_reason"], NO_START_REASON,
                         "slug 出现过但没有 tree.init → 保留原措辞")

    def test_blank_slug_stream_is_not_reported_as_unknown(self):
        """事件流只有空串 slug：那不是「可用 slug」，不得走「未知 slug」并列出空串。

        复核者实测：旧实现把空串收进 seen，文案变成「……出现过的 slug：」冒号后为空、
        自相矛盾。设计第三条：流里压根没有可用 slug → 保留笼统文案。
        """
        events = [_event("tree.init", _iso(2025, 1, 1), slug="")]
        result = summary(_tree(_node("A", points=1)), events, slug="t",
                         now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["start_ts"])
        self.assertEqual(result["start_reason"], NO_START_REASON,
                         "空串 slug 视同没有可用 slug，走笼统文案")
        self.assertNotIn("未知 slug", result["start_reason"])

    def test_none_slug_is_undecidable_not_implicit_match(self):
        """`slug=None` 查询不得与「事件没有 slug 键」隐含匹配（`None == None`）。

        复核者实测：旧实现会把无 slug 的 `tree.init` 当成该树起点（start_ts 有值）。
        `slug` 是 `str` 契约，`None` 按不可判定处理 → 起点 None + `NO_START_REASON`。
        """
        events = [{"kind": "tree.init", "ts": _iso(2025, 1, 1)}]
        self.assertIsNone(tree_start_ts(events, slug=None))  # type: ignore[arg-type]
        result = summary(_tree(_node("A", points=1)), events, slug=None,  # type: ignore[arg-type]
                         now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["start_ts"], "无 slug 的事件不得被当成 slug=None 的树起点")
        self.assertEqual(result["start_reason"], NO_START_REASON,
                         "不可判定 → 笼统文案，且不得让展示路径抛异常")

    def test_the_two_reasons_differ(self):
        tree = _tree(_node("A", points=1))
        known = [_event("node.start", _iso(2025, 1, 2), node="A")]
        unknown = [_event("tree.init", _iso(2025, 1, 1), slug="REAL")]
        now = _epoch(2025, 1, 4)
        r_known = burndown(tree, known, slug="t", now_ts=now)["start_reason"]
        r_unknown = burndown(tree, unknown, slug="t", now_ts=now)["start_reason"]
        self.assertNotEqual(r_known, r_unknown, "两种情况必须给不同的 reason")


class FutureStartTest(DisflowTestCase):
    """FIX2-A4：now_ts 早于 tree.init 时只给起点行，并显式报告（不静默）。"""

    def test_start_after_now_reports_reason_and_only_start_row(self):
        tree = _tree(_node("A", points=5))
        events = [_event("tree.init", _iso(2025, 1, 10))]
        now = _epoch(2025, 1, 3)
        result = burndown(tree, events, slug="t", now_ts=now)
        self.assertEqual([row["day"] for row in result["series"]], ["2025-01-10"],
                         "now 之前的日期无行；跨度夹到 0，只剩起点行")
        self.assertIsNotNone(result["start_reason"], "起点晚于 now 必须显式报告")
        s = summary(tree, events, slug="t", now_ts=now)
        self.assertIsNotNone(s["start_reason"])
        self.assertEqual(result["start_reason"], s["start_reason"],
                         "summary 与 burndown 对同一事实同一措辞")
        self.assertEqual(s["points_left"], result["series"][-1]["remaining"],
                         "不变量仍成立")


class AsOfConsistencyTest(DisflowTestCase):
    """FIX-A1 对抗性：`summary` 与 `burndown` 对同一份事实必须给同一个数。"""

    def _fixture(self) -> tuple[dict, list[dict]]:
        tree = _tree(_node("A", status="done", points=2), _node("B", points=3))
        events = [
            _event("tree.init", _iso(2025, 1, 1)),
            _event("node.done", _iso(2025, 1, 5), node="A"),
        ]
        return tree, events

    def test_points_left_equals_last_remaining_when_now_precedes_completion(self):
        """`now_ts` 早于完工 ts：summary 不得把「未来的完工」算进 points_done。"""
        tree, events = self._fixture()
        now = _epoch(2025, 1, 3)
        s = summary(tree, events, slug="t", now_ts=now)
        b = burndown(tree, events, slug="t", now_ts=now)
        self.assertEqual(s["points_done"], 0.0, "完工事件在 now_ts 之后，不计入")
        self.assertEqual(s["points_left"], 5.0)
        self.assertEqual(b["series"][-1]["remaining"], 5.0)
        self.assertEqual(s["points_left"], b["series"][-1]["remaining"])

    def test_points_left_equals_last_remaining_when_now_follows_completion(self):
        tree, events = self._fixture()
        now = _epoch(2025, 1, 6)
        s = summary(tree, events, slug="t", now_ts=now)
        b = burndown(tree, events, slug="t", now_ts=now)
        self.assertEqual(s["points_done"], 2.0)
        self.assertEqual(s["points_left"], 3.0)
        self.assertEqual(s["points_left"], b["series"][-1]["remaining"])

    def test_without_tree_init_both_give_explicit_readings(self):
        """无 tree.init：不许一处 `[]`、一处静默 0——两者都要有显式 start_reason。"""
        tree = _tree(_node("A", points=1))
        now = _epoch(2025, 1, 4)
        s = summary(tree, [], slug="t", now_ts=now)
        b = burndown(tree, [], slug="t", now_ts=now)
        self.assertEqual(b["series"], [], "没有起点就画不出序列")
        self.assertEqual(s["start_reason"], NO_START_REASON)
        self.assertEqual(b["start_reason"], NO_START_REASON)
        self.assertEqual(s["start_reason"], b["start_reason"], "同一份事实同一措辞")


class BurndownTest(DisflowTestCase):
    def _fixture(self) -> tuple[dict, list[dict]]:
        tree = _tree(_node("A", status="done", points=2),
                     _node("B", points=3),
                     _node("C", points=None))
        events = [
            _event("tree.init", _iso(2025, 1, 1)),
            _event("node.start", _iso(2025, 1, 2), node="A"),
            _event("node.done", _iso(2025, 1, 2), node="A"),
        ]
        return tree, events

    def test_series_length_and_values_track_completion_days(self):
        tree, events = self._fixture()
        result = burndown(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        series = result["series"]
        self.assertEqual([row["day"] for row in series],
                         ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"])
        # A(2) 在 01-02 完成 ⇒ 从那天起从 5 降到 3；未估的 C 不计入。
        self.assertEqual([row["remaining"] for row in series], [5.0, 3.0, 3.0, 3.0])

    def test_without_deadline_ideal_is_none_with_a_reason(self):
        tree, events = self._fixture()
        result = burndown(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["ideal"], "无 deadline 不得伪造一条理想线")
        self.assertTrue(result["ideal_reason"])

    def test_ideal_descends_linearly_to_zero_at_deadline(self):
        tree, events = self._fixture()
        tree["deadline"] = "2025-01-06"
        result = burndown(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        ideal = result["ideal"]
        self.assertIsNotNone(ideal)
        assert ideal is not None
        self.assertEqual([row["day"] for row in ideal],
                         ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"])
        for row, expected in zip(ideal, [5.0, 4.0, 3.0, 2.0], strict=True):
            self.assertAlmostEqual(row["ideal"], expected)

    def test_deadline_not_after_start_yields_no_ideal(self):
        tree, events = self._fixture()
        tree["deadline"] = "2025-01-01"
        result = burndown(tree, events, slug="t", now_ts=_epoch(2025, 1, 4))
        self.assertIsNone(result["ideal"])
        self.assertTrue(result["ideal_reason"])

    def test_last_remaining_matches_summary_points_left(self):
        tree, events = self._fixture()
        now = _epoch(2025, 1, 4)
        series = burndown(tree, events, slug="t", now_ts=now)["series"]
        self.assertEqual(series[-1]["remaining"],
                         summary(tree, events, slug="t", now_ts=now)["points_left"])


class SlugNotInProjectionTest(DisflowTestCase):
    """FIX-A5：`slug` 恒等于外层 key，是可推导值——不得持久化进投影。"""

    def test_projection_tree_has_no_slug_key(self):
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        tree = engine.tree("t")
        self.assertNotIn("slug", tree, "可推导值不该进投影")
        self.assertIn("deadline", tree, "deadline 是真声明，留在投影里")
        projected = state.project(store.load_events(spec.SPEC))["trees"]["t"]
        self.assertNotIn("slug", projected)


class ProjectionDeterminismTest(DisflowTestCase):
    def test_project_of_loaded_events_equals_live_state(self):
        """投影确定性：`project(load_events())` 必须等于实时投影（`rebuild --check` 的判据）。"""
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [
            {"id": "a", "goal": "a", "points": 3, "acceptance": acceptance()},
            {"id": "c", "kind": "container", "goal": "c"}])
        engine.deadline("t", "2030-01-01")
        engine.start("t", "a", "w")
        engine.done("t", "a")
        matched, diffs = store.projection_matches(spec.SPEC, project=state.project)
        self.assertTrue(matched, diffs)
        self.assertEqual(state.project(store.load_events(spec.SPEC),
                                       )["trees"]["t"]["deadline"], "2030-01-01")


if __name__ == "__main__":
    unittest.main()
