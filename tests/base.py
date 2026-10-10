"""disflow 测试基类：每个测试用独立状态目录（`DISFLOW_STATE_DIR`），隔离不串味。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from typing import TYPE_CHECKING

if TYPE_CHECKING:                      # 仅为类型标注，运行期不导入（避循环）
    from disflow.engine import Engine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class DisflowTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="disflow-test-")
        os.environ["DISFLOW_STATE_DIR"] = self.tmp
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.addCleanup(os.environ.pop, "DISFLOW_STATE_DIR", None)


def acceptance(text: str = "ok") -> dict:
    """可移植的验收命令：打印文本并 contains 匹配。"""
    return {"cmd": f'{sys.executable} -c "print(\'{text}\')"', "expect": f"contains:{text}"}


def always_fails() -> dict:
    return {"cmd": f'{sys.executable} -c "import sys; sys.exit(3)"', "expect": "exit0"}


def git_repo(test: DisflowTestCase, files: dict[str, str],
             commit: bool = False) -> tuple[Engine, str]:
    """建一个隔离的**真 git 仓**并写入初始文件，返回 `(Engine, 仓路径)`。

    为何要真仓：作用域门 / 改动归因靠 `git status` + 内容指纹，非 git 仓会跳过。
    状态目录（`DISFLOW_STATE_DIR`）已在父类 setUp 里放到仓外，不会自己变成脏文件。

    `commit=False`（默认）：初始文件保持**未跟踪**（开工时就算脏，进基线）。
    `commit=True`：先提交，它们变成**干净已跟踪文件**（不进基线）。两种都要测：
    前者代表“开着脏树干活”，后者代表“从干净点开干”。
    """
    import subprocess

    from disflow.engine import Engine

    repo = tempfile.mkdtemp(prefix="disflow-git-")
    test.addCleanup(shutil.rmtree, repo, ignore_errors=True)
    subprocess.run(["git", "init", "-q", repo], check=True)
    for name, body in files.items():
        full = os.path.join(repo, name)
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(body)
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "init"], cwd=repo, check=True)
    return Engine(cwd=repo), repo


def touch(path: str, body: str) -> None:
    """在仓里写一个文件（模拟 worker 的改动）。"""
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)
