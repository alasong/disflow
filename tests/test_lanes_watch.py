"""运行视图测试（b6）：**disflow 树作骨架**、**看板布局同源**、**实时进展只在在跑节点上**。

判据（2026-10-11 用户要求改版，见 brief b6-run-view-live）：

1. **骨架 = 全部节点**（含 pending/skipped/failed）：临时 state dir 摆一棵 2 层 + 多阶段的树，
   运行视图必须打出**四个阶段列（含空列）**与**全部节点**——pending 不许消失（这正是 v6 的
   病：外层循环是 pi 的 run 目录 ⇒ 一个 pending 都看不见）。
2. **布局同源**：列头 / 列内缩进 / 状态分组逐行复用 `disflow.views.board_lines`（本文件断言
   运行视图的输出里出现看板**一模一样**的列头与缩进）——运行视图没有第二套分列/缩进。
3. **就地标注 lane**：join 键 = lane 名（父 run 的 `childId`）== 节点 id；**没有 lane 的节点照样
   出现**（只多一行 `▸` 事实）；lane 名在投影里找不到时**明说**，不猜。
4. **实时进展只对在跑节点**（`◈` 目标 / `⌘` 最后命令 / `↳` worker 自报进展）：pending/done 不显示
   （避免刷屏）。
5. **读不到就明说**：`state.json` 缺失时**不崩**，且显式写明读不到；无 pi 运行目录时也能跑。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from typing import Any

from base import DisflowTestCase

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANES_WATCH = ROOT / ".agents" / "skills" / "disflow" / "lanes_watch.py"


def _load_lanes_watch() -> Any:
    spec = importlib.util.spec_from_file_location("lanes_watch", LANES_WATCH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lanes_watch: Any = _load_lanes_watch()


def _tree() -> dict:
    """2 层树 + 多阶段节点：容器 root(req) / dev 三层一父一子 / verify 的 done / design 为空。"""
    def task(node_id, parent, stage, goal, **extra):
        node = {"id": node_id, "parent": parent, "kind": "task", "stage": stage,
                "goal": goal, "status": "pending"}
        node.update(extra)
        return node

    return {"goal": "运行视图骨架", "root": "root", "closed": False, "nodes": {
        "root": {"id": "root", "parent": None, "kind": "container", "stage": "req",
                 "goal": "运行视图骨架"},
        "dev1": task("dev1", "root", "dev", "开发一"),
        "sub": task("sub", "dev1", "dev", "子任务"),
        "wait": task("wait", "root", "dev", "等前置", requires=["ver1"]),
        "skip1": task("skip1", "root", "dev", "跳过", status="skipped"),
        "ver1": task("ver1", "root", "verify", "验证一"),
        "done1": task("done1", "root", "verify", "已完成", status="done"),
        "fail1": task("fail1", "root", "verify", "失败", status="failed"),
    }}


class LanesWatchTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._orig_pat = lanes_watch.PAT
        self.addCleanup(setattr, lanes_watch, "PAT", self._orig_pat)
        # 默认把 pi 运行目录指到**空目录**：与本仓真 run 目录解耦（测试确定、不串味）
        self.runs = tempfile.mkdtemp(prefix="disflow-lanes-runs-")
        self.addCleanup(shutil.rmtree, self.runs, ignore_errors=True)
        lanes_watch.PAT = os.path.join(self.runs, "*")

    def _write_state(self, trees: dict | None = None) -> None:
        with open(os.path.join(self.tmp, "state.json"), "w", encoding="utf-8") as fh:
            json.dump({"trees": trees if trees is not None else {"t": _tree()}}, fh)

    def _render(self, all_=True) -> list[str]:
        return lanes_watch.render(all_)

    def _fake_run(self, name, *, run_id, mode, state, children=(), command="", text=""):
        """造一个 pi run 目录（本视图只读 `status.json` / `recovery-descriptor.json` / session）。"""
        run_dir = os.path.join(self.runs, name)
        os.makedirs(run_dir, exist_ok=True)
        status = {"runId": run_id, "state": state, "mode": mode, "pid": 999999,
                  "startedAt": 1_000_000, "lastActivityAt": 2_000_000}
        if children:
            status["workflowChildren"] = {"children": list(children)}
        with open(os.path.join(run_dir, "status.json"), "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        if command or text:
            session = os.path.join(run_dir, "session.jsonl")
            with open(session, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"message": {"role": "assistant", "content": [
                    {"type": "toolCall", "name": "bash",
                     "arguments": {"command": command or "echo hi"}},
                    {"type": "text", "text": text or "⟳ 进展"}]}}) + "\n")
            with open(os.path.join(run_dir, "recovery-descriptor.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"sessionFile": session}, fh)
        return run_dir

    def _running_lane(self, child_id="dev1"):
        """一个在跑的批次（父 run）+ 一条在跑的 lane（childId = 节点 id）。"""
        self._fake_run("parent", run_id="parent-run-1234", mode="workflow", state="running",
                       children=[{"childId": child_id, "state": "running",
                                  "runId": "child-run"}])
        self._fake_run("child", run_id="child-run", mode="single", state="running",
                       command="grep -n foo tests/x.py", text="⟳ 干活 · 1/3\n下一步 写测试")

    # ── 骨架 = 全部节点 + 四列（含空列）──────────────────────────────────

    def test_skeleton_is_the_tree_and_lists_pending_skipped_failed(self):
        self._write_state()
        text = "\n".join(self._render())
        for node_id in ("dev1", "sub", "wait", "ver1", "skip1", "done1", "fail1"):
            self.assertIn(node_id, text, f"{node_id} 必须出现（含 pending/skipped/failed）")
        self.assertIn("· ver1", text, "pending 节点必须就地出现（不是「只列已派发 lane」）")
        self.assertIn("· wait", text, "被前置挡住的 pending 节点也要出现")
        self.assertIn("· 可派", text, "可派组：drive 判就绪的 pending 节点（dev1/sub/ver1）")
        self.assertIn("· 待前置", text, "待前置组：wait 缺 ver1")

    def test_all_stage_columns_appear_even_when_the_column_is_empty(self):
        """四列按配置顺序全出（含空列）——列头与 `disflow board` **逐字符相同**。"""
        self._write_state()
        headers = [line for line in self._render() if line.startswith("[")]
        self.assertEqual(headers, ["[req] 0/0", "[design] 0/0", "[dev] 1/4", "[verify] 1/3"])

    def test_in_column_indent_reflects_parent_child(self):
        """列内缩进反映父子：`dev1` 在列顶层，`sub`（parent=dev1，同列）缩进一层。"""
        self._write_state()
        lines = self._render()
        dev1 = next(line for line in lines if line.lstrip().startswith("· dev1"))
        sub = next(line for line in lines if line.lstrip().startswith("· sub"))
        self.assertEqual(len(dev1) - len(dev1.lstrip()), 2, "列顶层 = 2 空格（看板同款）")
        self.assertEqual(len(sub) - len(sub.lstrip()), 4, "子任务缩进一层")

    # ── 就地标注 lane + 实时进展只在在跑节点 ─────────────────────────────

    def test_lane_facts_are_annotated_on_the_row_and_progress_only_when_running(self):
        self._running_lane()
        self._write_state()
        lines = self._render()
        row = next(line for line in lines if line.lstrip().startswith("· dev1"))
        i = lines.index(row)
        annotation = lines[i + 1]
        self.assertIn("▸ lane dev1", annotation, "就地标注哪条 lane")
        self.assertIn("批次 parent-r", annotation, "就地标注哪个批次")
        self.assertIn("已跑", annotation)
        self.assertIn("静默", annotation)
        self.assertIn("条消息", annotation)
        # 在跑节点：◈ 目标 · ⌘ 最后命令 · ↳ 自报进展（沿用现运行视图那几行）
        self.assertIn("◈", lines[i + 2])
        self.assertIn("开发一", lines[i + 2])
        self.assertIn("⌘", lines[i + 3])
        self.assertIn("grep -n foo tests/x.py", lines[i + 3])
        self.assertIn("↳", lines[i + 4])
        self.assertIn("干活", lines[i + 4])

    def test_live_progress_is_not_shown_for_non_running_nodes(self):
        """`◈/⌘/↳` 只属于**在跑**节点：全屏恰好一份（pending/done 不许被刷屏）。"""
        self._running_lane()
        self._write_state()
        lines = self._render()
        self.assertEqual(sum("◈" in line for line in lines), 1, "只有一个在跑节点")
        self.assertEqual(sum("▸" in line for line in lines), 1, "只有 dev1 有 lane")
        ver = next(line for line in lines if line.lstrip().startswith("· ver1"))
        self.assertNotIn("◈", lines[lines.index(ver) + 1], "没 lane 的节点不许显示进展")

    def test_nodes_without_a_lane_do_not_fake_progress(self):
        self._write_state()                       # 无 pi run（PAT 指空目录）
        text = "\n".join(self._render())
        for marker in ("◈", "⌘", "↳", "▸"):
            self.assertNotIn(marker, text, f"无 lane 时不许冒充实时进展（出现 {marker}）")

    def test_a_lane_with_no_matching_node_is_named_not_guessed(self):
        """lane 名在投影里找不到 ⇒ **明说**（次级注解区点名），绝不猜一个 goal 顶上。"""
        self._running_lane(child_id="ghost-node")
        self._write_state()
        text = "\n".join(self._render())
        self.assertIn("ghost-node", text)
        self.assertIn("没有对应节点", text)
        self.assertNotIn("▸", text, "无对应节点的 lane 不该贴到任何节点行上")

    # ── 读不到就明说 / 无 pi 目录也能跑 ──────────────────────────────────

    def test_render_is_read_only_byte_for_byte(self):
        self._running_lane()
        self._write_state()
        path = os.path.join(self.tmp, "state.json")
        with open(path, "rb") as fh:
            before = fh.read()
        self._render()
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before, "运行视图是纯读：state.json 逐字节不变")

    def test_missing_state_json_reports_instead_of_crashing(self):
        lines = self._render()                    # 不写 state.json：不许抛
        text = "\n".join(lines)
        self.assertIn("state.json", text)
        self.assertIn("读不到", text)

    def test_cli_once_runs_with_no_state_and_no_runs(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = lanes_watch.main(["once"])
        self.assertEqual(code, 0)
        self.assertIn("运行视图", buf.getvalue())
        self.assertIn("读不到", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
