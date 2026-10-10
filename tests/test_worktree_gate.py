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
  - **A5**：**原地派发 + worker 自己 commit** ⇒ 门仍看见 introduced（`git status` 为空，
    靠 `start` 记的 `head_sha` 看见已提交改动）——这正是「`else None`」那个 fail-open 分支；
  - **fail-closed（顺序错）**：开工基线不在门目录里（start 时无 worktree、done 时有）⇒
    响亮拒收，报错给出 `disflow reopen …` 补救；
  - **fail-closed（对称化）**：start 记录有 worktree，done 前整个 worktree 被删（reap/手工）⇒
    也从**记录**判出、响亮拒收——不能从现状反推（现状说「没有 worktree」会把整段检查跳过，
    门退回主工作区 ⇒ 又一次空洞通过）；
  - **路径归一**：state_dir 与进程侧符号链接前缀不同形（`/private/var/…` 与 `/var/…`）
    ⇒ 同一目录，**不得**误拒（假红）；
  - **brief / collect 的 worktree 面**：brief 给绝对路径 + 分支 + 「不是仓根」说明；
    collect 拒收越出 worktree 的自报 changed_files；
  - **B（规模读数）**：`size_budget` 对**已提交**改动不再恒真空（复用 `start` 记的
    `head_sha`）；未提交场景行数逐字节不变；
  - **R（rescope 守卫）**：worktree 里先越界改并提交，再 `rescope` 补进声明 ⇒ 守卫必须
    与 `done` 的门**同一目录源**（`_gate_cwd`）与**同一基线**（`start` 记的 `head_sha`）
    拒收；只改目录、不改基线仍会漏掉已提交的越界改动；
  - **E（宿主循环契约）**：无已落盘 collect 的 `done` 在 **CLI 层**拒收（exit 1，报文
    给出 `collect` 命令），`--no-result "<理由>"` 显式破例且理由落进事件；**引擎层**不拒收
    （直干型组合合法），但每次 `node.done` 都记 `evidence.result_collected`。

