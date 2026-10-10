"""CLI 装配层测试（C1）：建树 / 建图类动词。

**为什么单独有这个文件**：`tests/test_cli.py` 只覆盖了 26 个 `cmd_*` 中的 1 个——
V3 独立验证在 `cmd_close` 上抓到真缺陷（漏传 `dry_run`），说明装配层是薄弱面。
本文件按动词分组覆盖：**每个动词至少验证「接线通了」+「非法输入被拒」两面**。

隔离：`DisflowTestCase` 给每个用例独立 `DISFLOW_STATE_DIR`。
"""
from __future__ import annotations

import importlib
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import spec, store
from disflow.cli import build_parser, main

# 与 tests/test_worktree.py 同款：importlib 运行期解析，绕开 `disflow/__init__` 的再导出登记。
worktree = importlib.import_module("disflow.worktree")

# 宿主点名的 14 个选项（动词 → 该动词下必须有非空 help 的选项）；模块级常量避免可变类属性。
_FLAGGED_HELP_OPTIONS = {
    "init": ("--goal",),
    "expand": ("--children",),
    "start": ("--holder",),
    "collect": ("--result",),
    "done": ("--decl",),
    "fail": ("--reason",),
    "skip": ("--reason",),
    "drive": ("--json",),
    "status": ("--json",),
    "rebuild": ("--check",),
    "board": ("--stage",),
    "graph": ("--dot",),
    "import": ("--tree",),
    "context": ("--set",),
}


class CliBuildVerbTest(DisflowTestCase):
    """init / goal / expand / require / relate / produce / consume / context"""

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _state(self) -> dict:
        return store.read_json(store.projection_path(spec.SPEC))

    def _tree(self, tree: str = "t") -> dict:
        return self._state()["trees"][tree]

    def _node(self, node: str, tree: str = "t") -> dict:
        return self._tree(tree)["nodes"][node]

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(spec.SPEC)))

    # ── init / goal ───────────────────────────────────────────────────────

    def test_init_creates_tree_with_goal(self):
        code, _out, _err = self._run(["init", "t", "--goal", "目标一"])
        self.assertEqual(code, 0)
        self.assertEqual(self._tree()["goal"], "目标一")
        self.assertEqual(self._tree()["root"], "root")

    def test_goal_updates_existing_tree(self):
        self._run(["init", "t", "--goal", "旧"])
        code, _out, _err = self._run(["goal", "t", "新"])
        self.assertEqual(code, 0)
        self.assertEqual(self._tree()["goal"], "新")

    def test_init_twice_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, err = self._run(["init", "t", "--goal", "g"])
        self.assertNotEqual(code, 0, "同名树重复 init 必须被拒")
        self.assertIn("✗", err)

    # ── expand ────────────────────────────────────────────────────────────

    def test_expand_creates_children_with_declared_fields(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, _err = self._run(["expand", "t", "root", "--children",
            '[{"id":"a","goal":"干活","stage":"verify","role":"qa",'
            '"touch_plan":["src/a.py"],"awaits":["other"]}]'])
        self.assertEqual(code, 0)
        node = self._node("a")
        self.assertEqual(node["goal"], "干活")
        self.assertEqual(node["stage"], "verify")
        self.assertEqual(node["role"], "qa")
        self.assertEqual(node["touch_plan"], ["src/a.py"])
        self.assertEqual(node["awaits"], ["other"])

    def test_expand_bad_json_reports_error_not_traceback(self):
        self._run(["init", "t", "--goal", "g"])
        code, _out, err = self._run(["expand", "t", "root", "--children", "{不是json}"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err)
        self.assertNotIn("Traceback", err)

    # ── require / relate ──────────────────────────────────────────────────

    def test_require_records_dependency_edge(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        code, _out, _err = self._run(["require", "t", "b", "a"])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("b")["requires"], ["a"])

    def test_require_rejects_cycle(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self._run(["require", "t", "b", "a"])
        code, _out, err = self._run(["require", "t", "a", "b"])
        self.assertNotEqual(code, 0, "成环必须写时拒收")
        self.assertIn("环", err)

    def test_require_self_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"a","goal":"A"}]'])
        code, _out, _err = self._run(["require", "t", "a", "a"])
        self.assertNotEqual(code, 0)

    def test_unrequire_removes_the_edge_and_unblocks_drive(self):
        """删边的价值要看**就绪波次真的变了**，而不只看字段被抹了。"""
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self._run(["require", "t", "b", "a"])
        _code, blocked, _err = self._run(["drive", "t"])
        self.assertIn("b ← a", blocked)                  # 加边后 b 被阻塞

        code, out, _err = self._run(["unrequire", "t", "b", "a"])
        self.assertEqual(code, 0)
        self.assertIn("不再 requires", out)
        self.assertEqual(self._node("b")["requires"], [])
        _code, freed, _err = self._run(["drive", "t"])
        self.assertNotIn("b ← a", freed)
        self.assertIn("  b  ", freed)                     # b 出现在就绪波次里

    def test_unrequire_missing_edge_goes_to_stderr_and_writes_nothing(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        before = self._events()
        code, out, err = self._run(["unrequire", "t", "b", "a"])
        self.assertNotEqual(code, 0)
        self.assertIn("✗", err, "错误报文必须走 stderr")
        self.assertEqual(out, "")
        self.assertEqual(self._events(), before, "被拒的写入不得落事件")

    def test_relate_is_soft_and_may_cycle(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self._run(["relate", "t", "a", "b"])
        code, _out, _err = self._run(["relate", "t", "b", "a"])   # 软引用可成环
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["relates"], ["b"])
        self.assertEqual(self._node("b")["relates"], ["a"])

    # ── produce / consume ─────────────────────────────────────────────────

    def test_produce_then_consume_registers_asset(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A"},{"id":"b","goal":"B"}]'])
        self.assertEqual(self._run(["produce", "t", "a", "schema"])[0], 0)
        self.assertEqual(self._run(["consume", "t", "b", "schema"])[0], 0)
        self.assertEqual(self._tree()["assets"]["schema"], "a")
        self.assertEqual(self._node("b")["consumes"], ["schema"])

    def test_consume_unregistered_asset_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"b","goal":"B"}]'])
        code, _out, err = self._run(["consume", "t", "b", "不存在的东西"])
        self.assertNotEqual(code, 0, "消费未登记产出必须被拒（FR-10）")
        self.assertIn("✗", err)

    # ── context ───────────────────────────────────────────────────────────

    def test_context_merge_is_cumulative(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"a","goal":"A"}]'])
        self._run(["context", "t", "a", "--set", '{"k": 1, "背景": "x"}'])
        code, _out, _err = self._run(["context", "t", "a", "--set", '{"j": 2}'])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["context"], {"k": 1, "背景": "x", "j": 2})


