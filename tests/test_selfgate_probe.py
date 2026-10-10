"""`scripts/disflow_selfgate_probe.py` 的黑盒回归。

探针本身是「写在被测代码之前的独立判据」（四个情形见其顶部 docstring）。本测试不复制它的
判据，只把它当**命令**跑：在**未改坏**的仓上必须退出 0。实现被改坏时探针变红——三种改坏
（`engine._gate_cwd` 不再跟随 worktree / 关掉 `done` 的 fail-closed 比对 / 去掉
`gates.git_changed_files` 的 `base_sha` 并集）实测都让探针退出 1（负向对照见交付说明）。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PROBE = ROOT / "scripts" / "disflow_selfgate_probe.py"


class SelfGateProbeTest(unittest.TestCase):
    def test_probe_passes_on_this_checkout(self) -> None:
        """本仓未改坏 ⇒ 四情形全过（退出 0）；任何一条变红都带上原始输出。"""
        proc = subprocess.run([sys.executable, str(PROBE), str(ROOT)],
                              cwd=str(ROOT), capture_output=True, text=True, timeout=300)
        self.assertEqual(
            proc.returncode, 0,
            f"探针应退出 0，实际 {proc.returncode}：\n{proc.stdout}\n{proc.stderr}")

    def test_probe_rejects_non_disflow_dir(self) -> None:
        """指到没有 `disflow/` 的目录 ⇒ 探针必须响亮退出 1（不静默当成空跑或通过）。"""
        with tempfile.TemporaryDirectory(prefix="disflow-selfgate-test-") as empty:
            proc = subprocess.run([sys.executable, str(PROBE), empty],
                                  cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("不是 disflow 仓", proc.stderr)


if __name__ == "__main__":
    unittest.main()
