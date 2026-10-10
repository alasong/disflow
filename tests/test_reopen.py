"""关树之后的边界：`reopen` 动词 + `expand` 对已关闭树的拒收 + 事后探测。

**为什么要有这组测试**：`closed` 是跨树完成信号（另一棵树的 `awaits` 读它，FR-23）。
在本次修复前，`expand` 对已关闭的树**照收**（guard 只查 parent 存在 + 词表合法），于是：

    树 d 关掉（●）→ expand d a 又挂两个任务 → d 仍报 ● → 另一棵树里 awaits:["d"] 立刻 ready

即「一个已完成的上游」可以继续长出未完成的任务，而消费方已经基于该信号开工了，且没人知道。
本文件把三条边界钉住：

1. `expand` 对已关闭树**拒收**，且报错文本直接给出修法（`disflow reopen`），不让人猜。
2. `reopen` 是**显式、留痕**的动作（`tree.reopen` 事件 + `reopen_log`），与 `rescope` 同形状。
3. `doctor` 能**事后探测**修复前建的老状态目录——而这条只能从**事件顺序**判，
   因为「已关闭且仍有未完成任务」本身是**合法状态**（`close` 不要求没有未完成任务）。
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import spec, state, store
from disflow.cli import main
from disflow.engine import Engine, EngineError
from disflow.state import expands_after_close, project


def _task(node_id: str, **extra) -> dict:
    body = {"id": node_id, "goal": node_id, "acceptance": {"cmd": "true"}}
    body.update(extra)
    return body


class _CliRunMixin:
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()


class ExpandOnClosedTreeTest(DisflowTestCase):
    """边界 1：已关闭的树不能继续挂任务。"""

    def setUp(self) -> None:
        super().setUp()                     # 必须先调：否则没隔离状态目录，测试会串味
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("d", "demo")
        self.engine.expand("d", "root", [_task("a")])
        self.engine.annotate("d", "root", {"closure": "not_applicable: 演示"})
        self.engine.close("d")

    def test_closed_tree_is_reported_closed(self) -> None:
        """前提：上面那套操作**真的**把树关掉了（否则后面的断言全在测空气）。"""
        self.assertTrue(self.engine.tree("d").get("closed"))
        self.assertIn("d", self.engine.closed_trees())

    def test_expand_on_closed_tree_is_rejected_and_points_to_reopen(self) -> None:
        before = len(store.load_events(spec.SPEC))
        with self.assertRaises(EngineError) as ctx:
            self.engine.expand("d", "a", [_task("z")])
        message = str(ctx.exception)
        self.assertIn("已关闭", message)
        # 报错必须给出**可照抄的修法**，而不是只说不许
        self.assertIn("disflow reopen d", message)
        self.assertEqual(len(store.load_events(spec.SPEC)), before,
                         "拒收必须一条事件都不落（不是落一半）")
        self.assertNotIn("z", self.engine.tree("d")["nodes"])

    def test_expand_works_again_after_reopen(self) -> None:
        """边界 2 的正向面：显式重开之后，挂任务必须恢复正常（逃生口真的能逃生）。"""
        self.engine.reopen("d", reason="发现漏了 z")
        self.assertFalse(self.engine.tree("d").get("closed"))
        self.engine.expand("d", "a", [_task("z")])
        self.assertIn("z", self.engine.tree("d")["nodes"])


class ReopenMechanicsTest(DisflowTestCase):
    """边界 2：`reopen` 的幂等性 / 留痕 / 与 `close` 的对称性。"""

    def setUp(self) -> None:
        super().setUp()                     # 必须先调：否则没隔离状态目录，测试会串味
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("d", "demo")
        self.engine.expand("d", "root", [_task("a")])
        self.engine.annotate("d", "root", {"closure": "not_applicable: 演示"})

    def test_reopen_on_open_tree_is_idempotent(self) -> None:
        """与 `close` 的幂等对齐：重复调不报错；且**不**在 `reopen_log` 里记假历史。"""
        self.engine.reopen("d", reason="没关就重开")
        self.assertFalse(self.engine.tree("d").get("closed"))
        self.assertNotIn("reopen_log", self.engine.tree("d"),
                         "从未关闭过就不该出现重开历史（否则历史是伪造的）")

    def test_reopen_records_reason_and_timestamp(self) -> None:
        self.engine.close("d")
        self.engine.reopen("d", reason="客户改了需求")
        log = self.engine.tree("d").get("reopen_log") or []
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["reason"], "客户改了需求")
        self.assertTrue(log[0]["at"], "时间戳要留（否则审计只有理由没有先后）")

    def test_reopen_without_reason_still_lands_an_event(self) -> None:
        """`reason` 可选：审计价值在「至少有一次显式事件」，不在强逼字符串。"""
        self.engine.close("d")
        self.engine.reopen("d")
        kinds = [e["kind"] for e in store.load_events(spec.SPEC)]
        self.assertIn("tree.reopen", kinds)

    def test_reopen_then_close_again_is_allowed(self) -> None:
        """反复开关是合法的：每一次都留痕，谁都没被静默吞掉。"""
        self.engine.close("d")
        self.engine.reopen("d", reason="再干一阵")
        self.engine.close("d")
        self.assertTrue(self.engine.tree("d").get("closed"))
        kinds = [e["kind"] for e in store.load_events(spec.SPEC) if e["kind"].startswith("tree.")]
        self.assertEqual(kinds, ["tree.init", "tree.close", "tree.reopen", "tree.close"])

    def test_twice_closed_after_reopen_keeps_both_reasons(self) -> None:
        self.engine.close("d")
        self.engine.reopen("d", reason="第一次重开")
        self.engine.close("d")
        self.engine.reopen("d", reason="第二次重开")
        self.assertEqual([r["reason"] for r in self.engine.tree("d")["reopen_log"]],
                         ["第一次重开", "第二次重开"])

    def test_kind_is_registered_in_both_declarations(self) -> None:
        """`spec.EVENT_KINDS` 与 `state._HANDLERS` 必须同步（结构性守卫，见 test_declarations）。"""
        self.assertIn("tree.reopen", spec.EVENT_KINDS)
        self.assertIn("tree.reopen", state._HANDLERS)


class HolderOnExpandTest(DisflowTestCase):
    """`node.expand` 记「谁挂的」：`start` 早就有 `--holder`，`expand` 一直没有。"""

    def test_expand_event_carries_holder(self) -> None:
        engine = Engine(cwd=self.tmp)
        engine.init_tree("d", "demo")
        engine.expand("d", "root", [_task("a")], holder="alice")
        event = [e for e in store.load_events(spec.SPEC) if e["kind"] == "node.expand"][-1]
        self.assertEqual(event["holder"], "alice")

    def test_holder_defaults_to_empty_and_never_breaks_replay(self) -> None:
        engine = Engine(cwd=self.tmp)
        engine.init_tree("d", "demo")
        engine.expand("d", "root", [_task("a")])
        event = [e for e in store.load_events(spec.SPEC) if e["kind"] == "node.expand"][-1]
        self.assertEqual(event["holder"], "")
        replayed = project(store.load_events(spec.SPEC))
        matched, diffs = store.projection_matches(spec.SPEC, project=project)
        self.assertTrue(matched, diffs)
        self.assertEqual(sorted(replayed["trees"]["d"]["nodes"]), ["a", "root"])


class ExpandsAfterCloseTest(DisflowTestCase):
    """探测函数本身：这就是 `doctor` 用的那条判据。"""

    def test_consecutive_close_reopen_close_expand_is_clean(self) -> None:
        """关过了、但**重开之后**才挂 —— 不是违规（那条 close 已被显式撤销）。"""
        events = [
            {"kind": "tree.init", "slug": "d", "ts": "1"},
            {"kind": "tree.close", "slug": "d", "ts": "2"},
            {"kind": "tree.reopen", "slug": "d", "ts": "3"},
            {"kind": "node.expand", "slug": "d", "ts": "4", "node": "root"},
        ]
        self.assertEqual(expands_after_close(events), [])

    def test_expand_after_last_close_is_a_violation(self) -> None:
        events = [
            {"kind": "tree.init", "slug": "d", "ts": "1"},
            {"kind": "tree.close", "slug": "d", "ts": "2"},
            {"kind": "node.expand", "slug": "d", "ts": "3", "node": "a", "holder": "bob"},
        ]
        found = expands_after_close(events)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["tree"], "d")
        self.assertEqual(found[0]["closed_at"], "2")
        self.assertEqual(found[0]["expanded_at"], "3")
        self.assertEqual(found[0]["node"], "a")
        self.assertEqual(found[0]["holder"], "bob")

    def test_only_the_earliest_violation_per_tree_is_reported(self) -> None:
        """反复违规只报第一条 —— 报一次就够了，刷屏会淹掉别的故障。"""
        events = [
            {"kind": "tree.close", "slug": "d", "ts": "1"},
            {"kind": "node.expand", "slug": "d", "ts": "2", "node": "a"},
            {"kind": "node.expand", "slug": "d", "ts": "3", "node": "b"},
        ]
        self.assertEqual(len(expands_after_close(events)), 1)
        self.assertEqual(expands_after_close(events)[0]["expanded_at"], "2")

    def test_expand_before_any_close_is_clean(self) -> None:
        events = [
            {"kind": "node.expand", "slug": "d", "ts": "1", "node": "root"},
            {"kind": "tree.close", "slug": "d", "ts": "2"},
        ]
        self.assertEqual(expands_after_close(events), [])

    def test_each_tree_is_judged_separately(self) -> None:
        events = [
            {"kind": "tree.close", "slug": "d", "ts": "1"},
            {"kind": "node.expand", "slug": "e", "ts": "2", "node": "root"},
        ]
        self.assertEqual(expands_after_close(events), [])

    def test_events_without_slug_are_ignored_not_crashed_on(self) -> None:
        events = [{"kind": "tree.close", "ts": "1"}, {"kind": "node.expand", "ts": "2"}]
        self.assertEqual(expands_after_close(events), [])


class DoctorDetectsLegacyViolationTest(_CliRunMixin, DisflowTestCase):
    """边界 3：`doctor` 事后探测老状态目录 —— 且**不误报合法状态**。"""

    def _close_tree_with_pending_task(self) -> None:
        self._run(["init", "d", "--goal", "demo"])
        self._run(["expand", "d", "root", "--children", json.dumps([_task("a")])])
        self._run(["context", "d", "root", "--set",
                   '{"closure":"not_applicable: 演示"}'])
        code, _, _ = self._run(["close", "d"])
        self.assertEqual(code, 0, "前提：这套操作必须真的关成功")

    def test_legitimately_closed_tree_with_pending_tasks_is_not_a_problem(self) -> None:
        """**这条是本文件最重要的反面守卫**：`close` 不要求没有未完成任务。

        「放弃剩余活、就这么关」是正当意图，它与「关树后又偷偷挂任务」在 `state.json` 里
        长得一模一样。若 doctor 用**终态**判，就会对这个合法状态报故障——
        而宽错比不报错更坏（同时制造假红与假绿）。
        """
        self._close_tree_with_pending_task()
        code, out, err = self._run(["doctor"])
        self.assertEqual(code, 0, f"合法状态被误报成故障：{err}")
        self.assertIn("全绿", out)

    def test_expand_event_after_close_is_reported(self) -> None:
        """模拟修复前建的状态目录：绕过 `Engine.expand` 的守卫，直接落事件。"""
        self._close_tree_with_pending_task()
        store.append_event(spec.SPEC, "node.expand", project=project, slug="d", node="a",
                           extra={"children": [_task("z")], "holder": "legacy-bob"})
        code, out, err = self._run(["doctor"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "", "失败时 stdout 必须干净（既有契约）")
        self.assertIn("已关闭", err)
        self.assertIn("legacy-bob", err)
        self.assertIn("disflow reopen d", err)

    def test_violation_disappears_after_explicit_reopen(self) -> None:
        """重开之后，那条历史违规不再算违规（显式动作就是修法）。"""
        self._close_tree_with_pending_task()
        store.append_event(spec.SPEC, "node.expand", project=project, slug="d", node="a",
                           extra={"children": [_task("z")], "holder": "legacy-bob"})
        self.assertEqual(self._run(["doctor"])[0], 1)
        self.assertEqual(self._run(["reopen", "d", "--reason", "补上 z"])[0], 0)
        self.assertEqual(self._run(["doctor"])[0], 0, "重开是那个历史违规的合法修法")

    def test_expand_cli_refusal_is_exit_1_and_stdout_clean(self) -> None:
        self._close_tree_with_pending_task()
        code, out, err = self._run(["expand", "d", "a", "--children", json.dumps([_task("z")])])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("disflow reopen d", err)

    def test_expand_cli_accepts_holder(self) -> None:
        self._run(["init", "d", "--goal", "demo"])
        self._run(["expand", "d", "root", "--children", json.dumps([_task("a")]),
                   "--holder", "alice"])
        event = [e for e in store.load_events(spec.SPEC) if e["kind"] == "node.expand"][-1]
        self.assertEqual(event["holder"], "alice")

    def test_reopen_cli_warns_when_reason_missing(self) -> None:
        self._close_tree_with_pending_task()
        code, out, _ = self._run(["reopen", "d"])
        self.assertEqual(code, 0)
        self.assertIn("未给 --reason", out)


if __name__ == "__main__":
    unittest.main()
