"""CLI 层补齐（GAP3）：三个新动词 `metrics` / `deadline` / `worktree` 的端到端测试。

**为什么会有这个文件**：`disflow/cli.py` 注册了 `cmd_metrics` / `cmd_deadline` /
`cmd_worktree`，但此前 `tests/` 里**没有任何测试调用过它们**
（`grep -rn 'cmd_metrics\\|cmd_deadline\\|cmd_worktree' tests/` 为空）。本文件照
`tests/test_cli.py` / `tests/test_cli_readonly.py` 的既有模式，用
`from disflow.cli import main` 直接驱动，断言退出码与报文（成功走 stdout、失败走 stderr）。

三条动词各自的硬契约（每条都有对应的负向对照把实现改坏 → 本文件必须变红）：
- **metrics**：未声明 `points` 时打印「未声明」而**绝不编一个 0**；未估节点不进任何分母；
  `--json` 同时给 `summary` 与 `burndown` 两个键。
- **deadline**：非法日期**在写时响亮拒收**（退出码非 0 且**不落事件**、不覆盖旧值）；
  `-` 表示清空。
- **worktree**：`plan`/`list`/`reap` 零副作用；`apply` 真建且幂等；`reap` **只打印不执行**
  （删是唯一不可逆动作，引擎不替人删数据）；`branch_cmd` 一律 `-d`（安全删），绝不出 `-D`。

`DISFLOW_STATE_DIR` 由 `tests/base.py` 的 `DisflowTestCase.setUp` 放到**仓外**临时目录——
`worktree` 有一道守卫会拒绝「worktree 路径落在 repo 内部」，state dir 在仓内会直接报错。
"""
from __future__ import annotations

import importlib
import io
import json
import os
import re
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone

from base import DisflowTestCase, acceptance, git_repo

from disflow import vocab, store
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


def _refs(repo: str) -> str:
    """全部 refs 名 + sha 的逐行快照（排序），用来证明「逐字不变」。"""
    proc = subprocess.run(["git", "-C", repo, "for-each-ref",
                           "--format=%(refname) %(objectname)"],
                          capture_output=True, text=True, check=True)
    return "\n".join(sorted(proc.stdout.splitlines()))


class _CliRunMixin:
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()


