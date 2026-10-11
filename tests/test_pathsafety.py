"""命名路径安全（R1/R2/R3）：树名与节点 id 不得是路径形，worktree 路径构造 fail-closed。

背景（主控实测）：`init '../../evil'` 与 `expand` 带 `../` 的 id 之前照收，坏 id 进了事实源；
`path_for('..')` 返回根本身、`path_for('../../../../../../tmp/pwned')` 直接逃出 root。
本文件把三件事钉住（每条都配负向对照，见交付说明）：

- **R1 写时校验**：树名（`Engine.init_tree`）与节点 id（`validate_node_spec`）都拒路径形名字，
  且**不落事件**（跑完读事件数，必须与跑前相同）。
- **R2 结构性兜底**：`worktree.path_for` 算完路径必须断言严格在 `root(storage)` 之下，否则抛错——
  **就算事实源里已有坏名字（老状态目录）也不放行**。
- **R3 `apply` 失败一行人话**：git 建失败时 CLI 输出**不含 `Traceback`**、退出码 1。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, git_repo

from disflow import store, worktree
from disflow.cli import main
from disflow.engine import Engine
from disflow.model import validate_node_spec, validate_path_name
from disflow.vocab import STORAGE

# 路径形名字表（树名与节点 id 共用）。`a／b` 里的 `／` 是全角斜杠（U+FF0F）。
BAD_NAMES = ["", "  ", ".", "..", "a/b", "../x", "x/..", "a\\b", "a／b"]
# 合法名（防「修过头」）：现有 id 形态多样，一律不许误杀。
GOOD_NAMES = ["n0", "root", "a", "49e3af9a95b1", "中文名"]
# 真正能**逃出 root** 的路径形（R2 必须拦；`a/b` / `a／b` / `空格` 等在 POSIX 上
# 不构成上跳，只由 R1 拦——R2 的契约是「不逃出 root」，不是「拒所有怪名」）。
ESCAPING_NODE_NAMES = ["..", "../../x", "../../../../../../tmp/pwned", "x/../../.."]
ESCAPING_TREE_NAMES = ["..", "../x", "../../evil", "x/../.."]


class NameValidationTest(DisflowTestCase):
    """R1：写时校验 —— 坏名字拒收且不落事件；好名字照过。"""

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(STORAGE)))

    # ── 树名 ────────────────────────────────────────────────────────────

    def test_bad_tree_names_rejected_without_event(self):
        engine = Engine()
        for name in BAD_NAMES:
            before = self._events()
            with self.subTest(tree=name):
                with self.assertRaises(ValueError):
                    engine.init_tree(name, "g")
                self.assertEqual(self._events(), before,
                                 f"坏树名 {name!r} 不该落事件")
                self.assertNotIn(name, engine.trees())

    def test_good_tree_names_accepted(self):
        engine = Engine()
        for name in GOOD_NAMES:
            with self.subTest(tree=name):
                engine.init_tree(name, "g")           # 不抛即通过
                self.assertIn(name, engine.trees())

    # ── 节点 id ─────────────────────────────────────────────────────────

    def test_bad_node_ids_rejected_by_validate_node_spec(self):
        for name in BAD_NAMES:
            with self.subTest(node=name):
                with self.assertRaises(ValueError):
                    validate_node_spec({"id": name})
                with self.assertRaises(ValueError):
                    validate_path_name(name, what="节点 id")

    def test_bad_node_ids_rejected_without_event(self):
        engine = Engine()
        engine.init_tree("t", "g")
        before = self._events()
        for name in BAD_NAMES:
            with self.subTest(node=name):
                with self.assertRaises(ValueError):
                    engine.expand("t", "root", [{"id": name}])
                self.assertEqual(self._events(), before,
                                 f"坏节点 id {name!r} 不该落事件")
        # 投影里只有 root，没有被放进任何坏 id。
        self.assertEqual(list(engine.tree("t")["nodes"]), ["root"])

    def test_good_node_ids_accepted(self):
        for name in GOOD_NAMES:
            with self.subTest(node=name):
                validate_node_spec({"id": name})      # 不抛即通过
        engine = Engine()
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n0"}, {"id": "a"},
                                    {"id": "49e3af9a95b1"}, {"id": "中文名"}])
        self.assertEqual(sorted(engine.tree("t")["nodes"]),
                         ["49e3af9a95b1", "a", "n0", "root", "中文名"])

    def test_validate_node_spec_without_id_is_fine(self):
        # id 缺省由 `next_id` 生成安全 id —— 不该因「没有 id」误拒。
        validate_node_spec({"goal": "g"})


class PathForFailClosedTest(DisflowTestCase):
    """R2：`path_for` 必须 fail-closed —— 绝不返回逃逸路径。"""

    def test_path_for_rejects_bad_node(self):
        for name in ESCAPING_NODE_NAMES:
            with self.subTest(node=name), self.assertRaises(ValueError):
                worktree.path_for(STORAGE, "t", name)

    def test_path_for_rejects_bad_tree(self):
        for name in ESCAPING_TREE_NAMES:
            with self.subTest(tree=name), self.assertRaises(ValueError):
                worktree.path_for(STORAGE, name, "a")

    def test_path_for_good_is_strictly_under_root(self):
        root = os.path.realpath(worktree.root(STORAGE))
        path = os.path.realpath(worktree.path_for(STORAGE, "t", "a"))
        self.assertTrue(path.startswith(root + os.sep), path)
        self.assertNotEqual(path, root)

    def test_bad_name_cannot_escape_root(self):
        # 直接钉住「逃逸」本身：任何坏名字的结果要么抛错，要么（若真返回）仍在 root 下。
        root = os.path.realpath(worktree.root(STORAGE))
        for tree, node in [("t", ".."), ("t", "../../../../../../tmp/pwned"),
                           ("..", "a"), ("../../evil", "a")]:
            with self.subTest(tree=tree, node=node):
                try:
                    path = os.path.realpath(worktree.path_for(STORAGE, tree, node))
                except ValueError:
                    continue
                self.fail(f"{tree=} {node=} 返回了逃逸路径 {path!r}（root={root}）")


class LegacyBadStateTest(DisflowTestCase):
    """R2：事实源里**已有**坏名字（老状态目录）→ 算路径这一步也不放行。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)                    # main() 用 os.getcwd() 建 Engine

    def _inject_bad_node(self, bad_id: str) -> None:
        """绕过 Engine 直接往投影里塞一个坏 id 节点（模拟修复前落下的老数据）。

        `bad_id` 用**能真正逃出 root** 的路径形（`../../…`）——否则只上跳一级回到 root 内，
        R2（只管逃逸）就不该、也不会拒绝。
        """
        self.engine.init_tree("t", "g")
        path = store.projection_path(STORAGE)
        state = store.read_json(path)
        state["trees"]["t"]["nodes"][bad_id] = {
            "id": bad_id, "parent": "root", "kind": "task", "status": "pending",
        }
        store.write_json_atomic(path, state)

    def test_plan_raises_on_legacy_bad_id(self):
        self._inject_bad_node("../../pwned")
        with self.assertRaises(ValueError):
            worktree.plan(STORAGE, "t", self.repo)

    def test_cli_plan_reports_error_not_escape_path(self):
        self._inject_bad_node("../../../../../../tmp/pwned")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["worktree", "plan", "t"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue() + out.getvalue())
        # 逃逸路径绝不许被当成一份「计划」打出来（不发 add 命令、不给落点行）。
        self.assertNotIn("git worktree add", out.getvalue() + err.getvalue())


