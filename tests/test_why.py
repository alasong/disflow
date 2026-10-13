"""追溯（`disflow why`）：把「这个节点发生了什么」从事件流里捡回来。

**为何单独立测试**：本动词的判据不是「输出好看」，而是**「事件流里的字段一条都不丢」**——
出问题时要能顺着它追到「谁执行的 / 干了什么 / 为什么改 / 过了什么门」。
只测「有输出」等于没测：漏一个 kind、少一个字段，追溯就在那一条上断掉，且**静默**。

判据（每条对应一个断言）：

1. **原序不重排** —— 顺序本身是「何时发生」的线索；按 kind 排序会丢掉因果。
2. **只收本树的该节点** —— 别的树/别的节点的事件不许混进来（串了就是错的追溯）。
3. **六个关键 kind 都收** —— require / start / collect / rescope / acceptance / done；
   少收一个，就有一类问题追不到。
4. **关键字段真被渲染出来** —— holder（谁）、summary（干了什么）、reason（为什么改）、
   cmd（拿什么验）、exit（过没过）、introduced（改了多大）。
5. **空/异常输入不崩、不静默** —— 空 summary 不越界；节点无记录时**明说没有**（不打白屏）。
6. **纯读** —— 跑前后 `events.jsonl`/`state.json` 逐字节不变（与 `board` 同一纪律）。
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, git_repo

from disflow import store, views


def _ev(kind, tree="t", node="n", ts="2026-01-01T00:00:00+00:00", **extra):
    row = {"ts": ts, "kind": kind, "slug": tree, "node": node}
    row.update(extra)
    return row


class TraceNodeTest(DisflowTestCase):

    def test_keeps_original_order(self):
        events = [
            _ev("node.start", ts="2026-01-01T00:00:01+00:00"),
            _ev("node.collect", ts="2026-01-01T00:00:02+00:00"),
            _ev("node.done", ts="2026-01-01T00:00:03+00:00"),
        ]
        rows = views.trace_node(events, "t", "n")
        self.assertEqual([r["kind"] for r in rows],
                         ["node.start", "node.collect", "node.done"])

    def test_filters_tree_and_node(self):
        events = [
            _ev("node.done", tree="t", node="n"),
            _ev("node.done", tree="other", node="n"),
            _ev("node.done", tree="t", node="other"),
        ]
        rows = views.trace_node(events, "t", "n")
        self.assertEqual(len(rows), 1)

    def test_collects_all_six_key_kinds(self):
        events = [
            _ev("node.require", dep="survey"),
            _ev("node.start", lease={"holder": "web-test", "expires_at": "X"}),
            _ev("node.collect", result={"status": "ok", "summary": "干完了"}),
            _ev("node.rescope", touch_plan=["a.py"], reason="补写面"),
            _ev("node.acceptance", acceptance={"cmd": "pytest", "expect": "exit0"},
                reason="补判据"),
            _ev("node.done", evidence={"acceptance": {"exit": 0}, "introduced": ["a.py"]}),
        ]
        rows = views.trace_node(events, "t", "n")
        self.assertEqual(len(rows), 6)
        blob = "\n".join(r["text"] for r in rows)
        for needle in ("holder=web-test", "干完了", "补写面", "pytest", "1 处改动", "survey"):
            self.assertIn(needle, blob, f"追溯文本里丢了 {needle!r}")

    def test_empty_summary_does_not_crash(self):
        """空 summary 曾是**真崩溃**：`splitlines()[0]` 对空串越界（IndexError）。"""
        rows = views.trace_node([_ev("node.collect", result={"status": "ok", "summary": ""})],
                                "t", "n")
        self.assertEqual(len(rows), 1)
        self.assertIn("ok", rows[0]["text"])

    def test_missing_payload_key_does_not_crash(self):
        """事件体缺失/为 None 时按空处理，不崩。"""
        rows = views.trace_node([{"ts": "x", "kind": "node.done", "slug": "t", "node": "n",
                                  "payload": None}], "t", "n")
        self.assertEqual(len(rows), 1)

    def test_unknown_node_says_so(self):
        """无记录时**明说**，不打白屏（“沉默”会被当成“没出问题”）。"""
        lines = views.why_lines([], tree="t", node="nope")
        self.assertTrue(any("没有该节点的记录" in line for line in lines))

    def test_why_lines_marks_done(self):
        rows = views.trace_node([_ev("node.done")], "t", "n")
        lines = views.why_lines(rows, tree="t", node="n")
        self.assertTrue(lines[0].startswith("t/n"))
        self.assertTrue(any(line.strip().startswith("✓") for line in lines))


class WhyIsReadOnlyTest(DisflowTestCase):
    """`why` 是纯读：它只重排事件，不产生新事实、不落事件。"""

    def test_reads_events_without_writing(self):
        eng, _ = git_repo(self, {"a.py": "x\n"})
        eng.init_tree("t", goal="目标")
        ev = store.events_path(eng.storage)
        proj = store.projection_path(eng.storage)
        with open(ev, "rb") as fh:
            ev_before = fh.read()
        with open(proj, "rb") as fh:
            proj_before = fh.read()
        rows = views.trace_node(store.load_events(eng.storage), "t", "root")
        self.assertTrue(rows, "tree.init 应至少产生一条该节点的可追溯记录")
        with open(ev, "rb") as fh:
            self.assertEqual(ev_before, fh.read(), "why 不许改事件流")
        with open(proj, "rb") as fh:
            self.assertEqual(proj_before, fh.read(), "why 不许改投影")


if __name__ == "__main__":
    unittest.main()
