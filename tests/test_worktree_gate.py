"""worktree 场景下门的目录修复：把「门在错的目录上跑」这条 fail-open 钉死。

**背景（构造性反例）**：`disflow worktree apply <tree>` 把节点工作区建在
`<state_dir>/worktrees/<tree>/<node>`，而门的所有 IO 原本吃 `Engine.cwd`（进程 cwd）。
两者不相交 ⇒ 用 worktree 干活时门在仓根上跑、改动看不见 ⇒ 空洞通过。原始读数：

    acceptance = {"cmd": "test -f OLD.txt", "expect": "exit0"}
    在 worktree 里删掉 OLD.txt 并提交
    → acceptance_ok=true, acceptance.exit=0
    → changed_files=[], introduced=[], baseline=[]
    → vacuous={"design_first": "…introduced 为空", "size_budget": "改动行数为 0"}
    → done 退出码 0，节点被标记 done

本文件逐条钉住：
  - **A1（第一判据）**：worktree 里删掉验收要求存在的文件并提交 ⇒ done 必须**拒收**；
  - **A2**：worktree 里改而不提交 ⇒ 门看到（introduced 非空、无空洞警告）；
  - **A3**：worktree 里改**并提交** ⇒ 门仍看到（`git status` 为空，靠 `base_sha` 的
    已提交改动并集看见）——覆盖「改了什么」判据必须扩；
  - **A4**：无 worktree 的节点行为逐字节不变（回归）；
  - **fail-closed**：开工基线不在门目录里（start 时无 worktree、done 时有）⇒ 响亮拒收，
    报错给出 `disflow reopen …` 补救；
  - **brief / collect 的 worktree 面**：brief 给绝对路径 + 分支 + 「不是仓根」说明；
    collect 拒收越出 worktree 的自报 changed_files。

每条断言都配过负向对照（改坏实现必须变红），见交付说明。
"""
from __future__ import annotations

import os
import subprocess
import unittest

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import worktree
from disflow.engine import EngineError
from disflow.spec import SPEC


def git(cwd: str, *args: str) -> None:
    """在 `cwd` 里跑一条 git 子命令（测试辅助，失败即抛）。"""
    subprocess.run(["git", "-C", cwd, *args], check=True,
                   capture_output=True, text=True)


def commit(cwd: str, message: str) -> None:
    """在 `cwd` 里把当前改动提交（模拟「Commit 型 worker」）。"""
    git(cwd, "add", "-A")
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@t",
        "commit", "-qm", message)


class WorktreeGateTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        # 一个干净起点：OLD.txt / README.md 都在 HEAD 里（worktree apply 会检出它们）。
        self.engine, self.repo = git_repo(
            self, {"OLD.txt": "keep\n", "README.md": "readme\n"}, commit=True)

    def _node(self, node_id: str, **decl) -> None:
        self.engine.init_tree("t", "goal")
        self.engine.expand("t", "root", [{"id": node_id, **decl}])

    def _worktree(self, node_id: str) -> str:
        worktree.apply(SPEC, "t", self.repo, nodes=[node_id])
        return worktree.path_for(SPEC, "t", node_id)

    # ── A1：反例转正（第一判据）──────────────────────────────────────────

    def test_a1_deleted_committed_file_is_rejected(self):
        """worktree 里删掉 acceptance 要求存在的文件并提交 ⇒ done 必须拒收（修前会通过）。"""
        self._node("a", acceptance={"cmd": "test -f OLD.txt", "expect": "exit0"})
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        os.remove(os.path.join(wpath, "OLD.txt"))
        commit(wpath, "delete OLD.txt")

        with self.assertRaises(EngineError) as ctx:
            self.engine.done("t", "a")
        self.assertIn("验收未通过", str(ctx.exception))
        # 拒收 = 不落 node.done：节点仍 running，没被静默标记完成
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "running")

    # ── A2：改而不提交也看得到 ──────────────────────────────────────────

    def test_a2_uncommitted_change_is_seen(self):
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        touch(os.path.join(wpath, "README.md"), "changed\n")     # 不提交

        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["introduced"], ["README.md"])
        self.assertNotIn("design_first", evidence["vacuous"])

    # ── A3：改并提交仍看得到（第 4 条：判据必须扩）──────────────────────

    def test_a3_committed_change_is_seen(self):
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        touch(os.path.join(wpath, "README.md"), "changed\n")
        commit(wpath, "change README")

        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        # git status --porcelain 现在是空的；introduced 非空只能来自 base_sha 并集
        self.assertEqual(evidence["introduced"], ["README.md"])
        self.assertIn("README.md", evidence["changed_files"])
        self.assertNotIn("design_first", evidence["vacuous"])

    # ── A4：无 worktree 的节点行为逐字节不变（回归）────────────────────

    def test_a4_without_worktree_unchanged(self):
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        self.engine.start("t", "a", "w")
        workspace = self.engine.tree("t")["nodes"]["a"]["workspace"]
        self.assertFalse(workspace["worktree"])
        self.assertEqual(workspace["cwd"], self.repo)

        touch(os.path.join(self.repo, "README.md"), "changed\n")
        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["introduced"], ["README.md"])
        self.assertEqual(evidence["vacuous"], {})

    def test_a4_without_worktree_no_change_still_vacuous(self):
        """无 worktree、无改动：与旧行为一致 —— introduced 为空且空洞警告照旧。"""
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        self.engine.start("t", "a", "w")
        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["introduced"], [])
        self.assertIn("design_first", evidence["vacuous"])
        self.assertIn("size_budget", evidence["vacuous"])

    # ── fail-closed：起点与终点不是同一个目录 ───────────────────────────

    def test_fail_closed_when_baseline_dir_differs_from_worktree(self):
        self._node("a", acceptance=acceptance("ok"))
        self.engine.start("t", "a", "w")          # 此刻还没有 worktree：基线取自仓根
        self._worktree("a")                       # 之后才 apply：门目录已变

        with self.assertRaises(EngineError) as ctx:
            self.engine.done("t", "a")
        message = str(ctx.exception)
        self.assertIn("disflow reopen t", message)
        self.assertIn("重新 start", message)

    # ── brief / collect 的 worktree 面 ─────────────────────────────────

    def test_brief_reports_worktree_when_applied(self):
        self._node("a")
        self.assertIsNone(self.engine.brief("t", "a")["worktree"])

        wpath = self._worktree("a")
        payload = self.engine.brief("t", "a")
        self.assertTrue(os.path.isabs(payload["worktree"]["path"]))
        self.assertEqual(os.path.realpath(payload["worktree"]["path"]),
                         os.path.realpath(wpath))
        self.assertEqual(payload["worktree"]["branch"], "disflow/t/a")
        note = payload["context"]["worktree"]
        self.assertIn("不是仓根", note)
        self.assertIn(payload["worktree"]["path"], note)

    def test_collect_rejects_changed_files_outside_worktree(self):
        self._node("a")
        self._worktree("a")
        self.engine.start("t", "a", "w")

        # 相对路径按 worktree 解析 ⇒ 合法
        self.engine.collect("t", "a",
                            {"status": "ok", "summary": "s", "changed_files": ["README.md"]})
        # 绝对路径落在 worktree 之外 ⇒ 拒收
        with self.assertRaises(EngineError):
            self.engine.collect("t", "a", {
                "status": "ok", "summary": "s", "changed_files": ["/etc/hosts"]})


if __name__ == "__main__":
    unittest.main()
