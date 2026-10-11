"""worktree 类性质 + 端到端对抗（lane b）：原 `scripts/independent_checks.py` lane b 的对抗判据。

**来源**：本文件是已删除脚本 `scripts/independent_checks.py`（HEAD 70969fc^ 的 lane b）
的对抗判据搬进 `unittest` 的落地——让它们进入每次 `unittest discover` 的常跑面，而不是
临时 harness 跑完即丢。每条原始 `@case` → 一个测试方法（名带原 id），docstring 保留原始
中文判据原文，断言**同一个可观测量**（不是「能跑过」）。

**为什么每条用例自建临时真仓、state dir 仓外**：`worktree` 按设计**响亮拒绝**落在 repo 内
的路径（`worktree._reject_if_inside`）。状态目录已在 `DisflowTestCase.setUp` 放到仓外，故
`worktree.apply` 不会因「仓内路径」变红。每用例独立临时真 git 仓，退出清理；不依赖进程
cwd 之外的现场。

**为什么用真 git 仓**：类性质与对抗都靠真 `git status` / `git worktree list` / refs 判定，
假仓判据会变空。

**漂移修正（adv8）**：原脚本 adv8 断言 `"未提交的已跟踪改动"` 这个**旧文案**；当前实现契约
（`worktree._discard_warnings`）该类别的文案是「未提交的改动（已修改或未跟踪的文件）会丢失」。
判据的**可观测量**是「危险条目的 discard_warnings 逐条覆盖 dirty / ignored / unmerged-commit
三类会丢的东西」，故按当前文案断言三类覆盖，而不是为迁就旧文案去改实现。
"""
from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import subprocess
import tempfile

from base import DisflowTestCase, acceptance

from disflow.engine import Engine

# 用 importlib 而非 `from disflow import worktree`：`disflow/__init__.py` 的子模块再导出
# 列表未登记 worktree，运行期按文件解析绕开登记（对齐 tests/test_worktree.py 的既有写法）。
worktree = importlib.import_module("disflow.worktree")


def _git(repo: str, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", cwd or repo, *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} 失败（repo={repo}）：{proc.stderr.strip()}")
    return proc


def _is_exactly_false(value: object) -> bool:
    """严格判「恰好是布尔 ``False``」：键缺失（``None``）、``0``、空串都不算。

    本条判据的要点是**严格性**——``not value`` 会把「``executed`` 键整个不见了」也读成
    通过，那正是要抓的回归。先用 ``isinstance`` 钉住「确实是布尔」再取反。
    """
    return isinstance(value, bool) and not value


def _commit_in(wt: str, name: str = "work.txt", body: str = "important") -> str:
    with open(os.path.join(wt, name), "w") as fh:
        fh.write(body)
    _git(wt, "add", "-A", cwd=wt)
    _git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "node work", cwd=wt)
    return _git(wt, "rev-parse", "HEAD", cwd=wt).stdout.strip()


def _reachable(repo: str, sha: str) -> bool:
    """sha 从 heads/remotes 任一 ref 可达（数据是否还在 git 层）。"""
    return (_git(repo, "cat-file", "-e", sha, check=False).returncode == 0
            and bool(_git(repo, "for-each-ref", "--contains", sha, "refs/heads", "refs/remotes",
                          check=False).stdout.strip()))


# 危险状态 → 是否「默认必须拒绝」。residual-* = 目录/登记已不在、只剩分支的残留路径。
_DANGER = {"clean": False, "dirty": True, "ignored": True, "unmerged": True,
           "detached": True, "residual-unmerged": True, "residual-clean": False}


