"""CLI 侧 `worktree apply --nodes`（波次建工作区）的端到端测试。

**为什么会有这个文件**：模块侧 `disflow/worktree.py` 的 `apply(..., *, nodes=None)` 早已支持
按节点子集建工作区，但 `disflow/cli.py` 里 `nodes=` 出现 **0 次** ⇒ 本参数在 CLI 侧**不可达**。
为什么会是硬前置：`worktree.commands(..., base="HEAD")` ⇒ `apply` 一律从 **HEAD** 分叉 ⇒
**波 2 建工作区必须在波 1 合并进 HEAD 之后**；没有 `--nodes` 会一次给全部节点建，波 2 的节点
就从「还没有波 1 的 HEAD」分叉，拿不到波 1 的成果。

本文件照 `tests/test_cli_gap3.py` 的既有模式，用 `from disflow.cli import main` 直接驱动，
断言退出码与报文（成功走 stdout、失败走 stderr），并用真 git 仓核对 worktree 登记。

每条硬契约都有对应的**负向对照**（把实现改坏 → 本文件必须变红）：
- **按节点子集**：`--nodes a` 只建 a，不建 b（把 `nodes=nodes` 传参删掉 → 变红）。
- **两种写法等价**：`--nodes a --nodes b` 与 `--nodes a,b` 结果一致。
- **省略即全量**：不传 `--nodes` 时不限定节点（与加参前逐字一致）。
- **响亮拒收**：空串 / 含空项 → argparse error（退出码 2）；非活节点走 `not_applicable` 分支响亮报出；
  不存在的节点走模块 KeyError → `EngineError`（退出码 1）。
- **不静默忽略**：`plan`/`list`/`reap` 传 `--nodes` 一律响亮报错（退出码 1），不当作没传。

`DISFLOW_STATE_DIR` 由 `tests/base.py` 的 `DisflowTestCase.setUp` 放到**仓外**临时目录——
`worktree` 有一道守卫会拒绝「worktree 路径落在 repo 内部」，state dir 在仓内会直接报错。
"""
from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance, git_repo

from disflow.cli import main
from disflow.vocab import STORAGE

# 与 test_worktree.py 同款：importlib 运行期解析，绕开 `disflow/__init__` 的再导出登记。
worktree = importlib.import_module("disflow.worktree")


def _worktree_paths(repo: str) -> list[str]:
    """`git worktree list --porcelain` 登记的路径（排序）。"""
    proc = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=True)
    return sorted(line[len("worktree "):] for line in proc.stdout.splitlines()
                  if line.startswith("worktree "))


