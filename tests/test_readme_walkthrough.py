"""README 的「5 分钟上手」必须**照抄可跑**。

**为什么要有这个**：上手路径的全部价值就是“你能自己跑一遍并看见”。如果文档里的命令
改了名、或者输出与描述不符，那段文档就从资产变成了负债——而且只有读者照抄时才会发现。
所以这里**把 README 里的命令真的执行一遍**，并断言它声明的结果。

机制：README 里用 `# disflow-walkthrough:<名字>` 标记可执行代码块，本测试抽取并执行。
改文档时若命令漂了，这里就红。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

from base import DisflowTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent
_BLOCK = re.compile(r"```bash\n# disflow-walkthrough:(\w+)\n(.*?)```", re.S)


def walkthrough_blocks() -> dict[str, str]:
    """README 里标记的可执行块：`{名字: 脚本}`。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    return {match.group(1): match.group(2) for match in _BLOCK.finditer(text)}


class ReadmeWalkthroughTest(DisflowTestCase):
    def _run_block(self, script: str, cwd: str, env: dict) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", script], cwd=cwd, env=env,
                              capture_output=True, text=True, timeout=300)

    def test_readme_has_the_two_marked_blocks(self):
        self.assertEqual(sorted(walkthrough_blocks()), ["prepare", "run"],
                         "README 的上手块必须存在且只有这两个（见本文件顶部说明）")

    def test_documented_walkthrough_actually_runs(self):
        blocks = walkthrough_blocks()
        demo = tempfile.mkdtemp(prefix="disflow-walkthrough-")
        self.addCleanup(shutil.rmtree, demo, ignore_errors=True)
        # 块本身只管工作目录里的东西；环境（状态目录、可导入路径）由调用方给
        env = {**os.environ, "PYTHONPATH": str(ROOT),
               "DISFLOW_STATE_DIR": os.path.join(demo, ".disflow")}

        prepare = self._run_block(blocks["prepare"], demo, env)
        self.assertEqual(prepare.returncode, 0, f"prepare 失败：\n{prepare.stderr}")

        run = self._run_block(blocks["run"], demo, env)
        self.assertEqual(run.returncode, 0,
                         f"run 块没跑完（照抄会失败）：\n{run.stdout}\n{run.stderr}")
        # 拒收报文**按设计走 stderr**（成功报文走 stdout）——断言要看两边
        combined = run.stdout + run.stderr

        # ── 文档里声明的四件事，逐条断言 ──────────────────────────────────
        self.assertIn("就绪首波 1 个", run.stdout, "② 应算出首波只有 A")
        self.assertIn("B ← A", run.stdout, "② 应显示 B 被 A 阻塞")
        self.assertIn("门拒收", combined, "④ 假的完成必须被拒收")
        self.assertIn("验收未通过", combined, "④ 拒收理由应指向验收")
        self.assertIn("✓ A done", run.stdout, "⑤ 真完成后应落盘")
        self.assertIn("已关闭", combined, "关树应成功（逃生口给了理由）")

        # ── 配置：独立文件，且**在 start 之前**就位（否则会被算成本节点的改动）──
        # 这一行能成立，说明 prepare 真的把 disflow.json 放在首次 start 之前；
        # 若把它挪到 run 里（start 之后），⑤ 的 done 会被作用域门拒收。
        self.assertIn("acceptance_timeout = 300", run.stdout,
                      "⑧ doctor 应报出配置文件里的取值（不是出厂默认 120）")
        self.assertIn("disflow.json", run.stdout, "⑧ 应报出配置文件路径与来源")

        # ── 声明的证据形状（README 展示的那三行）──────────────────────────
        state = json.loads(
            pathlib.Path(env["DISFLOW_STATE_DIR"], "state.json").read_text(encoding="utf-8"))
        tree = state["trees"]["demo"]
        self.assertTrue(tree["closed"])
        node_a = tree["nodes"]["A"]
        self.assertEqual(node_a["status"], "done")
        evidence = node_a["evidence"]
        self.assertEqual(evidence["introduced"], ["greeting.txt"], "只归因本节点真的改了的文件")
        self.assertEqual(evidence["size"]["total"], 1)
        self.assertEqual(evidence["vacuous"], {}, "README 声明的两道门真验到了东西")
        self.assertEqual(evidence["acceptance"]["exit"], 0)
        self.assertEqual(evidence["acceptance"]["timeout"], 300,
                         "验收超时必须真的用配置文件里的值（此前是代码里的常量）")

    def test_readme_walkthrough_is_mentioned_from_the_top(self):
        """上手路径不能埋在文档深处（本次修的就是“新读者要读 555 行才敢动手”）。"""
        lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        heading = next((i for i, line in enumerate(lines) if line.startswith("## 5 分钟上手")), None)
        self.assertIsNotNone(heading, "README 必须有「5 分钟上手」一节")
        assert heading is not None
        self.assertLess(heading, 12, "上手一节必须在最前面（前 12 行内出现）")


if __name__ == "__main__":
    unittest.main()