class CliHelpTextTest(DisflowTestCase):
    """每个选项在 `--help` 里都要有非空 help；先点名钉住曾漏写的 14 个。

    宿主实测：这 14 个选项的 help 为 **None** ⇒ `disflow <动词> --help` 只显示选项名，
    看不出要什么、什么形状（`collect --result` 要 JSON、`done --decl` 要 JSON 最易用错）。
    改名/搬位置由 `tests/test_docs.py` 管；本类只管「选项必须说清楚」。
    """

    def _verb_parsers(self) -> dict:
        parser = build_parser()
        # 与 test_docs.py 同款：argparse 私有结构，给一次显式 ignore。
        return dict(parser._subparsers._group_actions[0].choices)  # type: ignore[union-attr]

    @staticmethod
    def _help_map(node) -> dict:
        return {opt: action.help
                for action in node._actions
                for opt in action.option_strings}

    def test_the_14_flagged_options_have_non_empty_help(self):
        parsers = self._verb_parsers()
        for verb, options in _FLAGGED_HELP_OPTIONS.items():
            self.assertIn(verb, parsers)
            helps = self._help_map(parsers[verb])
            for option in options:
                text = helps.get(option)
                self.assertTrue((text or "").strip(),
                                f"{verb} {option} 的 help 为空：{text!r}")

    def test_no_option_anywhere_is_help_less(self):
        """结构性守卫：任一动词的任一选项 help 都不得为空（防新增选项又漏写）。"""
        offenders = []
        for verb, node in self._verb_parsers().items():
            for action in node._actions:
                if action.option_strings and not (action.help or "").strip():
                    offenders.append(f"{verb} {action.option_strings[0]}")
        self.assertEqual(offenders, [], f"以下选项没有 help：{offenders}")


class CliWorktreeReapForceTest(DisflowTestCase):
    """`worktree reap --force`：显式破例强删 + 记账（CLI 侧可达）。

    宿主实测：`docs/design.md` 约定「单一显式 force=True，并记录 discarded」，但 CLI 的
    `worktree` 没暴露 `--force` ⇒ 记账路径从命令行不可达。本类钉住「可达 + 留痕 + 不传逐字不变」。
    """

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)                     # main() 用 os.getcwd() 建 Engine

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _seed_dirty_leak(self, node: str = "a") -> str:
        """建一个「已终态但 worktree 还在、且带未提交改动」的可回收项。"""
        self._run(["init", "t", "--goal", "force"])
        self._run(["expand", "t", "root", "--children",
                   json.dumps([{"id": node, "goal": node, "acceptance": acceptance()}],
                              ensure_ascii=False)])
        self._run(["worktree", "apply", "t", "--nodes", node])
        path = worktree.path_for(spec.SPEC, "t", node)
        touch(os.path.join(path, "wip.txt"), "wip\n")
        self._run(["skip", "t", node, "--reason", "已收工"])
        return path

    def test_force_records_discarded_and_uses_forced_remove(self):
        """传 `--force`：打印强删命令，并把将丢弃的项记进「记账」块。"""
        path = self._seed_dirty_leak()
        code, out, err = self._run(["worktree", "reap", "t", "--force"])
        self.assertEqual(code, 0, err)
        self.assertIn("显式 --force：破例强删", out)
        self.assertIn("记账", out)
        self.assertIn("git 会拒绝", out)                    # 丢什么（事实）被记下
        self.assertIn(f"git worktree remove --force {path}", out)

    def test_without_force_no_ledger_and_safe_command(self):
        """不传 `--force`：无记账块、命令是**安全删**（与加参前逐字一致）。"""
        path = self._seed_dirty_leak()
        code, out, err = self._run(["worktree", "reap", "t"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("记账", out)
        self.assertNotIn("remove --force", out)
        self.assertIn(f"git worktree remove {path}", out)

    def test_force_rejected_for_non_reap_actions(self):
        """`--force` 只对 `reap` 有意义；其余动作传它一律响亮拒收（不静默忽略）。"""
        self._run(["init", "t", "--goal", "g"])
        for action in ("plan", "list", "apply"):
            with self.subTest(action=action):
                code, _out, err = self._run(["worktree", action, "t", "--force"])
                self.assertNotEqual(code, 0, f"{action} 传 --force 应响亮报错")
                self.assertIn("不支持 --force", err)


if __name__ == "__main__":
    unittest.main()
