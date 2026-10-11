"""逐树看板（动态看板 D4 后半，2026-10-11）：**板定义进事件流**。

**为什么要进事件流**（本文件就是这条决定的规格）：`boards.json` 是**进程级**的
（全项目一份、同一棵树在不同机器上可能读到不同值）。若「该树有哪些板」由配置文件决定，
重放旧事件流就会得到不同结果 —— **事件流不可重放**。所以板定义随 `tree.init`/`tree.boards`
写进事件，成为该树的**事实**；`boards.json` 只是建树时的**播种值**。

判据（每条都可跑）：

1. **逐树覆盖**：`Engine.init_tree(boards=...)` / CLI `init --boards` 把该树板集写进事件、
   投影携带 —— 同一进程里不同树可以有**不同**的板；
2. **可重放**：换 `boards.json` 后，**旧树读数逐字节不变**（板是事件里的事实，不是配置）；
   新树才拿新配置；
3. **闭集按该树**：`stage` 写时校验用**该树**的板 id —— 别的树的合法列不该被本树拒掉，
   本树的非法列（哪怕在项目级配置里合法）也要响亮拒收；
4. **默认板随树**：任务没声明 `stage` 时落**该树**的默认板（内建 `dev` 在则用它，否则入口板）；
5. **旧事件流兼容**：不带 `boards` 的 `tree.init`（老目录 / 早期版本）⇒ 回落 `cfg.boards`，不崩；
6. **`set_boards` 可改**：树级补口，改完后校验与视图**同时**跟着变。
"""
from __future__ import annotations

import json
import os
import unittest

from base import DisflowTestCase

from disflow import vocab, store
from disflow.boards import Board, BoardsError
from disflow.cli import build_parser, cmd_board, cmd_init
from disflow.engine import Engine, EngineError
from disflow.state import project
from disflow.views import board_layout


