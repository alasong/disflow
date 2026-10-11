"""`gates.git_changed_files` 的**非 ASCII 路径**回归测试。

为何单开一个文件：这是一个**静默退化**型缺陷，不修不会报错、只会变慢与误判 ——
- git 默认 `core.quotepath=true`，把 `docs/0010-看板.md` 写成 `"docs/0010-\\347\\234\\213..."`；
- `test_changed.py` 拿这个转义串去映射测试 ⇒ **全部未命中** ⇒ 保守退化成全量
  （实测一次迭代 35s → 87s，且横幅只说「未映射」不说是编码问题）；
- 门的 `touch_plan` / 作用域比对同理：拿转义串跟真路径比**永远不等**，会误判
  （作用域门的两种错都能出：该拦的不拦、不该拦的拦下）。

判据：本仓文档大量中文文件名（`docs/decisions/0008-看板不做web化.md` 等），
**这不是边角情况，是主路径**。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest

from base import DisflowTestCase

from disflow import gates

# 覆盖三类：中文文件名、中文目录、纯 ASCII（对照，证明修复没把正常路径弄坏）
_CN_FILE = "docs/0010-看板不做web化.md"
_CN_DIR_FILE = "文档/说明-中文目录.md"
_ASCII_FILE = "src/plain.py"


def _init_repo(root: str) -> None:
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"], check=True)


def _write(root: str, rel: str, body: str = "x\n") -> None:
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(body)


class NonAsciiPathTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = tempfile.mkdtemp(prefix="disflow-cn-")
        _init_repo(self.repo)

    def _commit_all(self, message: str) -> str:
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-qm", message], check=True)
        return subprocess.run(["git", "-C", self.repo, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_changed_files_are_not_escaped(self):
        """修复的核心判据：返回的是**真路径**，不是 git 的八进制转义串。

        用「先提交、再修改」而不是「新建未跟踪文件」—— 后者 git 会把整个新目录折叠成
        `docs/`（看不到中文文件名，也就测不到编码）。主路径是改**已跟踪**文件。
        """
        for rel in (_CN_FILE, _CN_DIR_FILE, _ASCII_FILE):
            _write(self.repo, rel)
        self._commit_all("base")
        for rel in (_CN_FILE, _CN_DIR_FILE, _ASCII_FILE):
            _write(self.repo, rel, "changed\n")
        got = gates.git_changed_files(self.repo)
        self.assertIsNotNone(got)
        assert got is not None
        for rel in (_CN_FILE, _CN_DIR_FILE, _ASCII_FILE):
            self.assertIn(rel, got, f"{rel} 应以真路径出现（实际：{got}）")
        # 反向断言：不许出现转义形态（`"` + 八进制）
        self.assertFalse([p for p in got if p.startswith('"') or "\\3" in p],
                         f"出现转义路径：{got}")

    def test_committed_files_are_not_escaped(self):
        """已提交那条路（`git diff --name-only <sha>`）同判据 —— 提交型 worker 走这条。"""
        _write(self.repo, _ASCII_FILE)
        base = self._commit_all("base")
        _write(self.repo, _CN_FILE, "changed\n")
        self._commit_all("cn")
        got = gates.git_changed_files(self.repo, base_sha=base)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertIn(_CN_FILE, got, f"提交路径应为真形式（实际：{got}）")

    def test_scope_check_matches_chinese_touch_plan(self):
        """作用域门：中文 `touch_plan` 必须能与改动集**对上**（修复前恒不等）。"""
        _write(self.repo, _CN_FILE)
        self._commit_all("base")
        _write(self.repo, _CN_FILE, "changed\n")
        changed = gates.git_changed_files(self.repo)
        assert changed is not None
        result = gates.scope_check([_CN_FILE], changed)
        self.assertEqual(result["violations"], [],
                         f"中文 touch_plan 应被认作命中（实际：{result}）")


if __name__ == "__main__":
    unittest.main()
