"""`doctor` 的主动检查：「state dir 落在 repo 内部 ⇒ worktree 功能不可用」。

**为什么要有它**：`worktree` 模块硬拒落在 repo 内的 worktree 路径（会污染 `git status`，
且 `git clean -xdf` 会把状态目录连同全部 worktree 一起删掉）。但这条只在真跑 `worktree
apply` 时才炸——用的人得走到那一步才发现「已交付的功能在本配置下不可用」。本检查把它
**提前**到 doctor 报出来。

**归「需人判断」而非「故障」**（exit 0）：仓内 state dir 是**出厂默认**（`spec.py` 的
`state.dir = ".disflow"`），它只限制一项**可选功能**的可用性、其余功能正常。判成故障会因
默认布局把所有人假红——而假红比不报更坏。但也不能静默：单独一条、单独一行报出来。

两侧都要钉，缺一侧就退化成“只是把 worktree 动词的报错抄了一遍”或“误报”：

- **仓内**：exit 0；stdout 有一行「worktree 功能不可用」+ 修法（`[state].dir` /
  `DISFLOW_STATE_DIR` 配到仓外）；且 `realpath` 解析后判——文本在仓外、符号链接指进仓内
  同样要报。
- **仓外**：不得误报——干净状态下该行**不出现**、仍 exit 0、stdout 全绿。

`DISFLOW_STATE_DIR` 由子进程环境显式给定、cwd 用真实仓目录：这条检查吃的正是这两个来源，
用进程内 `chdir` + 环境变量会互相串味。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

from base import DisflowTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent


class DoctorWorktreeTest(DisflowTestCase):
    """两侧都是**真 git 仓** + 真 CLI 子进程（cwd 与 state dir 显式给定）。"""

    def _tmpdir(self, prefix: str) -> str:
        path = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def _repo(self) -> str:
        repo = self._tmpdir("disflow-doctor-repo-")
        subprocess.run(["git", "init", "-q", repo], check=True)
        return repo

    def _run(self, repo: str, state_dir: str, argv: list[str]):
        env = {**os.environ, "PYTHONPATH": str(ROOT),
               "DISFLOW_STATE_DIR": state_dir}
        return subprocess.run([sys.executable, "-m", "disflow.cli", *argv],
                              cwd=repo, env=env, capture_output=True, text=True,
                              timeout=120)

    def _seed(self, repo: str, state_dir: str) -> None:
        seeded = self._run(repo, state_dir, ["init", "t", "--goal", "自检隔离"])
        self.assertEqual(seeded.returncode, 0, seeded.stderr)

    # ── 仓内：需人判断（exit 0）+ 单独一行 + 修法 ──────────────────────

    def test_state_dir_inside_repo_is_an_advisory_not_a_failure(self):
        repo = self._repo()
        state_dir = os.path.join(repo, ".disflow")     # 出厂默认布局：落在 repo 内
        self._seed(repo, state_dir)
        proc = self._run(repo, state_dir, ["doctor"])
        self.assertEqual(proc.returncode, 0, "出厂默认布局不该被判成故障（假红）")
        self.assertIn("✓ doctor 全绿", proc.stdout)
        lines = [ln for ln in proc.stdout.splitlines() if "worktree" in ln]
        self.assertEqual(len(lines), 1, "必须是单独一行，不与别的检查挤在一起")
        line = lines[0]
        self.assertIn("worktree 功能不可用", line)
        self.assertIn("可用性限制", line, "要说清这是功能可用性限制、其余功能正常")
        self.assertIn("仓外", line, "必须给出修法")
        self.assertIn("DISFLOW_STATE_DIR", line)
        self.assertIn("需人判断", proc.stdout)
        self.assertEqual(proc.stderr, "", "非故障不该走 stderr")

    def test_symlinked_state_dir_resolving_inside_is_still_reported(self):
        """文本在仓外、符号链接指进仓内：`realpath` 解析后仍须报出来。"""
        repo = self._repo()
        real = os.path.join(repo, ".disflow")
        os.makedirs(real)                               # 真身先存在（makedirs 不吃悬空符号链接）
        outside = self._tmpdir("disflow-doctor-link-")
        link = os.path.join(outside, "state-link")
        os.symlink(real, link)                          # 文本在仓外，真身在仓内
        self._seed(repo, link)
        proc = self._run(repo, link, ["doctor"])
        self.assertEqual(proc.returncode, 0)
        self.assertIn("worktree 功能不可用", proc.stdout, "符号链接解析后在仓内 ⇒ 仍须报出")

    # ── 仓外：不得误报 ───────────────────────────────────────────────────

    def test_state_dir_outside_repo_is_not_reported(self):
        repo = self._repo()
        state_dir = self._tmpdir("disflow-doctor-outside-")
        self._seed(repo, state_dir)
        proc = self._run(repo, state_dir, ["doctor"])
        self.assertEqual(proc.returncode, 0, f"仓外 state dir 不该报：{proc.stderr}")
        self.assertIn("✓ doctor 全绿", proc.stdout)
        self.assertNotIn("worktree 功能不可用", proc.stdout + proc.stderr)

    def test_symlinked_state_dir_resolving_outside_is_not_reported(self):
        """文本在仓内、符号链接指向仓外：`realpath` 解析后在仓外 ⇒ 不得误报。"""
        repo = self._repo()
        real = self._tmpdir("disflow-doctor-real-")
        link = os.path.join(repo, ".disflow-link")
        os.symlink(real, link)                          # 文本在仓内，真身在仓外
        self._seed(repo, link)
        proc = self._run(repo, link, ["doctor"])
        self.assertEqual(proc.returncode, 0, f"解析后在仓外就不该报：{proc.stderr}")
        self.assertNotIn("worktree 功能不可用", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