class CliMetricsDeadlineTest(_CliRunMixin, DisflowTestCase):
    """`metrics` / `deadline`：只读与声明，不依赖 git。"""

    def _seed(self, children: list[dict]) -> None:
        self._run(["init", "t", "--goal", "gap3"])
        self._run(["expand", "t", "root", "--children",
                   json.dumps(children, ensure_ascii=False)])

    def _deadline_in_projection(self) -> str | None:
        state = store.read_json(store.projection_path(vocab.STORAGE))
        return state["trees"]["t"].get("deadline")

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(vocab.STORAGE)))

    # ── metrics ──────────────────────────────────────────────────────────

    def test_metrics_without_points_says_undeclared(self):
        """未声明点数时打印「未声明」，**不是**编一个 0。"""
        self._seed([
            {"id": "a", "goal": "A", "acceptance": acceptance()},
            {"id": "b", "goal": "B", "acceptance": acceptance()},
        ])
        code, out, err = self._run(["metrics", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("未声明", out, "没给 points 就该说「未声明」")
        self.assertNotIn("点数    0", out, "未声明不许被读成一个 0")

    def test_metrics_reports_points_done_left_unestimated(self):
        """有 points 时：点数 / 已完成 / 剩 / 未估 四个数都对；未估不进分母。"""
        self._seed([
            {"id": "a", "goal": "A", "points": 3, "acceptance": acceptance()},
            {"id": "b", "goal": "B", "points": 5, "acceptance": acceptance()},
            {"id": "c", "goal": "C", "acceptance": acceptance()},   # 没估 → 不进分母
        ])
        self._run(["skip", "t", "a", "--reason", "演示已完成"])
        code, out, err = self._run(["metrics", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("点数    8", out)          # 3 + 5（c 不算分母）
        self.assertIn("已完成 3", out)
        self.assertIn("剩 5", out)
        self.assertIn("未估 1 个节点", out)

    def test_metrics_json_has_summary_and_burndown(self):
        self._seed([{"id": "a", "goal": "A", "points": 2, "acceptance": acceptance()}])
        code, out, err = self._run(["metrics", "t", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(sorted(payload), ["burndown", "summary"])
        self.assertIn("points_total", payload["summary"])
        self.assertIn("series", payload["burndown"])

    # ── deadline ─────────────────────────────────────────────────────────

    def test_deadline_sets_and_echoes_remaining_days(self):
        self._seed([{"id": "a", "goal": "A", "acceptance": acceptance()}])
        code, out, err = self._run(["deadline", "t", "2026-12-31"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self._deadline_in_projection(), "2026-12-31")
        self.assertIn("已设为 2026-12-31", out)
        # 回显剩余天数（与 metrics 同口径，按 UTC 日历日算）
        expected = (date(2026, 12, 31) - datetime.now(timezone.utc).date()).days
        self.assertIn(f"剩 {expected} 天", out)

    def test_deadline_rejects_invalid_date_without_writing_event(self):
        """非法日期：退出码非 0、**不落事件**、旧值不被覆盖。"""
        self._seed([{"id": "a", "goal": "A", "acceptance": acceptance()}])
        self._run(["deadline", "t", "2026-12-31"])           # 先落一个旧值
        before = self._events()
        code, _out, err = self._run(["deadline", "t", "2026-13-01"])
        self.assertNotEqual(code, 0)
        self.assertIn("✗", err)
        self.assertEqual(self._events(), before, "非法日期不许落事件")
        self.assertEqual(self._deadline_in_projection(), "2026-12-31", "旧值不许被覆盖")

    def test_deadline_dash_clears(self):
        self._seed([{"id": "a", "goal": "A", "acceptance": acceptance()}])
        self._run(["deadline", "t", "2026-12-31"])
        code, out, err = self._run(["deadline", "t", "-"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self._deadline_in_projection())
        self.assertIn("已清空", out)


class CliWorktreeTest(_CliRunMixin, DisflowTestCase):
    """`worktree` 四个动作的 CLI 端到端：真 git 仓 + 仓外 state dir。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine, self.repo = git_repo(self, {"README.md": "hello\n"}, commit=True)
        old_cwd = os.getcwd()
        self.addCleanup(os.chdir, old_cwd)
        os.chdir(self.repo)                     # main() 用 os.getcwd() 建 Engine

    def _seed(self, ids: list[str]) -> None:
        self._run(["init", "t", "--goal", "gap3"])
        self._run(["expand", "t", "root", "--children",
                   json.dumps([{"id": nid, "goal": nid, "acceptance": acceptance()}
                               for nid in ids], ensure_ascii=False)])

    def _node_path(self, node: str) -> str:
        return worktree.path_for(STORAGE, "t", node)

    # ── plan：零副作用 ───────────────────────────────────────────────────

    def test_worktree_plan_is_side_effect_free(self):
        self._seed(["a"])
        before = _worktree_paths(self.repo)
        code, out, err = self._run(["worktree", "plan", "t"])
        self.assertEqual(code, 0, err)
        self.assertEqual(_worktree_paths(self.repo), before, "plan 不许改 worktree 登记")
        self.assertFalse(os.path.exists(worktree.root(STORAGE)), "plan 不许创建 worktree 根")
        # 打印出的落点必须在 state dir（= 仓外），否则会被 `git clean -xdf` 连带删掉
        path = os.path.realpath(self._node_path("a"))
        repo = os.path.realpath(self.repo)
        self.assertFalse(path == repo or path.startswith(repo + os.sep),
                         f"worktree 落点落在 repo 内：{path}")
        self.assertIn(self._node_path("a"), out)

    # ── apply：真建 + 幂等 ───────────────────────────────────────────────

    def test_worktree_apply_creates_worktree_and_is_idempotent(self):
        self._seed(["a"])
        base_len = len(_worktree_paths(self.repo))
        code, out, err = self._run(["worktree", "apply", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("建了 1 个", out)
        after_first = _worktree_paths(self.repo)
        self.assertEqual(len(after_first), base_len + 1, "apply 应真建出一个 worktree")
        self.assertTrue(os.path.isdir(self._node_path("a")))
        # 幂等：再跑一次不重复建
        code2, out2, err2 = self._run(["worktree", "apply", "t"])
        self.assertEqual(code2, 0, err2)
        self.assertEqual(_worktree_paths(self.repo), after_first, "第二次 apply 不许重复建")
        self.assertIn("建了 0 个", out2)

    # ── list：干净 vs leak ───────────────────────────────────────────────

    def test_worktree_list_reports_clean_then_leak(self):
        self._seed(["a"])
        self._run(["start", "t", "a", "--holder", "me"])     # 在跑 + 建工作区 → 干净
        self._run(["worktree", "apply", "t"])
        code, out, err = self._run(["worktree", "list", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("✓ 干净", out)
        # 节点已 skip 但工作区还在 → leak
        self._run(["skip", "t", "a", "--reason", "收工"])
        code2, out2, err2 = self._run(["worktree", "list", "t"])
        self.assertEqual(code2, 0, err2)
        self.assertIn("leak", out2)
        self.assertIn("a:", out2)

    # ── reap：只打印不执行 ───────────────────────────────────────────────

    def _seed_reap_scenario(self) -> None:
        """造一个 leak：节点有工作区、其分支上有一条**别处不可达**的提交，然后 skip。"""
        self._seed(["a", "b"])                     # b 留作活节点
        self._run(["worktree", "apply", "t"])
        path = self._node_path("a")
        subprocess.run(["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "--allow-empty", "-qm", "wip"], check=True)
        self._run(["skip", "t", "a", "--reason", "收工"])

    def test_worktree_reap_never_executes(self):
        self._seed_reap_scenario()
        refs_before = _refs(self.repo)
        trees_before = _worktree_paths(self.repo)
        code, out, err = self._run(["worktree", "reap", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("只推不跑", out)
        self.assertEqual(_refs(self.repo), refs_before, "reap 不许改任何 ref 的 sha")
        self.assertEqual(_worktree_paths(self.repo), trees_before, "reap 不许删 worktree")
        self.assertTrue(os.path.isdir(self._node_path("a")), "reap 不许删工作区目录")

    def test_worktree_reap_warns_unmerged_and_uses_safe_branch_delete(self):
        self._seed_reap_scenario()
        code, out, err = self._run(["worktree", "reap", "t"])
        self.assertEqual(code, 0, err)
        self.assertIn("unmerged-commit:", out)
        self.assertIn("将不可达、永久丢失", out)
        self.assertIn("branch -d", out)             # 安全删
        self.assertNotIn("branch -D", out)          # 绝不出强制删
        # branch_cmd 本身也核一遍（防打印层偷偷改文案）
        self.assertTrue(re.search(r"branch -d\b", out))


if __name__ == "__main__":
    unittest.main()