每条断言都配过负向对照（改坏实现必须变红），见交付说明。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import worktree
from disflow.cli import main
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

    # ── A1′：工作区在 done 之前被删 ⇒ 必须 fail-closed（第一判据对称化）──

    def test_a1_vanished_worktree_is_rejected(self):
        """start 记录了 worktree，done 前整个 worktree 被删（reap/手工）⇒ done 必须拒收。

        修前：`_gate_cwd` 退回仓根 ⇒ in_worktree=False ⇒ 整段 fail-closed 被跳过；base_sha 也
        变 None ⇒ 门退回主工作区比对 ⇒ 又一次空洞通过（done exit=0，节点被标记 done）。
        """
        self._node("a", acceptance={"cmd": "test -f OLD.txt", "expect": "exit0"},
                   touch_plan=["OLD.txt"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")
        self.assertTrue(self.engine.tree("t")["nodes"]["a"]["workspace"]["worktree"])

        os.remove(os.path.join(wpath, "OLD.txt"))
        commit(wpath, "delete OLD.txt")
        shutil.rmtree(wpath)                       # 整个工作区消失（reap / 手工删）

        with self.assertRaises(EngineError) as ctx:
            self.engine.done("t", "a")
        message = str(ctx.exception)
        self.assertIn("开工工作区已不存在", message)
        self.assertIn("disflow worktree apply t", message)
        self.assertIn("disflow reopen t", message)
        # 拒收 = 不落 node.done：节点仍 running，没被静默标记完成
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "running")

    def test_a1_symlinked_state_dir_text_is_not_a_false_reject(self):
        """两侧路径**文本不同但 realpath 相同**（state_dir 与进程侧符号链接前缀不同形）
        ⇒ 同一目录，done 必须**不**误拒（假红）。
        """
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        link_parent = tempfile.mkdtemp(prefix="disflow-link-")
        self.addCleanup(shutil.rmtree, link_parent, ignore_errors=True)
        link = os.path.join(link_parent, "state")
        os.symlink(self.tmp, link)                 # link/… 与 self.tmp/… 是同一目录
        self.addCleanup(os.environ.__setitem__, "DISFLOW_STATE_DIR", self.tmp)

        os.environ["DISFLOW_STATE_DIR"] = link     # start 走符号链接文本
        self._worktree("a")
        self.engine.start("t", "a", "w")
        recorded = self.engine.tree("t")["nodes"]["a"]["workspace"]["cwd"]

        os.environ["DISFLOW_STATE_DIR"] = self.tmp  # done 走另一文本、同一 realpath
        now = os.path.join(self.tmp, "worktrees", "t", "a")
        # 前置：两侧文本确实不同，否则本测试没测到点
        self.assertNotEqual(recorded, now)
        self.assertEqual(os.path.realpath(recorded), os.path.realpath(now))

        touch(os.path.join(now, "README.md"), "changed\n")
        self.engine.done("t", "a")                 # 修前会在此误拒（假红）
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["introduced"], ["README.md"])

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

    # ── A5：原地派发 + worker 自己 commit（修「门看不见已提交的改动」）──

    def test_a5_inplace_committed_change_is_seen(self):
        """无 worktree（原地派发）+ worker 自己 `git commit` ⇒ introduced 必须非空。

        修前：`done` 只对 worktree 传 `base_sha`，原地派发时它恒为 None ⇒ 门只读
        `git status --porcelain`；提交后它为空 ⇒ 读数
        introduced=[]、vacuous={design_first,size_budget}、done exit=0（宿主实测的空洞通过）。
        同时钉住第 1 条：原地派发也在 `start` **一律**记下 `head_sha`。
        """
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        self.engine.start("t", "a", "w")
        workspace = self.engine.tree("t")["nodes"]["a"]["workspace"]
        self.assertFalse(workspace["worktree"])          # 前置：确实是原地派发
        self.assertTrue(workspace.get("head_sha"))       # 第 1 条：一律记 head_sha

        touch(os.path.join(self.repo, "README.md"), "changed\n")
        commit(self.repo, "change README in place")

        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        # `git status --porcelain` 现在为空；introduced 非空只能来自 base_sha 的已提交并集
        self.assertEqual(evidence["introduced"], ["README.md"])
        self.assertIn("README.md", evidence["changed_files"])
        self.assertNotIn("design_first", evidence["vacuous"])
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "done")

    # ── R：rescope 的守卫与 done 的门同一目录源（不是 self.cwd）─────────

    def test_rescope_guard_follows_worktree_and_sees_committed_change(self):
        """worktree 里先越界改并提交，再 rescope 把该路径补进声明 ⇒ 守卫必须拒收。

        修前：守卫用 `self.cwd`（进程 cwd = 仓根）调 `gates.newly_changed`，而 worktree
        派发时改动落在 `<state>/worktrees/<tree>/<node>`，两者不相交 ⇒ 守卫在主工作区上看到
        干净 ⇒ 放行「先改后补计划」，`scope` 门变成装饰（改完 → rescope → done 无违规，
        违规记录消失；合法出口 `done --decl '{"scope_waiver": …}'` 被绕过而不留痕）。
        """
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        touch(os.path.join(wpath, "B.txt"), "b\n")   # 越界：B.txt 不在 touch_plan 里
        commit(wpath, "out-of-scope change")          # 提交型 worker：`git status` 为空

        with self.assertRaises(EngineError) as ctx:
            self.engine.rescope("t", "a", ["README.md", "B.txt"], "事后补声明")
        self.assertIn("已经改过", str(ctx.exception))
        self.assertIn("scope_waiver", str(ctx.exception))    # 拒收必须给出替代路径
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["touch_plan"],
                         ["README.md"])

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

    # ── B：size_budget 对「已提交改动」不再恒真空 ─────────────────────────

    def test_b_committed_change_size_is_not_vacuous(self):
        """worktree 里改文件并提交 ⇒ size.total 必须非 0，size_budget 不得空洞。

        修前：`diff_size` 用 `git diff HEAD`，提交后它就是 0 ⇒ size.total=0、
        vacuous=[size_budget]（宿主实测 4/4 节点如此）。
        """
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        touch(os.path.join(wpath, "README.md"), "readme\n" + "added\n" * 5)
        commit(wpath, "grow README")

        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["introduced"], ["README.md"])
        self.assertGreater(evidence["size"]["total"], 0)
        self.assertNotIn("size_budget", evidence["vacuous"])

    def test_b_inplace_committed_change_size_is_not_vacuous(self):
        """原地派发（无 worktree）+ 提交 ⇒ 同样非 0：判据不区分 worktree 与原位。"""
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        self.engine.start("t", "a", "w")
        touch(os.path.join(self.repo, "README.md"), "readme\n" + "added\n" * 3)
        commit(self.repo, "grow README in place")

        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertGreater(evidence["size"]["total"], 0)
        self.assertNotIn("size_budget", evidence["vacuous"])

    def test_b_uncommitted_size_is_unchanged(self):
        """回归：未提交场景的行数读数与旧行为一致（README 加 4 行 ⇒ total=4）。"""
        self._node("a", acceptance=acceptance("ok"), touch_plan=["README.md"])
        wpath = self._worktree("a")
        self.engine.start("t", "a", "w")

        touch(os.path.join(wpath, "README.md"), "readme\n" + "new\n" * 4)
        self.engine.done("t", "a")
        evidence = self.engine.tree("t")["nodes"]["a"]["evidence"]
        self.assertEqual(evidence["size"]["total"], 4)
        self.assertNotIn("size_budget", evidence["vacuous"])