def _write_project_boards(test: DisflowTestCase, ids: list[str]) -> str:
    """把项目级 `boards.json` 写到测试的 cwd（= `boards_path(cwd)` 的位置）。"""
    path = os.path.join(test.tmp, "boards.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"boards": ids}, handle)
    return path


class PerTreeBoardsTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    # ── 判据 1：逐树覆盖 ──────────────────────────────────────────────────
    def test_init_boards_are_recorded_in_the_projection(self):
        self.engine.init_tree("t", "g", boards=(Board("调研", "调研"), Board("实验", "实验")))
        tree = self.engine.tree("t")
        self.assertEqual(tree["boards"], [{"id": "调研", "label": "调研"},
                                          {"id": "实验", "label": "实验"}])
        self.assertEqual(self.engine._tree_stages(tree), ("调研", "实验"))

    def test_two_trees_can_have_different_boards(self):
        self.engine.init_tree("t1", "g", boards=(Board("a", "A"), Board("b", "B")))
        self.engine.init_tree("t2", "g", boards=(Board("x", "X"),))
        self.engine.expand("t1", "root", [{"id": "n", "stage": "a", "goal": "做"}])
        self.engine.expand("t2", "root", [{"id": "n", "stage": "x", "goal": "做"}])
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t1")), ("a", "b"))
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t2")), ("x",))

    def test_init_without_boards_uses_project_config(self):
        """不给 `boards` ⇒ 播种项目级 `boards.json`（与旧行为一致）。"""
        _write_project_boards(self, ["需求", "开发"])
        engine = Engine(cwd=self.tmp)          # 先建文件、再建引擎（cfg 在构造时读）
        engine.init_tree("t", "g")
        self.assertEqual(engine._tree_stages(engine.tree("t")), ("需求", "开发"))

    # ── 判据 2：可重放（换配置不影响旧树）────────────────────────────────
    def test_replay_is_independent_of_the_config_file(self):
        self.engine.init_tree("t", "g", boards=(Board("A", "A"), Board("B", "B")))
        _write_project_boards(self, ["X", "Y", "Z"])          # 之后改项目配置
        fresh = Engine(cwd=self.tmp)
        self.assertEqual(fresh.cfg.stages, ("X", "Y", "Z"), "配置确实变了")
        self.assertEqual(fresh._tree_stages(fresh.tree("t")), ("A", "B"), "旧树读数不变")
        # 投影重放同口径：板来自事件，不读配置
        events = store.read_jsonl(store.events_path(vocab.STORAGE))
        self.assertEqual([b["id"] for b in project(events)["trees"]["t"]["boards"]], ["A", "B"])

    def test_new_tree_picks_up_the_new_config(self):
        self.engine.init_tree("t1", "g")
        _write_project_boards(self, ["X", "Y"])
        Engine(cwd=self.tmp).init_tree("t2", "g")
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t2")), ("X", "Y"))

    # ── 判据 3：闭集按该树 ───────────────────────────────────────────────
    def test_stage_validation_uses_the_trees_own_boards(self):
        self.engine.init_tree("t", "g", boards=(Board("a", "A"), Board("b", "B")))
        self.engine.expand("t", "root", [{"id": "n", "stage": "a", "goal": "做"}])
        with self.assertRaises(EngineError) as ctx:
            self.engine.expand("t", "root", [{"id": "m", "stage": "dev", "goal": "做"}])
        self.assertIn("该树允许", str(ctx.exception))

    def test_another_trees_stage_is_not_accepted(self):
        self.engine.init_tree("t1", "g", boards=(Board("a", "A"),))
        self.engine.init_tree("t2", "g", boards=(Board("x", "X"),))
        with self.assertRaises(EngineError):
            self.engine.expand("t1", "root", [{"id": "n", "stage": "x", "goal": "做"}])

    # ── 判据 4：默认板随树 ───────────────────────────────────────────────
    def test_default_stage_follows_the_tree(self):
        # 板集没有内建默认板 `dev` ⇒ 未声明的任务落**入口板**（板序第一块）
        self.engine.init_tree("t", "g", boards=(Board("调研", "调研"), Board("实验", "实验")))
        self.engine.expand("t", "root", [{"id": "n", "goal": "做"}])
        self.assertEqual(self.engine.tree("t")["nodes"]["n"]["stage"], "调研")

    def test_default_stage_keeps_dev_when_present(self):
        self.engine.init_tree("t", "g", boards=(Board("req", "需求"), Board("dev", "开发")))
        self.engine.expand("t", "root", [{"id": "n", "goal": "做"}])
        self.assertEqual(self.engine.tree("t")["nodes"]["n"]["stage"], "dev")

    # ── 判据 5：旧事件流兼容 ─────────────────────────────────────────────
    def test_old_event_stream_without_boards_falls_back_to_config(self):
        store.append_event(vocab.STORAGE, "tree.init", project=project, slug="t",
                           node="", extra={"goal": "老树"})   # 不带 boards
        engine = Engine(cwd=self.tmp)
        self.assertEqual(engine.tree("t")["boards"], [])
        self.assertEqual(engine._tree_stages(engine.tree("t")), engine.cfg.stages)

    # ── 判据 6：set_boards 可改 ──────────────────────────────────────────
    def test_set_boards_changes_validation_and_view(self):
        self.engine.init_tree("t", "g")
        self.engine.set_boards("t", (Board("only", "唯一"),), reason="换流程")
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t")), ("only",))
        with self.assertRaises(EngineError):
            self.engine.expand("t", "root", [{"id": "n", "stage": "dev", "goal": "做"}])
        layout = board_layout(self.engine.tree("t"), None,
                              self.engine._tree_stages(self.engine.tree("t")))
        # 新板在首位且为规范列；root 仍带建树时的 `req`（**不静默改写已有节点**）——
        # 视图按「读时容忍」把它当非规范列排到末尾（容忍 ≠ 静默隐藏）。
        self.assertEqual(layout["columns"][0]["stage"], "only")
        self.assertTrue(layout["columns"][0]["canonical"])
        tail = [c for c in layout["columns"] if c["stage"] == "req"]
        self.assertEqual(len(tail), 1)
        self.assertFalse(tail[0]["canonical"])

    def test_set_boards_requires_a_reason_and_rejects_reserved(self):
        self.engine.init_tree("t", "g")
        with self.assertRaises(EngineError):
            self.engine.set_boards("t", (Board("a", "A"),), reason="")
        with self.assertRaises(BoardsError):       # `done` 是状态不是板
            self.engine.set_boards("t", (Board("done", "完成"),), reason="x")


class PerTreeBoardsCliTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def test_init_boards_flag_records_and_renders(self):
        args = build_parser().parse_args(
            ["init", "t", "--goal", "g", "--boards", "调研,实验,写作"])
        self.assertEqual(cmd_init(self.engine, args), 0)
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t")),
                         ("调研", "实验", "写作"))

    def test_init_boards_accepts_a_json_file(self):
        path = os.path.join(self.tmp, "custom.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"boards": [{"id": "a", "label": "甲"}, {"id": "b", "label": "乙"}]}, handle)
        args = build_parser().parse_args(["init", "t", "--goal", "g", "--boards", path])
        self.assertEqual(cmd_init(self.engine, args), 0)
        self.assertEqual(self.engine._tree_stages(self.engine.tree("t")), ("a", "b"))

    def test_board_uses_the_trees_boards_not_the_project_config(self):
        build = build_parser().parse_args(["init", "t", "--goal", "g", "--boards", "A,B"])
        cmd_init(self.engine, build)
        _write_project_boards(self, ["X", "Y", "Z"])           # 项目配置另有其板
        args = build_parser().parse_args(["board", "t"])
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cmd_board(Engine(cwd=self.tmp), args), 0)
        self.assertIn("列 A 0/0 · B 0/0", out.getvalue())


if __name__ == "__main__":
    unittest.main()