class ApplyFailureTest(DisflowTestCase):
    """R3：`apply` 的 git 失败归一到一行 `✗ …` + 退出码 1，不再是裸 traceback。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)

    def _seed_one(self) -> None:
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [{"id": "a"}])

    def _block_worktree_dir(self) -> None:
        """把 `<root>/t` 做成**普通文件**，让 `git worktree add <root>/t/a` 真失败。"""
        root = worktree.root(STORAGE)
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "t"), "w", encoding="utf-8") as handle:
            handle.write("not a dir\n")

    def test_apply_raises_worktree_error(self):
        self._seed_one()
        self._block_worktree_dir()
        with self.assertRaises(worktree.WorktreeError):
            worktree.apply(STORAGE, "t", self.repo)

    def test_cli_apply_failure_is_one_line(self):
        self._seed_one()
        self._block_worktree_dir()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["worktree", "apply", "t"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue() + out.getvalue())


class RealReproCommandsTest(DisflowTestCase):
    """主控实测命令的端到端复跑（修复后必须被拒）。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)

    def test_init_evil_path_rejected(self):
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(["init", "../../evil", "--goal", "x"])
        self.assertEqual(code, 1)
        self.assertIn("✗", err.getvalue())
        self.assertNotIn("../../evil", self.engine.trees())

    def test_expand_bad_id_rejected(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            main(["init", "t", "--goal", "g"])
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = main(["expand", "t", "root", "--children",
                         json.dumps([{"id": "../../../../../../tmp/x"}])])
        self.assertEqual(code, 1)
        self.assertIn("✗", err.getvalue())
        self.assertEqual(list(self.engine.tree("t")["nodes"]), ["root"])


if __name__ == "__main__":
    unittest.main()
