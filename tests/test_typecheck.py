"""全仓类型检查：把 pyright 当**回归门**跑一遍（本机没装则跳过）。

**为什么要有它**（实测教训，不是洁癖）：`store.batch_append` 的返回行引用了
**未定义的 `offset`** —— 一条**零调用方的死路径**。当时：

| 手段 | 结果 |
| --- | --- |
| 292 个单元测试 | ✅ 全绿（死路径没被调用） |
| 变异测试 39/39 | ✅ 全过（针脚覆盖的是别处） |
| 逐文件 LSP 探针 | ✅ 干净（**只探我改过的文件**，没探 `store.py`） |
| **全仓 pyright** | ❌ **`"offset" is not defined`** |

两条可迁移的结论：

1. **“测试全绿”不覆盖死路径，类型检查覆盖。** 零调用方 + 零测试 = 哑雷。
2. **逐文件探针不够**：只探改过的文件 ⇒ **没改过的文件里的既存 bug 永远看不见**。
   要按“全仓”而不是“本次改动”来跑。

pyright 是**可选**的（本仓保证零第三方运行时依赖）：找不到就 `skipTest`，
不让这个测试变成环境相关的假红。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 已知落点（pi-lens 自带的 pyright），再退回 PATH
_PYRIGHT_CANDIDATES = (
    pathlib.Path.home() / ".pi-lens/tools/node_modules/.bin/pyright",
)


def _find_pyright() -> str | None:
    for candidate in _PYRIGHT_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("pyright")


class WorkspaceTypeCheckTest(unittest.TestCase):
    def test_whole_workspace_typechecks_clean(self):
        pyright = _find_pyright()
        if pyright is None:
            self.skipTest("本机没有 pyright（可选工具）；跳过全仓类型检查")
        proc = subprocess.run([pyright, "--outputjson"], cwd=ROOT,
                              capture_output=True, text=True, timeout=600)
        try:
            report = json.loads(proc.stdout)
        except json.JSONDecodeError:                       # pragma: no cover
            self.fail(f"pyright 输出不是 JSON（exit={proc.returncode}）：\n"
                      f"{proc.stdout[:500]}\n{proc.stderr[:500]}")
        diagnostics = report.get("generalDiagnostics", [])
        errors = [d for d in diagnostics if d.get("severity") == "error"]
        lines = [
            f"  {d.get('file', '?').replace(str(ROOT) + '/', '')}:"
            f"{d.get('range', {}).get('start', {}).get('line', '?')}  {d.get('message', '')}"
            for d in errors
        ]
        self.assertEqual(errors, [], "全仓类型检查有错误：\n" + "\n".join(lines))


class AnalyzerConfigTest(unittest.TestCase):
    """静态检查的**配置**本身也要受检：不许“把检查器按住”。

    **为何要有这条**（实测代价，不是洁癖）：`.pi-lens.json` 从**第一个 commit**
    （`24e88fe`）起就写着 `{"ignore": ["disflow/store.py"]}`，把整个文件致盲。后来
    `store.py` 的 `batch_append` 里藏了一个未定义变量的 `NameError`——292 个测试、
    变异测试 39/39、逐文件探针全都没报，**只有全仓 pyright 扫出来了**
    （它是唯一不读 `.pi-lens.json` 的那条路）。

    正确的抑制粒度只有两种，都会**留下痕迹**：

    | 手段 | 粒度 | 能否致盲一个文件 |
    | --- | --- | --- |
    | `rules.<id>.disable` | 规则（全项目） | ❌ |
    | `# pi-lens-ignore: <rule>` | 站点 | ❌ |
    | `ignore` | **整文件 / 整目录** | ✅ **禁止** |
    """

    def test_no_file_level_ignore(self):
        path = ROOT / ".pi-lens.json"
        if not path.is_file():
            self.skipTest("本仓没有 .pi-lens.json")
        config = json.loads(path.read_text(encoding="utf-8"))
        ignored = config.get("ignore") or []
        self.assertEqual(
            ignored, [],
            f"不许整文件致盲：{ignored}——改用规则级（rules.<id>.disable）"
            "或行内（# pi-lens-ignore: <rule>）抑制，那两种会留下痕迹")

    def test_current_disables_are_rule_scoped_and_documented(self):
        """当前关掉的规则必须**按规则**关，且关掉的理由有出处。

        实测依据（本轮全仓扫描，45 文件 18 条）：
          - `unchecked-numeric-parse-python` 8/8 都是对**已是数值**的值取整
            （`int()` 在 isinstance/isdigit 守卫之后，或对 float 取整）——不是字符串解析；
          - `unchecked-throwing-call-python` 7/7 都是本仓**故意的响亮失败契约**
            （坏事件必须抛；`test_bad_jsonl_line_raises` 就在测它）。
        两种都是“规则的前提在本仓不成立”，而非“我们不想看”。
        """
        path = ROOT / ".pi-lens.json"
        if not path.is_file():
            self.skipTest("本仓没有 .pi-lens.json")
        config = json.loads(path.read_text(encoding="utf-8"))
        for rule in config.get("rules") or {}:
            self.assertNotIn("*", rule, "规则 id 里不该有通配")
        # 关掉的规则必须在开发板上写清理由（否则就是又一次“按住检查器”）
        board = (ROOT / "docs/dev-board.md").read_text(encoding="utf-8")
        for rule in config.get("rules") or {}:
            self.assertIn(rule, board, f"关掉规则 {rule} 必须在 docs/dev-board.md 里写明理由")


if __name__ == "__main__":
    unittest.main()
