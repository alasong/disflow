"""disflow 测试基类：每个测试用独立状态目录（`DISFLOW_STATE_DIR`），隔离不串味。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

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
