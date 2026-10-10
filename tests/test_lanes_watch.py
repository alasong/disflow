"""看板运行视图测试（2026-10-11 重写）：按**阶段列**（= 看板列）分别列出 **running + pending**。

判据（用户原话）：「我就想看看板运行视图，按照几个看板，分别显示 running 和 pending 的任务」
+「树只是看板中条目的组织关系」——**看板 = 阶段列**；树不是一块板。

1. 列 = 配置的 `stages`（与 `disflow board` 同源）；空列**显式**打 `（空）`（读时容忍 ≠ 静默隐藏）。
2. **默认只列运行中 + 待完成**；`--all` 连 `✓ 完成 / — 跳过` 也列。
3. 在跑任务就地给 lane 事实（`▸ lane <节点id> · 状态 · 已跑 · 静默 · 消息数`）；
   `-v` 才多给「最后命令 / 自报进展」（默认不刷屏）。
4. 行尾 `← 树名` **只在多于一棵树时**出现；列内按 `parent` 缩进（父不在同列则不缩进）。
5. 读不到 `state.json` ⇒ **明说**、不崩；lane 名在投影里找不到对应节点 ⇒ **点名**，不猜。
6. `once` / 非 TTY ⇒ **零控制序列**（可安全重定向）。
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
import unittest.mock
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


def _tree(name: str = "t") -> dict:
    """一棵树：root 容器(req) + dev 两层（父/子）+ dev 的 done + verify 的 pending/skipped。"""
    def task(node_id, parent, stage, goal, status="pending"):
        return {"id": node_id, "parent": parent, "kind": "task", "stage": stage,
                "goal": goal, "status": status}

    return {"goal": f"{name} 的树目标", "root": "root", "closed": False, "nodes": {
        "root": {"id": "root", "parent": None, "kind": "container", "stage": "req",
                 "goal": f"{name} 的树目标"},
        "dev1": task("dev1", "root", "dev", "开发一"),
        "sub": task("sub", "dev1", "dev", "子任务"),
        "gone": task("gone", "root", "dev", "已完成", status="done"),
        "ver1": task("ver1", "root", "verify", "验证一"),
        "skip1": task("skip1", "root", "verify", "跳过", status="skipped"),
    }}


class _FakeTTY(io.StringIO):
    """`isatty()` 为真的假 stdout：让 `main` 走常驻（top 式刷新）分支而不碰真终端。"""

    def isatty(self) -> bool:
        return True


class LanesWatchTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self._orig_pat = lanes_watch.PAT
        self.addCleanup(setattr, lanes_watch, "PAT", self._orig_pat)
        self.runs = tempfile.mkdtemp(prefix="disflow-lanes-runs-")
        self.addCleanup(shutil.rmtree, self.runs, ignore_errors=True)
        lanes_watch.PAT = os.path.join(self.runs, "*")   # 与真 run 目录解耦

    def _write_state(self, trees: dict | None = None) -> None:
        with open(os.path.join(self.tmp, "state.json"), "w", encoding="utf-8") as fh:
            json.dump({"trees": trees if trees is not None else {"t": _tree()}}, fh)

    def _render(self, show_all: bool = False, verbose: bool = False) -> str:
        return "\n".join(lanes_watch.render(show_all, verbose))

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

    def _running_lane(self, child_id: str = "dev1") -> None:
        """一个在跑的派发批次（父 run）+ 一条在跑的 lane（childId = 节点 id）。"""
        self._fake_run("parent", run_id="parent-run-1234", mode="workflow", state="running",
                       children=[{"childId": child_id, "state": "running",
                                  "runId": "child-run"}])
        self._fake_run("child", run_id="child-run", mode="single", state="running",
                       command="grep -n foo tests/x.py", text="⟳ 干活 · 1/3")

    def _once(self, argv) -> str:
        buf = io.StringIO()                 # isatty() → False ⇒ 非 TTY 路径：打一轮就退
        with contextlib.redirect_stdout(buf):
            code = lanes_watch.main(["once", *argv])
        self.assertEqual(code, 0)
        return buf.getvalue()

    # ── 1. 列 = 阶段；空列显式（不静默隐藏）───────────────────────────────

    def test_columns_are_the_stages_and_empty_ones_are_stated(self):
        self._write_state()
        text = self._render()
        self.assertIn("列 req 1 · design 0 · dev 2 · verify 1", text)
        self.assertIn("[design]  0　（空）", text)
        self.assertIn("[dev]  2", text)
        self.assertIn("[req]  1", text)

    def test_pending_items_sit_under_their_own_stage(self):
        self._write_state()
        lines = self._render().splitlines()
        dev_at = next(i for i, x in enumerate(lines) if x.startswith("[dev]"))
        ver_at = next(i for i, x in enumerate(lines) if x.startswith("[verify]"))
        self.assertTrue(any("dev1" in x for x in lines[dev_at:ver_at]), "dev1 在 [dev] 段里")
        self.assertTrue(any("ver1" in x for x in lines[ver_at:]), "ver1 在 [verify] 段里")
        self.assertFalse(any("ver1" in x for x in lines[dev_at:ver_at]), "ver1 不许串到 [dev]")

    # ── 2. 默认只列 running + pending；`--all` 连终态 ─────────────────────

    def test_done_and_skipped_show_only_with_all(self):
        self._write_state()
        plain = self._render()
        for hidden in ("gone", "skip1"):
            self.assertNotIn(hidden, plain, f"默认必须隐藏终态 {hidden}")
        full = self._render(show_all=True)
        for shown in ("gone", "skip1"):
            self.assertIn(shown, full, f"--all 必须列出 {shown}")
        self.assertIn("在跑 lane 0 路", plain)

    def test_no_active_work_is_stated(self):
        fresh = _tree()
        for node in fresh["nodes"].values():
            if node["kind"] == "task":
                node["status"] = "done"
        self._write_state({"t": fresh})
        self.assertIn("（没有运行中 / 待完成的任务", self._render())

    # ── 3. 在跑任务：lane 事实 + `-v` 才有命令/进展 ───────────────────────

    def test_running_task_shows_lane_facts_and_verbose_adds_command(self):
        self._write_state()
        self._running_lane()
        plain = self._render()
        self.assertIn("▶ 运行中", plain)
        lane = next(x for x in plain.splitlines() if "lane dev1" in x)
        self.assertIn("running", lane)
        self.assertIn("已跑", lane)
        self.assertIn("静默", lane)
        self.assertIn("条消息", lane)
        self.assertNotIn("⌘", plain, "默认不给命令（不刷屏）")
        verbose = self._render(verbose=True)
        self.assertIn("⌘ bash: grep -n foo tests/x.py", verbose)
        self.assertIn("↳ ⟳ 干活 · 1/3", verbose)

    def test_lane_without_a_matching_node_shows_its_facts_not_a_fake_node(self):
        """无对应节点的 lane（宿主直接跑的 worker）—— **列出它的运行事实**（用户 2026-10-11
        实测：「在跑 lane 2 路」却只丢一句「找不到对应节点」⇒ 看不到那两路在跑什么 = 视图说不完整），
        但**不挂到**任何条目行上（不猜一个 goal 顶上）。"""
        self._write_state()
        self._running_lane(child_id="ghost-node")
        text = self._render()
        self.assertIn("非 disflow 节点的 lane", text)
        self.assertIn("▸ lane ghost-node", text)
        lane = next(x for x in text.splitlines() if "lane ghost-node" in x)
        self.assertIn("running", lane, "要看到它在跑，而不是只说找不到")
        self.assertIn("已跑", lane)
        self.assertTrue(lane.startswith("  ▸"), "它自己一段（顶格两空格），不在任何条目行下")
        self.assertNotIn("◈", text)

    # ── 4. 行尾树名（只在多树时）+ 列内父子缩进 ───────────────────────────

    def test_tree_tag_only_when_several_trees(self):
        self._write_state({"t": _tree("t"), "u": _tree("u")})
        text = self._render()
        self.assertIn("← t", text)
        self.assertIn("← u", text)
        self.assertNotIn("← t", self._render_one_tree())

    def _render_one_tree(self) -> str:
        self._write_state({"t": _tree("t")})
        return self._render()

    def test_indent_follows_parent_within_the_same_column(self):
        self._write_state()
        lines = self._render().splitlines()
        dev1 = next(x for x in lines if x.strip().startswith("· dev1"))
        sub = next(x for x in lines if x.strip().startswith("· sub"))
        lead = lambda s: len(s) - len(s.lstrip())           # noqa: E731
        self.assertGreater(lead(sub), lead(dev1), "sub 的父在同列 ⇒ 必须缩进一层")

    # ── 5/6. 读不到就明说 + 非 TTY 零控制序列 ─────────────────────────────

    def test_missing_state_json_is_stated_and_does_not_crash(self):
        self._write_state()
        os.remove(os.path.join(self.tmp, "state.json"))
        text = self._render()
        self.assertIn("✗ 读不到", text)
        self.assertIn("state.json", text)

    def test_once_emits_no_control_sequences(self):
        self._write_state()
        out = self._once([])
        for seq in ("\033", "\x1b", "\r"):
            self.assertNotIn(seq, out, "非 TTY / once 不许发控制序列")
        self.assertIn("══ 看板运行视图 @", out)

    def test_all_flag_and_verbose_flag_are_understood_by_main(self):
        self._write_state()
        self.assertIn("gone", self._once(["--all"]))
        self.assertNotIn("gone", self._once([]))

    def test_refresh_mode_clears_the_screen_like_top(self):
        """默认刷新 = **清屏再画**（用户 2026-10-11：「直接 clear 再显示，像 top 那样」）：
        先 `\\033[H\\033[2J` 再画，且只画一份（不追加、不叠屏）。"""
        self._write_state()
        buf = _FakeTTY()
        with unittest.mock.patch.object(lanes_watch.time, "sleep",
                                        side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(buf):
            code = lanes_watch.main([])
        out = buf.getvalue()
        self.assertEqual(code, 0, "Ctrl-C 干净退出（0）")
        self.assertEqual(out.count("\033[H\033[2J"), 1, "一轮 = 清屏一次")
        self.assertLess(out.index("\033[H\033[2J"), out.index("看板运行视图"),
                        "先清屏，再画")
        self.assertIn("\033[J", out, "擦掉上一轮更长的尾巴")


if __name__ == "__main__":
    unittest.main()