class DoneCollectContractTest(DisflowTestCase):
    """E：宿主循环契约在 CLI 层强制、引擎层显式记账（`result_collected`）。

    为何分层：无 collect 的 `done` 是引擎 API 的**合法组合**（直干型节点不该被逼伪造自报）；
    而事故发生在走 CLI 的宿主——它的 collect 失败时 done 静默落盘，于是 collect（worker
    上报 blocked 的唯一通道）形同虚设。故：CLI 硬拒、引擎记账。
    """

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "readme\n"}, commit=True)
        self._orig_cwd = os.getcwd()
        # `main()` 用 `Engine(cwd=os.getcwd())`——chdir 进仓，使门与引擎同目录。
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self._orig_cwd)

    def _cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _seed(self, node: str = "a", **decl) -> None:
        code, _out, err = self._cli("init", "t", "--goal", "g")
        self.assertEqual(code, 0, err)
        child = {"id": node, "goal": "干活", "acceptance": acceptance("ok"), **decl}
        code, _out, err = self._cli("expand", "t", "root", "--children",
                                    json.dumps([child]))
        self.assertEqual(code, 0, err)

    def _node(self, node: str = "a") -> dict:
        return self.engine.tree("t")["nodes"][node]

    # ── 无 collect：CLI 拒收 ──────────────────────────────────────────

    def test_e_cli_done_without_collect_is_rejected(self):
        self._seed()
        self._cli("start", "t", "a", "--holder", "me")
        code, out, err = self._cli("done", "t", "a")
        self.assertEqual(code, 1, f"应 exit 1（数据问题），实得 {code}：{out}{err}")
        self.assertIn("collect", err)
        self.assertIn("disflow collect t a", err, "报文必须给可照抄的 collect 命令")
        self.assertEqual(self._node()["status"], "running", "拒收不许落 done")

    def test_e_cli_done_dry_run_also_rejects_without_collect(self):
        """预演与真跑同判：`--dry-run` 也不能绕过宿主循环契约。"""
        self._seed()
        self._cli("start", "t", "a", "--holder", "me")
        code, _out, err = self._cli("done", "t", "a", "--dry-run")
        self.assertEqual(code, 1, f"预演应与真跑同判：{err}")

    # ── --no-result 破例：放行 + 留痕 ────────────────────────────────

    def test_e_cli_no_result_escape_allows_and_records_reason(self):
        self._seed()
        self._cli("start", "t", "a", "--holder", "me")
        code, out, err = self._cli("done", "t", "a", "--no-result",
                                   "宿主直干，没有 worker 结果")
        self.assertEqual(code, 0, f"{out}{err}")
        evidence = self._node()["evidence"]
        self.assertIs(evidence["result_collected"], False)
        self.assertEqual(evidence["no_result"], "宿主直干，没有 worker 结果")
        self.assertIn("无 worker 结果", out, "破例也要在 stdout 警告一句")
        self.assertEqual(self._node()["status"], "done")

    # ── 有 collect：正常 + 记账 true ─────────────────────────────────

    def test_e_cli_with_collect_succeeds_and_records_true(self):
        self._seed()
        self._cli("start", "t", "a", "--holder", "me")
        code, _out, err = self._cli("collect", "t", "a", "--result",
                                    '{"status":"ok","summary":"s","changed_files":[]}')
        self.assertEqual(code, 0, err)
        code, out, err = self._cli("done", "t", "a")
        self.assertEqual(code, 0, f"{out}{err}")
        self.assertIs(self._node()["evidence"]["result_collected"], True)
        self.assertNotIn("无 worker 结果", out)

    # ── 引擎层是机制：无 collect 的 done 合法，但必须记账 ──────────────

    def test_e_engine_done_without_collect_is_allowed_and_accounted(self):
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "g", "acceptance": acceptance("ok")}])
        self.engine.start("t", "a", "me")
        self.engine.done("t", "a")          # 引擎不拒收：直干型组合仍合法
        self.assertIs(self._node()["evidence"]["result_collected"], False)
        self.assertEqual(self._node()["status"], "done")

    def test_e_engine_no_result_contradicts_existing_collect(self):
        """已有 collect 结果还传 `--no-result` = 自相矛盾，响亮拒收（不静默忽略）。"""
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "g", "acceptance": acceptance("ok")}])
        self.engine.start("t", "a", "me")
        self.engine.collect("t", "a",
                            {"status": "ok", "summary": "s", "changed_files": []})
        with self.assertRaises(EngineError):
            self.engine.done("t", "a", no_result="没有结果")


if __name__ == "__main__":
    unittest.main()
