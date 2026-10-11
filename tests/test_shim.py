"""`scripts/disflow-shim.sh`：在**任意项目**里跑 disflow 的启动器。

为何要有测试（实测需求）：跨项目用法的最小形态是「PYTHONPATH 指向 clone 的 disflow +
DISFLOW_STATE_DIR 配到**仓外**」——两条都要设，忘了第二条 worktree 就不可用。
启动器把这两条自动推导，但**推导逻辑本身会烂**（路径算错 / 状态目录落回仓内 /
子目录调用不复用）——那正是它要消除的故障，所以必须有牙。

三个硬判据（各对应一条实测踩过的坑）：
  ① **可用**：在无关的 git 项目里跑得通（doctor 全绿）。
  ② **状态目录在仓外**：落在仓内会被 git 追踪 ⇒ 污染改动集、worktree 不可用。
  ③ **按 git 仓根分**：同项目**子目录**调用必须复用同一状态目录（否则每次换目录
     就像换了个项目，树全丢）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from base import DisflowTestCase

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHIM = os.path.join(ROOT, "scripts", "disflow-shim.sh")


def _git(repo: str, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=repo, check=True, capture_output=True)


class ShimTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        # 临时项目 + 临时 DISFLOW_HOME：两边都不碰用户的真实目录。
        self.proj = tempfile.mkdtemp(prefix="disflow-shim-proj-")
        self.addCleanup(shutil.rmtree, self.proj, ignore_errors=True)
        self.home = tempfile.mkdtemp(prefix="disflow-shim-home-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        _git(self.proj, "init", "-q", ".")
        with open(os.path.join(self.proj, "main.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 1\n")
        _git(self.proj, "add", "-A")
        _git(self.proj, "commit", "-qm", "init")

    def _shim(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
        env["DISFLOW_HOME"] = self.home
        return subprocess.run([SHIM, *args], cwd=cwd or self.proj, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_runs_in_an_unrelated_project(self):
        """① 可用：在无关项目里 doctor 跑得通。"""
        proc = self._shim("doctor")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("doctor 全绿", proc.stdout + proc.stderr)

    def test_state_dir_lands_outside_the_repo(self):
        """② 状态目录必须在**仓外**——否则被 git 追踪 ⇒ worktree 不可用。"""
        proc = self._shim("init", "demo", "--goal", "试一下")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        entries = os.listdir(self.home)
        self.assertEqual(len(entries), 1, f"应恰好一个状态目录，实测 {entries}")
        state = os.path.join(self.home, entries[0])
        self.assertFalse(state.startswith(os.path.realpath(self.proj)),
                         f"状态目录落进了仓内：{state}")
        # 仓内不得出现状态目录（那会让它被 git 追踪）
        self.assertNotIn(".disflow", os.listdir(self.proj))

    def test_subdirectory_reuses_the_same_state_dir(self):
        """③ 子目录调用复用同一状态目录（按 git 仓根分，不按 cwd 分）。"""
        self._shim("init", "demo", "--goal", "试一下")
        sub = os.path.join(self.proj, "a", "b")
        os.makedirs(sub, exist_ok=True)
        proc = self._shim("trees", cwd=sub)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("demo", proc.stdout, "子目录里应看到同一棵树的树列表")
        self.assertEqual(len(os.listdir(self.home)), 1, "不得为子目录另建状态目录")

    def test_works_when_invoked_through_a_symlink(self):
        """经过**软链**调用时也要能找到 disflow 包。

        为何是硬判据（实测踩到，后果很重）：文档教的装法就是软链——
        `ln -s <仓>/scripts/disflow-shim.sh ~/bin/disflow`。若脚本直接
        `dirname "$0"`，拿到的是 **~/bin**（链所在目录），再取上一级得 `$HOME/disflow`
        ——一个不存在的位置 ⇒ **「装了启动器却报找不到包」**。
        必须先解链再推导仓根，POSIX 下没有 `readlink -f`，所以有回落逻辑。
        """
        link = os.path.join(self.home, "disflow-link")
        os.symlink(SHIM, link)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["DISFLOW_HOME"] = self.home
        proc = subprocess.run([link, "doctor"], cwd=self.proj, env=env,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("doctor 全绿", proc.stdout + proc.stderr)
        self.assertNotIn("找不到 disflow 包", proc.stderr + proc.stdout,
                         "软链场景下不得报找不到包")

    def test_missing_source_is_reported_with_a_fix(self):
        """源码路径不存在时**响亮报错 + 给修法**（不静默、不猜别处）。"""
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["DISFLOW_HOME"] = self.home
        env["DISFLOW_SRC"] = os.path.join(self.proj, "nope")
        proc = subprocess.run([SHIM, "doctor"], cwd=self.proj, env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 2, "环境不满足 ⇒ 退出码 2")
        self.assertIn("DISFLOW_SRC", proc.stderr, "报错要给出修法")


if __name__ == "__main__":
    unittest.main()
