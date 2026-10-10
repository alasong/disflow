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
6. **可用性四修（b7，2026-10-11 用户当场提的四条）**：
   ① 默认刷新模式 = **追加式**（每轮一整块、带时间戳头、保留回滚），**不出现** `\033[?1049` /
      `\033[2J`；备用屏只能**显式** `--screen`（并写明代价）；**非 TTY 零控制序列**；
   ② `✓ 完成` / `— 跳过` 行默认**不打 goal**（只给 `标记 + id + 状态`）；
   ③ `--active`（别名 `--open`）只留运行中 + 待完成；**默认不变**；
   ④ 无在跑节点时**显式打一句**（进展只对在跑节点显示是设计，不是没了）。
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


class _FakeTTY(io.StringIO):
    """`isatty()` 为真的假 stdout：让 `main` 走常驻（追加 / 备用屏）分支而不碰真终端。"""

    def isatty(self) -> bool:
        return True


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

    def _render(self, all_=True, only_active=True) -> list[str]:
        return lanes_watch.render(all_, only_active=only_active)

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

    def _one_round(self, argv):
        """让常驻循环只跑**一轮**：把 `time.sleep` 换成抛 `KeyboardInterrupt` 的侧效。

        `--screen` 路径的 KeyboardInterrupt 会穿过 `finally` 向外抛（那是它恢复备用屏的
        时机），本助手吞掉它并把 `code` 置 None；追加式路径自己捕获后返回 0。
        """
        buf = _FakeTTY()
        code: int | None = None
        with unittest.mock.patch.object(lanes_watch.time, "sleep",
                                        side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(buf):
            try:
                code = lanes_watch.main(list(argv))
            except KeyboardInterrupt:
                code = None
        return code, buf.getvalue()

    def _cli(self, argv) -> str:
        buf = io.StringIO()                 # isatty() → False ⇒ 非 TTY 路径
        with contextlib.redirect_stdout(buf):
            lanes_watch.main(list(argv))
        return buf.getvalue()

    # ── 骨架 = 全部节点 + 四列（含空列）──────────────────────────────────

    def test_skeleton_is_the_tree_and_lists_pending_skipped_failed(self):
        """`--full`（或 `only_active=False`）时，骨架 = 全部节点（含 pending/skipped/failed）。"""
        self._write_state()
        text = "\n".join(self._render(only_active=False))
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
        self.assertNotIn("批次", annotation,
                         "默认不显示派发批次（用户 2026-10-11）——批次详情只在 `-v` 的次级注解区")
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
        lines = self._render(only_active=False)   # 本用例测「进展贴在哪一行」，不是过滤
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
        text = self._cli(["-v", "once"])      # 「无对应节点」这句在次级批次注解区（`-v`）
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

    # ── 第 1 条：默认追加式（保留回滚）· 备用屏显式 · 非 TTY 零控制序列 ──────

    def test_default_refresh_mode_appends_and_never_uses_the_alternate_screen(self):
        """判据①：默认路径**不出现** `\033[?1049` 与 `\033[2J`；追加模式零控制序列。"""
        self._write_state()
        code, out = self._one_round(["1"])
        self.assertNotIn("\033[?1049", out, "默认刷新不许吃回滚（备用屏无 scrollback）")
        self.assertNotIn("\033[2J", out)
        self.assertNotIn("\033", out, "追加模式一个控制字符都不发")
        self.assertEqual(code, 0, "追加式：Ctrl-C 后干净退出")
        self.assertTrue(any(line.startswith("══ 运行视图 @ ") for line in out.splitlines()),
                        "每块自带时间戳头")

    def test_screen_mode_is_explicit_and_names_its_cost(self):
        """备用屏保留但**显式**：`--screen` 才切备用屏，并在页脚写明代价（不闪、无回滚）。"""
        self._write_state()
        _, out = self._one_round(["--screen", "1"])
        self.assertIn("\033[?1049h", out, "--screen 才用备用屏")
        self.assertIn("无回滚", out, "备用屏的代价要写在屏幕上")
        self.assertIn("\033[?1049l", out, "退出时还原备用屏")

    def test_non_tty_output_contains_no_control_sequences(self):
        """判据②：非 TTY（管道 / 重定向）**零控制序列** —— 常驻模式也必须（之前会吐）。"""
        self._write_state()
        for argv in (["1"], ["once"], ["all"]):
            with self.subTest(argv=argv):
                out = self._cli(argv)
                self.assertNotIn("\033", out, f"{argv} 在非 TTY 下不许吐控制字符")
                self.assertIn("══ 运行视图 @ ", out)

    def test_each_appended_block_is_readable_on_its_own(self):
        """判据③：追加模式下每块自带时间戳，且**不依赖上一块**即可独立阅读。"""
        self._write_state()
        first = lanes_watch.render(False)
        second = lanes_watch.render(False)
        for block in (first, second):
            self.assertRegex(block[0], r"^══ 运行视图 @ \d\d:\d\d:\d\d ══$")
        self.assertEqual(first, second, "同一状态下每块内容独立可复现（不依赖上一块）")
        self.assertIn("· dev1", "\n".join(second), "块内含完整骨架，不用回头看上一块")

    # ── 第 2 条：终态行不打 goal ──────────────────────────────────────

    def test_done_and_skipped_rows_do_not_print_the_goal(self):
        """`✓ 完成` / `— 跳过` 行只给 `标记 + id + 状态`，不打整段 goal（屏幕才扫得动）。"""
        long_goal = "把这件事做完 —— " + "细节" * 120
        tree = _tree()
        tree["nodes"]["done1"]["goal"] = long_goal
        tree["nodes"]["skip1"]["goal"] = long_goal
        self._write_state({"t": tree})
        lines = self._render(only_active=False)   # 终态行只在 --full 下出现
        done = next(line for line in lines if line.lstrip().startswith("✓ done1"))
        skip = next(line for line in lines if line.lstrip().startswith("— skip1"))
        for line in (done, skip):
            self.assertNotIn(long_goal, line)
            self.assertNotIn("细节", line, "终态行一个 goal 片段都不得出现")
        self.assertFalse(any(long_goal in line for line in lines),
                         "整段 goal 不出现在运行视图的任何一行")
        pending = next(line for line in lines if line.lstrip().startswith("· dev1"))
        self.assertLessEqual(len(pending), 2 + 2 + len("dev1") + 2 + 80,
                             "非终态行的 goal 也截断到 80 字")

    # ── 第 3 条：`--active` 只留运行中 + 待完成 ─────────────────────────

    def test_active_flag_hides_done_skipped_and_failed(self):
        """`--active`／`--open` 只留「运行中 + 待完成」——**现在这就是默认**（用户 2026-10-11）。"""
        self._write_state()
        for flag in ("--active", "--open"):
            with self.subTest(flag=flag):
                active = self._cli([flag, "once"])
                for hidden in ("done1", "skip1", "fail1"):
                    self.assertNotIn(hidden, active, f"{flag} 必须隐藏终态节点 {hidden}")
                for kept in ("dev1", "ver1", "wait"):
                    self.assertIn(kept, active, f"{flag} 必须保留 {kept}")
                for label in ("✓ 完成", "— 跳过", "✗ 失败"):
                    self.assertNotIn(label, active)
        default = self._cli(["once"])
        for hidden in ("done1", "skip1", "fail1"):
            self.assertNotIn(hidden, default,
                             "默认（= --active 行为）必须隐藏终态节点；要全看用 --full")
        full = self._cli(["--full", "once"])
        for shown in ("done1", "skip1", "fail1"):
            self.assertIn(shown, full, "--full 必须恢复显示全部状态")

    # ── 第 4 条：无在跑节点时明说 ──────────────────────────────────────────

    def test_no_running_node_is_stated_explicitly(self):
        """无在跑节点时屏幕上**不能一片空白**：显式打一句（进展只对在跑节点显示是设计）。"""
        self._write_state()                 # 无 pi run ⇒ 无在跑节点
        text = "\n".join(self._render())
        self.assertIn("当前无在跑节点", text)
        self.assertIn("只对在跑节点显示", text)
        self._running_lane()                # 有在跑节点时这句不许出现（否则是噪声）
        self.assertNotIn("当前无在跑节点", "\n".join(self._render()))

    def test_default_hides_terminal_nodes_and_full_restores_them(self):
        """默认只列「运行中 + 待完成」（用户 2026-10-11：已完成的不必看）；`--full` 恢复全部状态。"""
        self._write_state()
        default = "\n".join(self._render())
        for hidden in ("done1", "skip1", "fail1"):
            self.assertNotIn(hidden, default, f"默认必须隐藏终态节点 {hidden}")
        full = "\n".join(self._render(only_active=False))
        for shown in ("done1", "skip1", "fail1"):
            self.assertIn(shown, full, f"--full 必须显示 {shown}")

    def test_default_has_no_batch_section_but_verbose_does(self):
        """次级「派发批次」区默认不显示（用户 2026-10-11）；`-v` 才展开。"""
        self._write_state()
        self._running_lane()
        plain = self._cli(["once"])
        self.assertNotIn("▌批次", plain, "默认不许出现派发批次区")
        self.assertIn("派发批次", self._cli(["-v", "once"]), "-v 必须展开批次注解")


if __name__ == "__main__":
    unittest.main()
