"""改看板列（`stage`）的动词 —— 建树之后能改列，且这是**视图**改动（不碰调度）。

本文件钉五件事（缺一条这个动词就会退化成新的机制缺口）：

1. **闭集拒收**：取值必须在配置的 `stages` 内，非法值响亮拒收且**不落事件**
   （静默忽略会把“列与事实不符”这个要修的问题原样留下）。
2. **事件落盘 + 可重放 + rebuild 一致**：新 kind `node.stage` 落事件、进 `_HANDLERS` /
   `EVENT_KINDS`，`rebuild` 后逐字段读数不变（否则改列没有“事实源”可言）。
3. **收尾态拒收**：`done`/`skipped`/`failed` 不可改列（与「终态不可重开」一致），
   报错给出 `retry` 这条出路；`failed` 经 `retry` 放回 pending 后即可改列。
4. **不碰调度**：`ready`/`drive`/波次/`is_stuck` 改前改后**逐字节一致**
   （stage 是视图列，不是调度边；否则改列会变成隐形的调度阀门）。
5. **看板列跟着 stage 变**：`board` 的列随 `stage` 迁移（投影/视图同源）。

这五条正是「与 C4.1 同一形状的机制缺口」的判据：`stage` 当年像 `touch_plan` 一样
建树后不可改，而看板是**同一棵树的投影** —— 投影与事实不符却没有合法路径改，
人只能去改会被 `rebuild` 覆盖的 `state.json`（违反「事件才是真相」）。
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance

from disflow import vocab, state, store
from disflow.boards import DEFAULT_BOARDS, Board
from disflow.cli import main
from disflow.config import Config
from disflow.engine import Engine, EngineError
from disflow.ready import is_stuck, ready_tasks
from disflow.state import project
from disflow.views import board_lines


class StageChangeTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def _seed(self, children: list[dict] | None = None) -> None:
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", children or [
            {"id": "n12", "goal": "收益验证", "stage": "dev", "acceptance": acceptance()},
            {"id": "n13", "goal": "经验沉淀", "stage": "dev", "acceptance": acceptance()},
        ])

    def _node(self, node: str = "n12") -> dict:
        return self.engine.load()["trees"]["t"]["nodes"][node]

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    # ── 正当用法：改列 + 留痕（谁改的 / 为什么）───────────────────────────

    def test_change_stage_updates_column_and_logs_reason_and_holder(self):
        self._seed()
        self.engine.set_stage("t", "n12", "verify",
                              reason="收益验证不是 dev", holder="host")
        node = self._node()
        self.assertEqual(node["stage"], "verify")
        self.assertEqual(node["stage_log"], [{
            "from": "dev", "to": "verify",
            "reason": "收益验证不是 dev", "holder": "host",
        }])

    def test_stage_log_accumulates_every_change(self):
        self._seed()
        self.engine.set_stage("t", "n12", "verify", reason="r1", holder="h1")
        self.engine.set_stage("t", "n12", "design", reason="r2", holder="h2")
        log = self._node()["stage_log"]
        self.assertEqual([(item["from"], item["to"]) for item in log],
                         [("dev", "verify"), ("verify", "design")])
        self.assertEqual([item["holder"] for item in log], ["h1", "h2"])

    def test_change_stage_requires_a_reason(self):
        self._seed()
        for blank in ("", "   "):
            with self.subTest(reason=blank), self.assertRaises(EngineError):
                self.engine.set_stage("t", "n12", "verify", reason=blank)

    # ── 闭集拒收（写时：与 expand 对 stage/role 同一套判据）────────────────

    def test_illegal_stage_is_rejected_and_writes_nothing(self):
        self._seed()
        before = len(store.load_events(vocab.STORAGE))
        with self.assertRaises(EngineError) as ctx:
            self.engine.set_stage("t", "n12", "audti", reason="手滑写成错别字")
        self.assertIn("stage 取值非法", str(ctx.exception))
        self.assertIn("audti", str(ctx.exception))
        self.assertEqual(len(store.load_events(vocab.STORAGE)), before, "拒收不许落事件")
        self.assertEqual(self._node()["stage"], "dev", "拒收不许改投影")
        self.assertNotIn("stage_log", self._node(), "拒收不许留下半条历史")

    def test_closed_set_is_config_driven(self):
        """自定义看板列照旧走配置——与 `expand` 的 stage 校验同源。"""
        cfg = Config(boards=DEFAULT_BOARDS + (Board("blocked", "阻塞"),))
        engine = Engine(cwd=self.tmp, cfg=cfg)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "g", "acceptance": acceptance()}])
        engine.set_stage("t", "n", "blocked", reason="自定义列")
        self.assertEqual(engine.load()["trees"]["t"]["nodes"]["n"]["stage"], "blocked")

    # ── 事件可重放 + rebuild 一致 ────────────────────────────────────────

    def test_change_lands_new_event_kind_and_is_registered(self):
        self._seed()
        self.engine.set_stage("t", "n12", "verify", reason="r")
        kinds = [event["kind"] for event in store.load_events(vocab.STORAGE)]
        self.assertIn("node.stage", kinds)
        # 新 kind 必须在两套清单里（否则 doctor 与错误文本会漂移 —— 本仓既有判据）
        self.assertIn("node.stage", vocab.EVENT_KINDS)
        self.assertIn("node.stage", state._HANDLERS)

    def test_change_survives_replay_and_rebuild_is_consistent(self):
        self._seed()
        self.engine.set_stage("t", "n12", "verify", reason="r", holder="h")
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)
        rebuilt = store.rebuild(vocab.STORAGE, project=project)
        node = rebuilt["trees"]["t"]["nodes"]["n12"]
        self.assertEqual(node["stage"], "verify")
        self.assertEqual(node["stage_log"], self._node()["stage_log"])

    # ── 收尾态拒收（与「终态不可重开」一致）──────────────────────────────

    def test_terminal_nodes_cannot_change_stage(self):
        self._seed([
            {"id": "d", "goal": "d", "stage": "dev", "acceptance": acceptance()},
            {"id": "s", "goal": "s", "stage": "dev", "acceptance": acceptance()},
            {"id": "f", "goal": "f", "stage": "dev", "acceptance": acceptance()},
        ])
        self.engine.start("t", "d", "w")
        self.engine.done("t", "d")
        self.engine.skip("t", "s", "不做了")
        self.engine.fail("t", "f", "炸了")
        for node_id in ("d", "s", "f"):
            with self.subTest(node=node_id), self.assertRaises(EngineError) as ctx:
                self.engine.set_stage("t", node_id, "verify", reason="想改")
                self.assertIn("终态不可重开", str(ctx.exception))
                self.assertIn("retry", str(ctx.exception))      # 拒收必须给出路
                self.assertEqual(self._node(node_id)["stage"], "dev")

    def test_failed_node_can_change_stage_after_retry(self):
        """`failed` 的出路：先 `retry` 放回 pending，再改列（收尾态不是死路）。"""
        self._seed([{"id": "f", "goal": "f", "stage": "dev", "acceptance": acceptance()}])
        self.engine.fail("t", "f", "炸了")
        self.engine.retry("t", "f")
        self.engine.set_stage("t", "f", "verify", reason="重开后改列")
        self.assertEqual(self._node("f")["stage"], "verify")

    # ── 不碰调度：stage 是视图列，不是调度边 ──────────────────────────────

    def test_stage_change_does_not_affect_scheduling(self):
        self._seed([
            {"id": "a", "goal": "A", "stage": "dev", "acceptance": acceptance()},
            {"id": "b", "goal": "B", "stage": "dev", "acceptance": acceptance(),
             "requires": ["a"]},
            {"id": "c", "goal": "C", "stage": "dev", "acceptance": acceptance()},
        ])
        nodes_before = self.engine.tree("t")["nodes"]
        drive_before = self.engine.drive("t")
        status_before = self.engine.status("t")

        self.engine.set_stage("t", "a", "verify", reason="看板列迁移")
        self.engine.set_stage("t", "b", "design", reason="看板列迁移")

        nodes_after = self.engine.tree("t")["nodes"]
        drive_after = self.engine.drive("t")
        status_after = self.engine.status("t")

        self.assertEqual(drive_before, drive_after, "drive 读数逐字段不变")
        self.assertEqual(drive_before["waves"], drive_after["waves"], "波次不变")
        self.assertEqual(drive_before["blocked"], drive_after["blocked"])
        self.assertEqual(drive_before["ready_wave"], drive_after["ready_wave"])
        self.assertEqual(ready_tasks(nodes_before), ready_tasks(nodes_after))
        self.assertEqual(is_stuck(nodes_before), is_stuck(nodes_after))
        self.assertEqual(status_before["ready"], status_after["ready"])
        self.assertEqual(status_before["stuck"], status_after["stuck"])

    # ── 看板列跟着 stage 走（投影/视图同源）──────────────────────────────

    def test_board_column_follows_stage(self):
        self._seed()
        before = board_lines(self.engine.tree("t"))
        self.assertIn("[dev] 0/2", before)

        self.engine.set_stage("t", "n12", "verify", reason="收益验证属于 verify")
        after = board_lines(self.engine.tree("t"))
        self.assertIn("[verify] 0/1", after)
        self.assertIn("[dev] 0/1", after)
        self.assertFalse(any("n12" in line for line in after
                             if line.startswith("[dev]")),
                         "n12 的列表随 stage 迁移，不再留在 dev 列")

        verify_only = board_lines(self.engine.tree("t"), stage="verify")
        self.assertTrue(any("n12" in line for line in verify_only))
        self.assertFalse(any("n13" in line for line in verify_only))

    # ── CLI 装配（旗标真接上引擎；失败走 stderr）─────────────────────────

    def test_cli_stage_moves_column_and_records_holder(self):
        self._seed()
        code, out, err = self._run(["stage", "t", "n12", "verify",
                                    "--reason", "收益验证不是 dev", "--holder", "host"])
        self.assertEqual(code, 0, err)
        self.assertIn("stage", out)
        self.assertEqual(self._node()["stage"], "verify")
        self.assertEqual(self._node()["stage_log"][-1]["holder"], "host")

    def test_cli_rejects_illegal_stage_on_stderr(self):
        self._seed()
        code, out, err = self._run(["stage", "t", "n12", "audti", "--reason", "typo"])
        self.assertEqual(code, 1)
        self.assertIn("stage 取值非法", err)
        self.assertEqual(out, "", "失败时 stdout 必须干净")
        self.assertEqual(self._node()["stage"], "dev")


if __name__ == "__main__":
    unittest.main()