class CliWorktreeNodesTest(DisflowTestCase):
    """`worktree apply --nodes`：真 git 仓 + 仓外 state dir。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)                     # main() 用 os.getcwd() 建 Engine

    # ── 驱动助手 ─────────────────────────────────────────────────────────

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _seed(self, ids: list[str]) -> None:
        self._run(["init", "t", "--goal", "nodes"])
        self._run(["expand", "t", "root", "--children",
                   json.dumps([{"id": nid, "goal": nid, "acceptance": acceptance()}
                               for nid in ids], ensure_ascii=False)])

    def _node_path(self, node: str) -> str:
        return worktree.path_for(STORAGE, "t", node)

    # ── B1：只建点名节点 ─────────────────────────────────────────────────

    def test_apply_nodes_builds_only_named_node(self):
        """`apply t --nodes a` 只建 a：`worktree list t` 把在跑却没建的 b 报成 missing，a 不报。"""
        self._seed(["a", "b"])
        self._run(["start", "t", "a", "--holder", "me"])
        self._run(["start", "t", "b", "--holder", "me"])
        code, out, err = self._run(["worktree", "apply", "t", "--nodes", "a"])
        self.assertEqual(code, 0, err)
        self.assertIn("建了 1 个", out)
        paths = {os.path.realpath(p) for p in _worktree_paths(self.repo)}
        self.assertIn(os.path.realpath(self._node_path("a")), paths, "应建出 a")
        self.assertNotIn(os.path.realpath(self._node_path("b")), paths, "b 未被点名，不许建")
        self.assertTrue(os.path.isdir(self._node_path("a")))
        self.assertFalse(os.path.exists(self._node_path("b")), "b 未被点名，不许建")
        # `worktree list t` 为证：a 已就绪、b 仍缺（b 在跑却没工作区）
        code2, out2, err2 = self._run(["worktree", "list", "t"])
        self.assertEqual(code2, 0, err2)
        self.assertIn("missing   b", out2, "没建的 b 应被报成 missing")
        self.assertNotIn("missing   a", out2, "已建的 a 不该再报 missing")

    def test_apply_nodes_repeat_flag_equals_comma_form(self):
        """`--nodes a --nodes b` 与 `--nodes a,b` 都建 a、b，结果一致。"""
        self._seed(["a", "b", "c"])
        code, out, err = self._run(["worktree", "apply", "t", "--nodes", "a", "--nodes", "b"])
        self.assertEqual(code, 0, err)
        self.assertIn("建了 2 个", out)
        self.assertTrue(os.path.isdir(self._node_path("a")))
        self.assertTrue(os.path.isdir(self._node_path("b")))
        self.assertFalse(os.path.exists(self._node_path("c")), "c 未被点名，不许建")

        # 逗号写法：另起一棵树对照，结果集合与重复写法一致
        self._run(["init", "u", "--goal", "comma"])
        self._run(["expand", "u", "root", "--children",
                   json.dumps([{"id": nid, "goal": nid, "acceptance": acceptance()}
                               for nid in ["a", "b", "c"]], ensure_ascii=False)])
        code2, out2, err2 = self._run(["worktree", "apply", "u", "--nodes", "a,b"])
        self.assertEqual(code2, 0, err2)
        self.assertIn("建了 2 个", out2)
        self.assertTrue(os.path.isdir(worktree.path_for(STORAGE, "u", "a")))
        self.assertTrue(os.path.isdir(worktree.path_for(STORAGE, "u", "b")))
        self.assertFalse(os.path.exists(worktree.path_for(STORAGE, "u", "c")))

    # ── B2：不传 --nodes = 全量，逐字一致 ────────────────────────────────

    def test_apply_without_nodes_builds_all_live_nodes(self):
        """省略 `--nodes`：活节点全建（a、b 都建出），行为与加参前一致。"""
        self._seed(["a", "b"])
        code, out, err = self._run(["worktree", "apply", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("建了 2 个", out)
        self.assertTrue(os.path.isdir(self._node_path("a")))
        self.assertTrue(os.path.isdir(self._node_path("b")))

    # ── B3：非活节点 / 不存在节点：响亮 ──────────────────────────────────

    def test_apply_nodes_non_live_node_reported_not_applicable(self):
        """点名的节点存在但非活（已 skip）→ 走模块 not_applicable 分支，响亮报出、不建。"""
        self._seed(["a", "b"])
        self._run(["skip", "t", "a", "--reason", "已收工"])
        code, out, err = self._run(["worktree", "apply", "t", "--nodes", "a"])
        self.assertEqual(code, 0, err)
        self.assertIn("非活节点，不建工作区", out)
        self.assertIn("a", out)
        self.assertFalse(os.path.exists(self._node_path("a")), "非活节点不许建")

    def test_apply_nodes_nonexistent_node_is_loud(self):
        """点名不存在的节点 → 模块 KeyError → EngineError（退出码 1，stderr 响亮）。"""
        self._seed(["a"])
        code, out, err = self._run(["worktree", "apply", "t", "--nodes", "zzz"])
        self.assertNotEqual(code, 0)
        self.assertIn("✗", err)
        self.assertIn("节点不存在", err)
        self.assertFalse(os.path.exists(self._node_path("a")), "参数校验失败不许有任何副作用")

    # ── 空串 / 空项：响亮拒收（argparse，退出码 2）───────────────────────

    def test_apply_nodes_empty_string_rejected(self):
        self._seed(["a"])
        with self.assertRaises(SystemExit) as caught:
            self._run(["worktree", "apply", "t", "--nodes", ""])
        self.assertEqual(caught.exception.code, 2, "空串应被 argparse 响亮拒收")
        self.assertFalse(os.path.exists(self._node_path("a")))

    def test_apply_nodes_empty_item_rejected(self):
        self._seed(["a", "b"])
        for bad in ("a,,b", "a,", ",a"):
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit) as caught:
                    self._run(["worktree", "apply", "t", "--nodes", bad])
                self.assertEqual(caught.exception.code, 2, f"含空项 {bad!r} 应被拒收")
        self.assertFalse(os.path.exists(self._node_path("a")))

    # ── 不静默忽略：非 apply 动作传 --nodes 一律响亮 ─────────────────────

    def test_nodes_rejected_for_non_apply_actions(self):
        self._seed(["a"])
        for action in ("plan", "list", "reap"):
            with self.subTest(action=action):
                code, out, err = self._run(["worktree", action, "t", "--nodes", "a"])
                self.assertNotEqual(code, 0, f"{action} 传 --nodes 应响亮报错")
                self.assertIn("不支持 --nodes", err)


if __name__ == "__main__":
    unittest.main()