class AdversarialWorktreeTest(DisflowTestCase):
    """lane b：worktree 类性质 + 端到端对抗。每用例一个独立临时真 git 仓（仓外 state dir）。"""

    def setUp(self) -> None:
        super().setUp()
        # 临时真 git 仓，退出清理；state dir 已由 DisflowTestCase 放到仓外。
        self.repo = tempfile.mkdtemp(prefix="disflow-adv-repo-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        subprocess.run(["git", "init", "-q", "-b", "main", self.repo], check=True)
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("hi\n")
        # `.gitignore` 常驻：ignored 现场（*.log / .env）靠它，且它随初始提交进 HEAD，
        # 使 worktree checkout 后 ignored 判定现场与原始 `_fresh_repo(ignore=...)` 等价。
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as fh:
            fh.write("*.log\n.env\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")

    # ── 现场搭建（照搬原文 `_seed_node` / `_adv_case` / `_finish`）─────────────

    def _seed_node(self, state: str, node: str = "a") -> tuple[dict, str]:
        """建一棵 t 树，把节点 a 造成 `state` 描述的现场。返回 (storage, repo)。"""
        eng = Engine(cwd=self.repo)
        eng.init_tree("t", "g")
        eng.expand("t", "root", [{"id": node, "acceptance": acceptance()}])
        storage = eng.storage
        eng.start("t", node)
        worktree.apply(storage, "t", self.repo)
        wt = worktree.path_for(storage, "t", node)
        if state == "dirty":
            with open(os.path.join(wt, "README.md"), "a", encoding="utf-8") as fh:
                fh.write("改一行\n")
        elif state == "ignored":
            with open(os.path.join(wt, "debug.log"), "w", encoding="utf-8") as fh:
                fh.write("x\n")
        elif state == "unmerged":
            _commit_in(wt, "f.txt", "c")
        elif state == "detached":
            _git(wt, "checkout", "-q", "--detach", cwd=wt)
            _commit_in(wt, "f.txt", "c")
        elif state == "residual-unmerged":
            _commit_in(wt, "f.txt", "c")
            subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force", wt], check=True)
        elif state == "residual-clean":
            subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force", wt], check=True)
        eng.skip("t", node, "x")
        return storage, self.repo

    def _adv_case(self, node: str = "a") -> tuple[Engine, dict, str]:
        """独立临时真 git 仓 + 播一棵 t 树，节点 a 可跑。返回 (eng, storage, repo)。"""
        eng = Engine(cwd=self.repo)
        eng.init_tree("t", "g")
        eng.expand("t", "root", [{"id": node, "acceptance": acceptance()}])
        return eng, eng.storage, self.repo

    def _finish(self, eng: Engine, node: str = "a") -> None:
        eng.start("t", node)
        eng.skip("t", node, "done for test")

    @staticmethod
    def _leak_of(res: dict, node: str) -> dict | None:
        return next((e for e in res["leaks"] if e["node"] == node), None)

    @classmethod
    def _entry_of(cls, res: dict, node: str) -> dict | None:
        """leaks 与 residual 走同一漏斗，按节点取到那条计划（哪个分类由现场决定）。"""
        return cls._leak_of(res, node) or next((e for e in res["residual"] if e["node"] == node), None)

    def _require_leak(self, res: dict, node: str) -> dict:
        """取 leak 条目，缺失即 fail（供类型收窄，也让失败信息更直白）。"""
        entry = self._leak_of(res, node)
        if entry is None:
            self.fail(f"无 leak 条目（node={node}）")
        return entry

    def _require_entry(self, res: dict, node: str) -> dict:
        """取 leak 或 residual 条目，缺失即 fail。"""
        entry = self._entry_of(res, node)
        if entry is None:
            self.fail(f"无条目（node={node}）")
        return entry

    def _fingerprint(self, storage: dict) -> dict:
        """现实指纹：state 目录文件 + repo 工作区文件 + 全部 refs + worktree 登记 + status。"""
        from disflow import store
        fp: dict[str, str] = {}
        base = store.state_dir(storage)
        for root, _dirs, files in os.walk(base):
            for f in files:
                p = os.path.join(root, f)
                try:
                    with open(p, "rb") as fh:
                        fp["state:" + os.path.relpath(p, base)] = hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    fp["state:" + os.path.relpath(p, base)] = "<unreadable>"
        for root, _dirs, files in os.walk(self.repo):
            if ".git" in root.split(os.sep):
                continue
            for f in files:
                p = os.path.join(root, f)
                try:
                    with open(p, "rb") as fh:
                        fp["repo:" + os.path.relpath(p, self.repo)] = hashlib.sha256(fh.read()).hexdigest()
                except OSError:
                    fp["repo:" + os.path.relpath(p, self.repo)] = "<unreadable>"
        fp["refs"] = _git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
        fp["worktrees"] = _git(self.repo, "worktree", "list", "--porcelain").stdout
        fp["status"] = _git(self.repo, "status", "--porcelain", "-uall").stdout
        return fp

    def _other_worktree(self, branch: str) -> str:
        """在仓外另加一个别人的 worktree（用于「不 prune 别人登记」），返回其路径。"""
        parent = tempfile.mkdtemp(prefix="disflow-adv-other-")
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        other = os.path.join(parent, "other-wt")
        subprocess.run(["git", "worktree", "add", "-q", "-b", branch, other],
                       cwd=self.repo, check=True)
        return other

    # ── ① / ② 类性质（原文 `_b1` / `_b2`）────────────────────────────────────

    def _assert_b1(self, state: str) -> None:
        """① reap 在任何状态下都逐字不变更（含 refs / 登记 / 文件 / 状态目录）。"""
        storage, repo = self._seed_node(state)
        before = self._fingerprint(storage)
        res = worktree.reap(storage, "t", repo)
        after = self._fingerprint(storage)
        self.assertEqual(before, after, f"{state}：reap 变更了现场")
        self.assertTrue(_is_exactly_false(res.get("executed")),
                        f"{state}：executed={res.get('executed')!r}（须恰为 False）")

    def _assert_b2(self, state: str, dangerous: bool) -> None:
        """② 每种危险状态都必须 fail-closed（危险→全 False+警告；安全→全 True+无警告）。"""
        storage, repo = self._seed_node(state)
        res = worktree.reap(storage, "t", repo)
        entries = res["leaks"] + res["residual"]
        self.assertTrue(entries, f"{state}：无条目，无法判定")
        warnings = [w for e in entries for w in (e.get("discard_warnings") or [])]
        if dangerous:
            self.assertTrue(all(not e["safe_to_drop"] for e in entries),
                            f"{state}：safe={[e['safe_to_drop'] for e in entries]} "
                            f"reasons={[e['reasons'] for e in entries]}（须全 False）")
            self.assertTrue(warnings, f"{state}：危险条目缺 discard_warnings")
        else:
            self.assertTrue(all(e["safe_to_drop"] for e in entries),
                            f"{state}：safe={[e['safe_to_drop'] for e in entries]} "
                            f"reasons={[e['reasons'] for e in entries]}（须全 True）")
            self.assertFalse(warnings, f"{state}：安全条目不应有警告：{warnings}")

    def test_b1_clean_reap_never_changes_anything(self):
        """① [clean] reap 在任何状态下都逐字不变更（含 refs / 登记 / 文件 / 状态目录）"""
        self._assert_b1("clean")

    def test_b1_dirty_reap_never_changes_anything(self):
        """① [dirty] reap 在任何状态下都逐字不变更"""
        self._assert_b1("dirty")

    def test_b1_ignored_reap_never_changes_anything(self):
        """① [ignored] reap 在任何状态下都逐字不变更"""
        self._assert_b1("ignored")

    def test_b1_unmerged_reap_never_changes_anything(self):
        """① [unmerged] reap 在任何状态下都逐字不变更"""
        self._assert_b1("unmerged")

    def test_b1_detached_reap_never_changes_anything(self):
        """① [detached] reap 在任何状态下都逐字不变更"""
        self._assert_b1("detached")

    def test_b1_residual_unmerged_reap_never_changes_anything(self):
        """① [residual-unmerged] reap 在任何状态下都逐字不变更"""
        self._assert_b1("residual-unmerged")

    def test_b1_residual_clean_reap_never_changes_anything(self):
        """① [residual-clean] reap 在任何状态下都逐字不变更"""
        self._assert_b1("residual-clean")

    def test_b2_clean_fails_closed(self):
        """② [clean] 每种危险状态都必须 fail-closed（clean 安全：safe_to_drop=True、无警告）"""
        self._assert_b2("clean", _DANGER["clean"])

    def test_b2_dirty_fails_closed(self):
        """② [dirty] 每种危险状态都必须 fail-closed"""
        self._assert_b2("dirty", _DANGER["dirty"])

    def test_b2_ignored_fails_closed(self):
        """② [ignored] 每种危险状态都必须 fail-closed"""
        self._assert_b2("ignored", _DANGER["ignored"])

    def test_b2_unmerged_fails_closed(self):
        """② [unmerged] 每种危险状态都必须 fail-closed"""
        self._assert_b2("unmerged", _DANGER["unmerged"])

    def test_b2_detached_fails_closed(self):
        """② [detached] 每种危险状态都必须 fail-closed"""
        self._assert_b2("detached", _DANGER["detached"])

    def test_b2_residual_unmerged_fails_closed(self):
        """② [residual-unmerged] 每种危险状态都必须 fail-closed"""
        self._assert_b2("residual-unmerged", _DANGER["residual-unmerged"])

    def test_b2_residual_clean_fails_closed(self):
        """② [residual-clean] 每种危险状态都必须 fail-closed（clean 安全：safe_to_drop=True）"""
        self._assert_b2("residual-clean", _DANGER["residual-clean"])

    def test_b3_residual_unmerged_branch_uses_same_funnel_and_is_rejected(self):
        """③ 残留未合并分支走同一漏斗且被拒"""
        storage, repo = self._seed_node("residual-unmerged")
        res = worktree.reap(storage, "t", repo)
        residual = res["residual"]
        self.assertTrue(residual, "无 residual 条目")
        self.assertIn("unmerged-commit", " ".join(residual[0]["reasons"]),
                      f"reasons={residual[0]['reasons']}")
        self.assertFalse(residual[0]["safe_to_drop"],
                         f"safe_to_drop={residual[0]['safe_to_drop']}")

    def test_b3b_residual_branch_cmd_is_safe_delete_d_and_remove_cmd_none(self):
        """③b 残留分支的 branch_cmd 用安全删 `-d`（非 -D）且 remove_cmd 为 None"""
        storage, repo = self._seed_node("residual-unmerged")
        res = worktree.reap(storage, "t", repo)
        residual = res["residual"]
        self.assertTrue(residual, "无 residual 条目")
        sha = _git(repo, "rev-parse", "disflow/t/a").stdout.strip()[:12]
        entry = residual[0]
        self.assertIsNone(entry["remove_cmd"], f"remove_cmd={entry['remove_cmd']}")
        self.assertIn("-d", entry["branch_cmd"], f"branch_cmd={entry['branch_cmd']}")
        self.assertNotIn("-D", entry["branch_cmd"], f"branch_cmd={entry['branch_cmd']}")
        self.assertIn(sha, " ".join(entry["reasons"]), f"reasons={entry['reasons']} sha={sha}")

    def test_b4_broken_probe_raises_worktree_probe_error(self):
        """④ 探针坏 → 抛 WorktreeProbeError（不返回假 all-clear）"""
        storage, _repo = self._seed_node("clean")
        notgit = tempfile.mkdtemp(prefix="disflow-adv-notgit-")
        self.addCleanup(shutil.rmtree, notgit, ignore_errors=True)
        with self.assertRaises(worktree.WorktreeProbeError):
            worktree.reap(storage, "t", notgit)

    def test_b5_apply_executes_and_reattach_restores_old_head(self):
        """⑤ apply 仍执行 + re-attach 后 HEAD 回到旧提交"""
        eng = Engine(cwd=self.repo)
        eng.init_tree("t", "g")
        eng.expand("t", "root", [{"id": "a", "acceptance": acceptance()}])
        storage = eng.storage
        eng.start("t", "a")                          # 保持 running（活节点），不是终态
        worktree.apply(storage, "t", self.repo)
        wt0 = worktree.path_for(storage, "t", "a")
        _commit_in(wt0, "f.txt", "c")
        subprocess.run(["git", "-C", self.repo, "worktree", "remove", "--force", wt0], check=True)
        sha = _git(self.repo, "rev-parse", "disflow/t/a").stdout.strip()
        res = worktree.apply(storage, "t", self.repo)
        wt = worktree.path_for(storage, "t", "a")
        head_back = _git(wt, "rev-parse", "HEAD").stdout.strip() if os.path.isdir(wt) else ""
        self.assertEqual(res["reattached"], ["a"],
                         f"reattached={res['reattached']} created={res['created']}")
        self.assertEqual(head_back, sha, f"HEAD={head_back} 旧提交={sha}")

    # ── 端到端对抗（原文 adv1–adv8）──────────────────────────────────────────

    def test_adv1_unmerged_commit_plan_rejects_and_commit_still_reachable(self):
        """adv1 未合并提交：计划拒绝 + 提交仍可达"""
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        sha = _commit_in(wt)
        self._finish(eng)
        res = worktree.reap(storage, "t", repo)
        entry = self._require_leak(res, "a")
        self.assertFalse(entry["safe_to_drop"], f"reasons={entry['reasons']}")
        self.assertTrue(any(r.startswith("unmerged-commit") for r in entry["reasons"]),
                        f"reasons={entry['reasons']}")
        self.assertTrue(entry.get("discard_warnings"),
                        f"discard_warnings={entry.get('discard_warnings')}")
        self.assertTrue(_is_exactly_false(res["executed"]), f"executed={res['executed']!r}")
        self.assertTrue(_reachable(repo, sha), f"提交 {sha} 不可达")

    def test_adv2_ignored_file_plan_rejects_and_file_survives(self):
        """adv2 ignored 文件：计划拒绝 + 文件还在"""
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        with open(os.path.join(wt, "debug.log"), "w", encoding="utf-8") as fh:
            fh.write("secret")
        self._finish(eng)
        res = worktree.reap(storage, "t", repo)
        entry = self._require_leak(res, "a")
        survived = os.path.exists(os.path.join(wt, "debug.log"))
        self.assertFalse(entry["safe_to_drop"], f"reasons={entry['reasons']}")
        self.assertTrue(any(r.startswith("ignored:") for r in entry["reasons"]),
                        f"reasons={entry['reasons']}")
        self.assertTrue(survived, "ignored 文件被删了")

    def test_adv3_dir_gone_branch_has_commit_plan_rejects_and_commit_reachable(self):
        """adv3 目录已丢+分支有提交：计划拒绝 + 提交仍可达"""
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        sha = _commit_in(wt)
        shutil.rmtree(wt)                            # 模拟目录被手工删掉（登记仍在 → 落 leaks 漏斗）
        self._finish(eng)
        res = worktree.reap(storage, "t", repo)
        entry = self._require_entry(res, "a")
        self.assertFalse(entry["safe_to_drop"], f"reasons={entry['reasons']}")
        self.assertTrue(any(r.startswith("unmerged-commit") for r in entry["reasons"]),
                        f"reasons={entry['reasons']}")
        self.assertTrue(_reachable(repo, sha), f"提交 {sha} 不可达")

    def test_adv4_detached_commit_plan_rejects_and_workspace_not_destroyed(self):
        """adv4 detached 提交：计划拒绝 + 工作区未被销毁"""
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        _git(wt, "checkout", "-q", "--detach", cwd=wt)
        sha = _commit_in(wt, "detached.txt")
        readable = _git(wt, "cat-file", "-e", sha, cwd=wt, check=False).returncode == 0
        self._finish(eng)
        res = worktree.reap(storage, "t", repo)
        entry = self._require_leak(res, "a")
        self.assertFalse(entry["safe_to_drop"], f"safe_to_drop={entry['safe_to_drop']}")
        self.assertTrue(os.path.isdir(wt), "worktree 目录被销毁")
        self.assertTrue(readable, "detached 提交不可读")

    def test_adv5_apply_does_not_prune_others_registration(self):
        """adv5 apply 不 prune 别人的登记"""
        _eng, storage, repo = self._adv_case()
        other = self._other_worktree("other/x")
        shutil.rmtree(other)                         # 别人的 worktree「暂时不在」（卷卸载）
        before = _git(repo, "worktree", "list", "--porcelain").stdout
        worktree.apply(storage, "t", repo)
        after = _git(repo, "worktree", "list", "--porcelain").stdout
        self.assertIn("other-wt", before, f"现场没建上：{before}")
        self.assertIn("other-wt", after, f"apply prune 了别人的登记：{after}")

    def test_adv6_apply_missing_tree_raises_and_zero_side_effects(self):
        """adv6 apply(不存在的树) 抛错且零副作用"""
        _eng, storage, repo = self._adv_case()
        other = self._other_worktree("other/y")
        shutil.rmtree(other)
        before = _git(repo, "worktree", "list", "--porcelain").stdout
        with self.assertRaises(KeyError):
            worktree.apply(storage, "NOPE", repo)
        after = _git(repo, "worktree", "list", "--porcelain").stdout
        self.assertEqual(before, after, "apply(不存在的树) 产生了副作用")

    def test_adv7_residual_branch_reattach_no_deadlock_and_old_commit_restored(self):
        """adv7 残留分支 re-attach：不卡死且拿回上次提交"""
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        sha = _commit_in(wt)
        self._finish(eng)
        worktree.reap(storage, "t", repo)               # 纯计划：分支被拒留 → 残留分支
        branch_left = bool(_git(repo, "branch", "--list", "disflow/t/a").stdout.strip())
        eng.retry("t", "a")
        r2 = worktree.apply(storage, "t", repo)
        head_ok = (_git(wt, "rev-parse", "HEAD", cwd=wt, check=False).stdout.strip() == sha
                   if os.path.isdir(wt) else False)
        self.assertTrue(branch_left, "分支没留下")
        self.assertFalse(r2.get("broken"), f"broken={r2.get('broken')}")
        self.assertTrue(head_ok,
                        f"reattached={r2.get('reattached')} HEAD 未回到旧提交 {sha}")

    def test_adv8_dangerous_entry_discard_warnings_spell_out_losses(self):
        """adv8 危险条目的 discard_warnings 逐条说清会丢什么

        修正漂移：原脚本断言旧文案 `"未提交的已跟踪改动"`；当前实现契约
        （`worktree._discard_warnings`）该类文案为「未提交的改动（已修改或未跟踪的文件）会丢失」。
        可观测量不变——危险条目的 discard_warnings 必须逐条覆盖 dirty / ignored /
        unmerged-commit 三类会丢的东西（强删前宿主读的就是它）。
        """
        eng, storage, repo = self._adv_case()
        worktree.apply(storage, "t", repo)
        wt = worktree.path_for(storage, "t", "a")
        with open(os.path.join(wt, "debug.log"), "w", encoding="utf-8") as fh:
            fh.write("secret")
        _commit_in(wt, "tracked.txt")
        _commit_in(wt, "more.txt", "c2")             # 让分支 tip 是未合并提交
        with open(os.path.join(wt, "README.md"), "a", encoding="utf-8") as fh:
            fh.write("uncommitted\n")                # 提交后再改脏已跟踪文件
        self._finish(eng)
        res = worktree.reap(storage, "t", repo)
        entry = self._require_leak(res, "a")
        self.assertFalse(entry["safe_to_drop"], f"reasons={entry['reasons']}")
        warnings_list = entry.get("discard_warnings") or []
        warnings = " ".join(warnings_list)
        self.assertGreaterEqual(len(warnings_list), 3,
                                f"discard_warnings 未逐条覆盖三类：{warnings_list}")
        self.assertIn("未提交的改动", warnings, f"缺 dirty 类：{warnings_list}")      # dirty
        self.assertIn("被忽略的文件", warnings, f"缺 ignored 类：{warnings_list}")      # ignored
        self.assertIn("debug.log", warnings, f"未点名 ignored 文件：{warnings_list}")
        self.assertIn("永久丢失", warnings, f"缺 unmerged-commit 类：{warnings_list}")   # unmerged
        unmerged = next(r for r in entry["reasons"] if r.startswith("unmerged-commit"))
        sha = unmerged.split(":", 1)[1]
        self.assertIn(sha, warnings, f"未点名未合并提交 {sha}：{warnings_list}")
