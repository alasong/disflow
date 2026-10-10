"""每节点 git worktree（派生资源）测试。

覆盖的核心契约：
  - 路径 / 分支是纯函数（确定性、幂等）；
  - `plan` **零副作用**（这是「只推不跑」的可执行判据）；
  - **`reap` 是纯计划**：给判据 / 命令，**永不执行任何 git 变更**——最强性质：
    `reap(...)` 在**任何**输入下调用前后状态逐字相同（state dir 内容 hash /
    `git worktree list --porcelain` / 所有 refs 的 sha / worktree 目录内容）；
  - **leaks 与 residual 走同一套判据**：任何一种危险状态（dirty / ignored /
    unmerged-commit / detached / residual-unmerged / 探针坏）都必须
    `safe_to_drop=False` 且 `discard_warnings` 非空；
  - `apply` **真执行**（创建可逆、幂等）；半创建状态响亮归 `broken`；残留分支 re-attach；
  - `reconcile` 认得 leaks / missing / residual、探针失败**直接抛** `WorktreeProbeError`；
  - tree/node 不存在**响亮报错**；
  - worktree 路径**不在 repo 内**（否则污染 git status），且守卫与进程 cwd 无关。

FIX3（守卫 fail-closed by construction）额外覆盖：
  - 同名 tag / 远端名 `disflow` 造成的短名歧义象限在计划里必须被判为不安全；
  - 探测不可判定（`for-each-ref` 失败）→ 计划里留显式 `unreadable-refs:` reason。
FIX4（删从引擎手里拿掉）：执行路径与其测试一并删除；`force` / `removed` / `branches_removed`
  / `discarded` / `restored` / `failed` 这些执行结果字段不复存在。
"""
from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow.spec import SPEC

# 用 importlib 而非 `from disflow import worktree`：`disflow/__init__.py` 的子模块再导出
# 列表不归本 lane 管（CLI 装配由另一人做），未登记的新子模块会被静态分析判为
# 「unknown import symbol」。importlib 在运行期按文件解析，绕开登记，且不越界改 __init__。
worktree = importlib.import_module("disflow.worktree")


class WorktreeTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)

    def _seed(self, ids: list[str]) -> None:
        """建一棵含 `root` 与给定 task 叶子的树（叶子都带验收声明，方便后续 done）。"""
        self.engine.init_tree("t", "goal")
        self.engine.expand("t", "root", [
            {"id": nid, "acceptance": acceptance()} for nid in ids
        ])

    def _path(self, node: str, tree: str = "t") -> str:
        return worktree.path_for(SPEC, tree, node)

    # ── 纯函数 ────────────────────────────────────────────────────────────

    def test_path_and_branch_are_deterministic_and_idempotent(self):
        first = self._path("a")
        second = self._path("a")
        self.assertEqual(first, second)
        self.assertEqual(first, os.path.join(worktree.root(SPEC), "t", "a"))
        self.assertEqual(worktree.branch_for("t", "a"), worktree.branch_for("t", "a"))
        self.assertEqual(worktree.branch_for("t", "a"), "disflow/t/a")

    def test_worktree_path_is_outside_repo(self):
        path = os.path.realpath(self._path("a"))
        repo = os.path.realpath(self.repo)
        self.assertFalse(path == repo or path.startswith(repo + os.sep),
                         f"worktree 落在 repo 内：{path}")

    def test_commands_shape(self):
        cmds = worktree.commands(SPEC, "t", "a", self.repo)
        path = self._path("a")
        self.assertEqual(cmds["add"],
                         ["git", "worktree", "add", "-b", "disflow/t/a", path, "HEAD"])
        # 默认**不带** --force：脏 worktree 由 git 自己响亮拒绝
        self.assertEqual(cmds["remove"], ["git", "worktree", "remove", path])
        forced = worktree.commands(SPEC, "t", "a", self.repo, force=True)
        self.assertEqual(forced["remove"], ["git", "worktree", "remove", "--force", path])

    # ── plan 零副作用（「只推不跑」判据）──────────────────────────────────

    def test_plan_has_no_side_effects(self):
        self._seed(["a", "b"])
        before_registered = worktree.registered(self.repo)
        root_existed = os.path.exists(worktree.root(SPEC))
        # 反复调用也必须前后完全一致
        plan = worktree.plan(SPEC, "t", self.repo)
        plan2 = worktree.plan(SPEC, "t", self.repo)
        self.assertEqual(plan, plan2)                       # 同输入必同输出
        self.assertEqual(worktree.registered(self.repo), before_registered)
        self.assertEqual(os.path.exists(worktree.root(SPEC)), root_existed)
        self.assertFalse(root_existed, "plan 不许创建 worktree 根目录")
        self.assertEqual({e["node"] for e in plan["plan"]}, {"a", "b"})
        for entry in plan["plan"]:
            self.assertFalse(entry["status"]["exists"])
            self.assertEqual(entry["add_cmd"][0:2], ["git", "worktree"])

    # ── apply（真执行；创建可逆、幂等）────────────────────────────────────

    def test_apply_creates_real_worktree(self):
        self._seed(["a", "b"])
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], ["a"])
        self.assertTrue(os.path.isdir(self._path("a")))
        self.assertIn(os.path.realpath(self._path("a")),
                      {os.path.realpath(p) for p in worktree.registered(self.repo)})
        self.assertFalse(os.path.exists(self._path("b")), "只建了 a，b 不该出现")

    def test_apply_marks_status_real(self):
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        st = worktree.status(SPEC, "t", "a", self.repo)
        self.assertTrue(st["exists"] and st["registered"] and st["real"])

    def test_apply_is_idempotent(self):
        self._seed(["a", "b"])
        first = worktree.apply(SPEC, "t", self.repo)
        self.assertEqual(sorted(first["created"]), ["a", "b"])
        second = worktree.apply(SPEC, "t", self.repo)
        self.assertEqual(second["created"], [])
        self.assertEqual(sorted(second["skipped"]), ["a", "b"])
        self.assertEqual(second["broken"], [])

    def test_apply_reattaches_registered_dir_missing(self):
        # FIX2-B2：已注册但目录被手工删了、分支还在 → re-attach（而非卡死/报 broken）
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        shutil.rmtree(self._path("a"))
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["reattached"], ["a"])
        self.assertTrue(os.path.isdir(self._path("a")))
        self.assertTrue(worktree.status(SPEC, "t", "a", self.repo)["real"])

    def test_apply_flags_unregistered_dir_as_broken(self):
        # FIX-B6(2)：目录存在但未注册（手工 mkdir）→ 不得当成已建好
        self._seed(["a"])
        os.makedirs(self._path("a"))
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], [])
        self.assertNotIn("a", result["skipped"])
        self.assertEqual([b["node"] for b in result["broken"]], ["a"])
        self.assertTrue(result["broken"][0]["exists"])
        self.assertFalse(result["broken"][0]["registered"])

    # ── reconcile ─────────────────────────────────────────────────────────

    def test_reconcile_detects_leaks_and_missing(self):
        self._seed(["a", "b", "c"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a", "b"])
        self.engine.skip("t", "a")                          # 终态但 worktree 还在 → leak
        self.engine.start("t", "c", "w1")                  # running 且无 worktree → missing
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual([leak["node"] for leak in report["leaks"]], ["a"])
        self.assertEqual(report["missing"], ["c"])
        self.assertEqual(report["orphans"], [])

    def test_reconcile_leak_carries_dirty_read(self):
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        touch(os.path.join(self._path("a"), "scratch.txt"), "wip\n")
        self.engine.skip("t", "a")
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual(report["leaks"],
                         [{"node": "a", "status": "skipped", "dirty": True,
                           "ignored": []}])

    def test_reconcile_flags_missing_for_running_without_worktree(self):
        self._seed(["a"])
        self.engine.start("t", "a", "w1")                   # running，无 worktree
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual(report["missing"], ["a"])
        self.assertEqual(report["leaks"], [])

    def test_reconcile_flags_orphan_under_root(self):
        self._seed([])
        # 手工造一个注册在 root() 下、却对不上任何已知节点的 worktree
        orphan_path = os.path.join(worktree.root(SPEC), "t", "ghost")
        os.makedirs(os.path.dirname(orphan_path), exist_ok=True)
        subprocess.run(["git", "-C", self.repo, "worktree", "add", "-b", "ghostbranch",
                        orphan_path, "HEAD"], check=True,
                       capture_output=True, text=True)
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual([os.path.realpath(p) for p in report["orphans"]],
                         [os.path.realpath(orphan_path)])

    def test_reconcile_raises_when_probe_fails(self):
        # FIX2-B5：非 git 仓 → 探针失败**直接抛**，不得返回空表（空表会被读成 all-clear）
        self._seed(["a"])
        non_git = tempfile.mkdtemp(prefix="disflow-nongit-")
        self.addCleanup(shutil.rmtree, non_git, ignore_errors=True)
        with self.assertRaises(worktree.WorktreeProbeError):
            worktree.registered(non_git)
        with self.assertRaises(worktree.WorktreeProbeError):
            worktree.reconcile(SPEC, "t", non_git)
        with self.assertRaises(worktree.WorktreeProbeError):
            worktree.reap(SPEC, "t", non_git)
        with self.assertRaises(worktree.WorktreeProbeError):
            worktree.apply(SPEC, "t", non_git)

    # ── reap：纯计划（无执行）─────────────────────────────────────────────

    def test_reap_plans_only_terminal_worktrees(self):
        self._seed(["a", "b"])
        worktree.apply(SPEC, "t", self.repo)
        self.engine.skip("t", "a")                          # a 终态 → 可回收
        self.engine.start("t", "b", "w1")                   # b 在跑 → 不许碰
        plan = worktree.reap(SPEC, "t", self.repo)
        self.assertEqual([leak["node"] for leak in plan["leaks"]], ["a"])
        self.assertEqual(plan["executed"], False)
        self.assertTrue(os.path.isdir(self._path("a")), "reap 不真删")
        self.assertTrue(os.path.isdir(self._path("b")), "在跑节点的 worktree 不许碰")

    def test_reap_plan_lists_safe_commands(self):
        # 干净终态节点：safe_to_drop=True，给出安全删命令（remove 不带 --force、branch 用 -d）
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.engine.skip("t", "a")
        plan = worktree.reap(SPEC, "t", self.repo)
        entry = plan["leaks"][0]
        self.assertEqual(entry["remove_cmd"],
                         ["git", "worktree", "remove", self._path("a")])
        self.assertEqual(entry["branch_cmd"],
                         ["git", "-C", self.repo, "branch", "-d", "disflow/t/a"])
        self.assertTrue(entry["safe_to_drop"])
        self.assertNotIn("discard_warnings", entry)
        self.assertTrue(plan["reliable"])
        self.assertFalse(plan["executed"])

    def test_reap_never_drops_uncommitted_work(self):
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        precious = os.path.join(self._path("a"), "UNCOMMITTED_WORK.txt")
        touch(precious, "precious\n")
        self.engine.skip("t", "a")
        self.assertTrue(worktree.status(SPEC, "t", "a", self.repo)["dirty"])

        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(any(r.startswith("dirty:") for r in entry["reasons"]))
        self.assertTrue(entry["discard_warnings"])
        self.assertTrue(os.path.exists(precious), "reap 不得丢弃未提交工作（它压根不执行）")

    def test_apply_reap_apply_cycle_keeps_branch(self):
        # reap 是纯计划 → 不删分支；apply 仍幂等。
        self._seed(["a"])
        first = worktree.apply(SPEC, "t", self.repo)
        self.assertEqual(first["created"], ["a"])
        self.engine.skip("t", "a")
        plan = worktree.reap(SPEC, "t", self.repo)
        self.assertEqual([e["node"] for e in plan["leaks"]], ["a"])
        self.assertEqual(self._branches("disflow/t/a"), ["disflow/t/a"], "reap 不删分支")
        self.engine.retry("t", "a")
        second = worktree.apply(SPEC, "t", self.repo)
        self.assertEqual(second["created"], [])
        self.assertEqual(second["skipped"], ["a"])

    def test_failed_node_is_reclaimable_in_plan(self):
        # failed 与 done/skipped 同列可回收，不再落在三分类之外永久占盘
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.engine.fail("t", "a", "boom")
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual([leak["node"] for leak in report["leaks"]], ["a"])
        self.assertEqual([leak["status"] for leak in report["leaks"]], ["failed"])
        # failed 不算「活」→ plan 不会再给它发 add
        self.assertEqual(worktree.plan(SPEC, "t", self.repo)["plan"], [])
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertTrue(entry["safe_to_drop"])

    # ── 最强性质：reap 永不改变任何东西 ──────────────────────────────────

    def _snapshot(self) -> dict:
        """把「现实」逐字冻结：state dir 内容 hash / 登记 / 所有 refs 的 sha / worktree 目录内容。"""
        state_dir = os.environ["DISFLOW_STATE_DIR"]
        worktrees = {os.path.realpath(p): self._hash_tree(p)
                     for p in worktree.registered(self.repo) if os.path.isdir(p)}
        return {
            "state_dir": self._hash_tree(state_dir),
            "worktree_list": self._run(self.repo, "worktree", "list", "--porcelain"),
            "refs": self._run(self.repo, "for-each-ref",
                              "--format=%(refname) %(objectname)",
                              "refs/heads", "refs/remotes"),
            "worktrees": worktrees,
        }

    def _hash_tree(self, root: str) -> dict | None:
        if not os.path.exists(root):
            return None
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                with open(full, "rb") as handle:
                    out[os.path.relpath(full, root)] = hashlib.sha256(handle.read()).hexdigest()
        return out

    def _run(self, cwd: str, *args: str) -> str:
        proc = subprocess.run(["git", "-C", cwd, *args],
                              capture_output=True, text=True, check=True)
        return proc.stdout

    # 各危险状态建场（每个用**独立新仓 + 独立状态目录**，互不串味）

    def _reset_state(self) -> None:
        tmp = tempfile.mkdtemp(prefix="disflow-wt-state-")
        os.environ["DISFLOW_STATE_DIR"] = tmp
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

    def _new_seeded_worktree(self) -> None:
        self._reset_state()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])

    def _setup_clean(self) -> None:
        self._new_seeded_worktree()
        self.engine.skip("t", "a")

    def _setup_dirty(self) -> None:
        self._new_seeded_worktree()
        touch(os.path.join(self._path("a"), "wip.txt"), "wip\n")
        self.engine.skip("t", "a")

    def _setup_ignored(self) -> None:
        self._reset_state()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        touch(os.path.join(self.repo, ".gitignore"), "*.log\n")
        subprocess.run(["git", "-C", self.repo, "add", ".gitignore"],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "ignore"], check=True, capture_output=True, text=True)
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        touch(os.path.join(self._path("a"), "debug.log"), "secret\n")
        self.engine.skip("t", "a")

    def _setup_unmerged(self) -> None:
        self._new_seeded_worktree()
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        self.engine.skip("t", "a")

    def _setup_detached(self) -> None:
        self._new_seeded_worktree()
        subprocess.run(["git", "-C", self._path("a"), "checkout", "--detach"],
                       check=True, capture_output=True, text=True)
        self._commit_in(self._path("a"), "det.txt", "x\n")
        self.engine.skip("t", "a")

    def _setup_residual_unmerged(self) -> None:
        self._new_seeded_worktree()
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force",
                        self._path("a")], check=True, capture_output=True, text=True)
        self.engine.skip("t", "a")

    def _plan_entry_for(self, node: str) -> dict:
        plan = worktree.reap(SPEC, "t", self.repo)
        for entry in plan["leaks"] + plan["residual"]:
            if entry["node"] == node:
                return entry
        self.fail(f"计划里没有节点 {node} 的条目")

    def test_reap_never_mutates_any_state(self):
        # 最强性质：无论现场多危险，reap 调用前后「现实」逐字相同。
        cases = {
            "干净": self._setup_clean,
            "脏": self._setup_dirty,
            "ignored": self._setup_ignored,
            "未合并提交": self._setup_unmerged,
            "detached": self._setup_detached,
            "残留分支(未合并)": self._setup_residual_unmerged,
        }
        for kind, setup in cases.items():
            with self.subTest(kind=kind):
                setup()
                before = self._snapshot()
                worktree.reap(SPEC, "t", self.repo)
                self.assertEqual(before, self._snapshot(), f"{kind}: reap 改变了状态")

        with self.subTest(kind="探针坏"):
            self._setup_unmerged()
            with mock.patch.object(worktree, "_commit_on_other_ref", return_value=None):
                before = self._snapshot()
                worktree.reap(SPEC, "t", self.repo)
                self.assertEqual(before, self._snapshot(), "探针坏: reap 改变了状态")

    # ── 结构性守卫：不靠「结果没变」，直接审 reap 发出的 argv ─────────────────

    # 写型 git 子命令（含需要的子动作）。只读的那些（status / rev-parse /
    # for-each-ref / log / diff / cat-file / check-ignore / ls-files /
    # worktree list / config --get）不在此列。
    _MUTATING: frozenset[tuple[str, ...]] = frozenset({
        ("branch", "-d"), ("branch", "-D"), ("branch", "-f"), ("branch", "-m"),
        ("worktree", "add"), ("worktree", "remove"), ("worktree", "prune"),
        ("worktree", "move"), ("worktree", "lock"),
        ("update-ref",), ("reset",), ("commit",), ("checkout",), ("switch",),
        ("clean",), ("push",), ("rm",), ("gc",), ("stash",), ("tag",),
        ("fetch",), ("merge",), ("rebase",),
    })

    @classmethod
    def _mutating_git_calls(cls, argv_list: list[list[str]]) -> list[str]:
        """从记录的 argv 里挑出写型 git 调用（剥掉前置的 `git` 与 `-C <路径>` / `-c k=v`）。"""
        bad: list[str] = []
        for argv in argv_list:
            rest = list(argv)
            if not rest or rest[0] != "git":
                continue
            rest = rest[1:]
            while rest and rest[0] in ("-C", "-c", "--git-dir", "--work-tree"):
                rest = rest[2:] if rest[0] in ("-C", "-c") else rest[1:]
            if tuple(rest[:2]) in cls._MUTATING or tuple(rest[:1]) in cls._MUTATING:
                bad.append(" ".join(argv))
        return bad

    def test_reap_only_issues_readonly_git_calls(self):
        """**结构性**守卫：`reap` 只允许发出只读 git 调用。

        为何另加这一条（而不只留 `test_reap_never_mutates_any_state`）：
        那条是**观测式**断言（看「状态有没有变」），于是它的牙**取决于外部工具是否配合**。
        实测：把 `git branch -D <branch>` 注入 `reap` 后，只有 detached 象限能抓到——
        其余象限里分支正被 worktree 检出，git **自己**拒删，状态没变，观测式断言看不出差别。
        那时「reap 不变更」实际上靠的是 git 的保护，不是我们的判据。
        这里改成直接审 argv：不管 git 是否放行，只要 reap 发出写型子命令就是红。
        两个象限都测（一个 git 会拒、一个 git 会放行），就是为了钉住「与象限无关」。
        """
        for label, setup in (("未合并提交（git 会拒删已检出分支）", self._setup_unmerged),
                             ("detached（git 会放行删除）", self._setup_detached)):
            with self.subTest(kind=label):
                setup()
                seen: list[list[str]] = []
                real_run = subprocess.run

                # 默认参数把 `seen` / `real_run` **绑定成参数**（而不是隐式捕获循环变量）：
                # 后者会在下一次循环重绑变量时静默被换掉，是难查的一类错。
                def recorder(argv, *args, _seen=seen, _real=real_run, **kwargs):
                    _seen.append([str(tok) for tok in argv] if isinstance(argv, (list, tuple))
                                 else [str(argv)])
                    return _real(argv, *args, **kwargs)

                with mock.patch.object(subprocess, "run", side_effect=recorder):
                    worktree.reap(SPEC, "t", self.repo)

                bad = self._mutating_git_calls(seen)
                self.assertEqual([], bad, f"{label}: reap 发出了写型 git 调用：{bad}")

    # ── 每种危险状态必须 safe_to_drop=False 且 discard_warnings 非空 ───────

    def test_reap_dangerous_states_are_unsafe_and_warned(self):
        cases = {
            "dirty": (self._setup_dirty, "dirty:"),
            "ignored": (self._setup_ignored, "ignored:"),
            "unmerged-commit": (self._setup_unmerged, "unmerged-commit:"),
            "detached": (self._setup_detached, "unmerged-commit:"),
            "residual-unmerged": (self._setup_residual_unmerged, "unmerged-commit:"),
        }
        for kind, (setup, prefix) in cases.items():
            with self.subTest(kind=kind):
                setup()
                entry = self._plan_entry_for("a")
                self.assertFalse(entry["safe_to_drop"])
                self.assertTrue(entry.get("discard_warnings"), entry)
                self.assertTrue(any(r.startswith(prefix) for r in entry["reasons"]),
                                entry["reasons"])

    def test_reap_flags_unreadable_probe_as_unsafe(self):
        # FIX3-R2 / fail-closed：for-each-ref 不可判定 → safe_to_drop=False 且留显式 reason
        self._setup_unmerged()
        with mock.patch.object(worktree, "_commit_on_other_ref", return_value=None):
            plan = worktree.reap(SPEC, "t", self.repo)
        entry = plan["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(any(r.startswith("unreadable-refs:") for r in entry["reasons"]))
        self.assertTrue(entry["discard_warnings"])
        self.assertFalse(plan["reliable"])

    # ── 默认拒绝静默丢失：计划必须判不安全 ───────────────────────────────

    def test_no_silent_loss_unmerged_commit_planned_unsafe(self):
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        self.engine.skip("t", "a")
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertIn(f"unmerged-commit:{sha[:12]}", entry["reasons"])
        self.assertTrue(entry["discard_warnings"])
        self.assertTrue(os.path.isdir(self._path("a")))
        self.assertTrue(self._reachable(sha), "那笔提交必须仍可达")
        self.assertTrue(self._branches("disflow/t/a"))

    def test_no_silent_loss_ignored_files_planned_unsafe(self):
        touch(os.path.join(self.repo, ".gitignore"), "*.log\n")
        subprocess.run(["git", "-C", self.repo, "add", ".gitignore"],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "ignore"], check=True, capture_output=True, text=True)
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        touch(os.path.join(self._path("a"), "debug.log"), "secret\n")
        st = worktree.status(SPEC, "t", "a", self.repo)
        self.assertFalse(st["dirty"], "ignored 不该混进 dirty")
        self.assertEqual(st["ignored"], ["debug.log"])
        self.engine.skip("t", "a")
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertIn("ignored:debug.log", entry["reasons"])
        self.assertTrue(entry["discard_warnings"])
        self.assertTrue(os.path.exists(os.path.join(self._path("a"), "debug.log")),
                        "ignored 文件不得被静默销毁")

    def test_no_silent_loss_missing_dir_with_commit_planned_unsafe(self):
        # E2：目录已不在、登记/分支还在且分支有提交 → 计划判不安全
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        self.engine.skip("t", "a")
        shutil.rmtree(self._path("a"))
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(self._reachable(sha))

    def test_no_silent_loss_detached_head_planned_unsafe(self):
        # E3：detached HEAD 上的提交；git 认为工作树干净，无 --force 也照删 → 计划必须判不安全
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        subprocess.run(["git", "-C", self._path("a"), "checkout", "--detach"],
                       check=True, capture_output=True, text=True)
        self._commit_in(self._path("a"), "det.txt", "x\n")
        sha = self._sha(self._path("a"))
        self.assertFalse(worktree.status(SPEC, "t", "a", self.repo)["dirty"])
        self.engine.skip("t", "a")
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(os.path.isdir(self._path("a")))
        self.assertTrue(self._reachable(sha))

    def test_apply_reattaches_residual_branch_with_commit(self):
        # FIX2-B2：worktree 没了、分支还在（残留分支）→ apply re-attach，带回上次那笔提交
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        shutil.rmtree(self._path("a"))                 # 模拟「worktree 被清、分支被拒留」
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["reattached"], ["a"])
        self.assertEqual(self._sha(self._path("a")), sha, "re-attach 必须带回上次那笔提交")
        self.assertTrue(self._reachable(sha))

    def test_apply_after_refused_reap_keeps_commit(self):
        # 建 → commit → reap（纯计划，不动手）→ apply 不得报错、提交仍在
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        self.engine.skip("t", "a")
        worktree.reap(SPEC, "t", self.repo)
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["broken"], [])
        self.assertEqual(self._sha(self._path("a")), sha)

    # ── FIX3：守卫 fail-closed by construction（计划面）──────────────────

    def test_no_silent_loss_same_name_tag_ambiguity(self):
        # FIX3-R1：同名 tag 使 git 给短名加前缀（heads/）→ 旧代码 refs.discard 打空而 fail-open；
        # 改用全名精确排除后计划必须判不安全，且提交仍可达。
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        subprocess.run(["git", "-C", self.repo, "tag", "disflow/t/a", "HEAD"],
                       check=True, capture_output=True, text=True)
        self.engine.skip("t", "a")
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(any(r.startswith("unmerged-commit:") for r in entry["reasons"]))
        self.assertTrue(os.path.isdir(self._path("a")), "同名 tag 下计划不得判安全")
        self.assertTrue(self._reachable(sha), "那笔提交必须仍可达")

    def test_no_silent_loss_remote_named_disflow(self):
        # FIX3-R1 第二触发面：远端名恰为 disflow → refs/remotes/disflow/t/a；全名排除后不得 fail-open
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        main = self._sha(self.repo)
        subprocess.run(["git", "-C", self.repo, "update-ref",
                        "refs/remotes/disflow/t/a", main],
                       check=True, capture_output=True, text=True)
        self.engine.skip("t", "a")
        entry = worktree.reap(SPEC, "t", self.repo)["leaks"][0]
        self.assertFalse(entry["safe_to_drop"])
        self.assertTrue(self._reachable(sha))

    def test_residual_branch_passes_same_guard(self):
        # R2 闭洞：目录+登记都没、分支还在且带未合并提交 → 残留分支**必须过同一套判据**：
        # safe_to_drop=False，discard_warnings 说清会丢哪笔提交；且 reap 什么都不做。
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self._commit_in(self._path("a"), "work.txt", "precious\n")
        sha = self._sha(self._path("a"))
        subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force",
                        self._path("a")], check=True, capture_output=True, text=True)
        self.engine.skip("t", "a")
        report = worktree.reconcile(SPEC, "t", self.repo)
        self.assertEqual(report["leaks"], [])
        self.assertEqual([r["node"] for r in report["residual"]], ["a"])
        plan = worktree.reap(SPEC, "t", self.repo)
        self.assertEqual([r["node"] for r in plan["residual"]], ["a"])
        entry = plan["residual"][0]
        self.assertEqual(entry["branch"], "disflow/t/a")
        self.assertIsNone(entry["remove_cmd"], "残留分支没有 worktree 可删")
        self.assertEqual(entry["branch_cmd"],
                         ["git", "-C", self.repo, "branch", "-d", "disflow/t/a"])
        self.assertFalse(entry["safe_to_drop"])
        self.assertIn(f"unmerged-commit:{sha[:12]}", entry["reasons"])
        self.assertTrue(entry["discard_warnings"])
        self.assertTrue(any(sha[:12] in w for w in entry["discard_warnings"]),
                        entry["discard_warnings"])
        # reap 不执行：提交仍可达、分支仍在
        self.assertTrue(self._reachable(sha))
        self.assertEqual(self._branches("disflow/t/a"), ["disflow/t/a"])
        # 宿主仍可 apply re-attach 拿回那笔提交（先 retry 让节点重新变“活”）
        self.engine.retry("t", "a")
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["reattached"], ["a"])
        self.assertEqual(self._sha(self._path("a")), sha)

    def test_apply_does_not_prune_other_worktrees(self):
        # FIX2-B6(a)：apply 不得全局 prune——别人「暂时不在」的登记必须留住
        self._seed(["a"])
        other_parent = tempfile.mkdtemp(prefix="disflow-other-")
        self.addCleanup(shutil.rmtree, other_parent, ignore_errors=True)
        other = os.path.join(other_parent, "wt")
        subprocess.run(["git", "-C", self.repo, "worktree", "add", "-b", "other/x",
                        other, "HEAD"], check=True, capture_output=True, text=True)
        shutil.rmtree(other)
        before = {os.path.realpath(p) for p in worktree.registered(self.repo)}
        self.assertIn(os.path.realpath(other), before)
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        after = {os.path.realpath(p) for p in worktree.registered(self.repo)}
        self.assertIn(os.path.realpath(other), after, "别人的悬空登记不得被 apply 清掉")

    def test_apply_unknown_tree_has_no_side_effects(self):
        # FIX2-B6(b)：校验失败时零副作用——不得先把本命名空间下的悬空登记清掉
        self._seed(["a"])
        worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        shutil.rmtree(self._path("a"))
        before = {os.path.realpath(p) for p in worktree.registered(self.repo)}
        with self.assertRaises(KeyError):
            worktree.apply(SPEC, "nope", self.repo)
        after = {os.path.realpath(p) for p in worktree.registered(self.repo)}
        self.assertEqual(before, after, "apply 校验失败时不得有任何副作用")

    def test_apply_reports_explicitly_named_but_filtered_nodes(self):
        # FIX2-B9：显式点名却被过滤掉的节点单列 not_applicable，不与「已建好」混淆
        self._seed(["a"])
        self.engine.skip("t", "a")
        result = worktree.apply(SPEC, "t", self.repo, nodes=["a"])
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["not_applicable"], ["a"])

    def test_status_dirty_is_none_when_not_a_real_worktree(self):
        # FIX2-B11：目录在但不是真 worktree → dirty 是「未读」(None)，不是「干净」(False)
        self._seed(["a"])
        os.makedirs(self._path("a"))
        st = worktree.status(SPEC, "t", "a", self.repo)
        self.assertFalse(st["real"])
        self.assertIsNone(st["dirty"])

    # ── 约束 3：仓内拒绝（与 cwd 无关）────────────────────────────────────

    def test_reject_in_repo_is_cwd_independent(self):
        # FIX-B3 + FIX-B8：仓内路径必须被**响亮拒绝**，且两个 cwd 下行为一致
        rel_spec = {"state": dict(SPEC["state"])}
        rel_spec["state"]["env"] = "_DISFLOW_UNSET_FOR_TEST"      # 强制走相对 dir
        self.assertNotIn("_DISFLOW_UNSET_FOR_TEST", os.environ)
        old_cwd = os.getcwd()
        try:
            for cwd in (self.repo, tempfile.gettempdir()):
                os.chdir(cwd)
                self.assertEqual(worktree.path_for(rel_spec, "t", "a"),
                                 os.path.join(".disflow", "worktrees", "t", "a"))
                with self.assertRaises(ValueError):
                    worktree.commands(rel_spec, "t", "a", self.repo)
            # 仓外绝对 state_dir：两个 cwd 下命令必须完全一致
            os.chdir(self.repo)
            here = worktree.commands(SPEC, "t", "a", self.repo)
            os.chdir(tempfile.gettempdir())
            there = worktree.commands(SPEC, "t", "a", self.repo)
            self.assertEqual(here, there)
        finally:
            os.chdir(old_cwd)

    def test_reject_if_inside_raises_value_error(self):
        # FIX-B8：正向上钉住守卫本身（删掉它这个测试就红）
        legacy_spec = {"state": {"dir": ".disflow", "env": "_DISFLOW_UNSET_FOR_TEST"}}
        with self.assertRaises(ValueError):
            worktree.commands(legacy_spec, "t", "a", self.repo)
        with self.assertRaises(ValueError):
            worktree._reject_if_inside(
                os.path.join(self.repo, ".disflow", "worktrees", "t", "a"), self.repo)

    # ── 响亮报错 ──────────────────────────────────────────────────────────

    def test_unknown_tree_raises(self):
        self._seed(["a"])
        with self.assertRaises(KeyError):
            worktree.plan(SPEC, "nope", self.repo)
        with self.assertRaises(KeyError):
            worktree.status(SPEC, "nope", "a", self.repo)

    def test_unknown_node_raises(self):
        self._seed(["a"])
        with self.assertRaises(KeyError):
            worktree.status(SPEC, "t", "ghost", self.repo)
        with self.assertRaises(KeyError):
            worktree.plan(SPEC, "t", self.repo, nodes=["ghost"])

    # ── helpers ───────────────────────────────────────────────────────────

    def _commit_in(self, path: str, name: str, body: str) -> None:
        """在某个 worktree 里提交一个文件（模拟 worker 的已提交工作）。"""
        touch(os.path.join(path, name), body)
        subprocess.run(["git", "-C", path, "add", "-A"],
                       check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", name], check=True, capture_output=True, text=True)

    def _sha(self, path: str) -> str:
        proc = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def _reachable(self, sha: str) -> bool:
        proc = subprocess.run(["git", "-C", self.repo, "cat-file", "-e", f"{sha}^{{commit}}"],
                              capture_output=True, text=True, check=False)
        return proc.returncode == 0

    def _branches(self, pattern: str) -> list[str]:
        proc = subprocess.run(["git", "-C", self.repo, "branch", "--list", pattern],
                              capture_output=True, text=True, check=True)
        # `git branch` 给当前分支加 `*`、给别处 checkout 的分支加 `+` 前缀，都要剥掉
        return [line.lstrip("*+ ").strip() for line in proc.stdout.splitlines()
                if line.strip()]


if __name__ == "__main__":
    unittest.main()
