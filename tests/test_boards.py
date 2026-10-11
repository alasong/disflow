"""四块板视图测试（2026-10-11 用户裁决：「我要的是四个看板，需求、设计、开发、测试」）。

判据（每一条都是可跑的断言，不是提示）：

1. **一个阶段一块板**：`boards_layout` 把阶段从「板内的列」升格为「板与板之间的划分」——
   四块板、顺序 == 配置顺序、`index` / `total_boards` 自报；
2. **同一份布局，不是第二份实现**：`boards_layout` 直接消费 `board_layout` 的列
   （分组/深度/理由逐字段相同）——`board_layout` 仍是布局的**唯一实现**；
3. **板间工作流**：每块板带**相邻**阶段的完成度（`upstream` / `downstream`）——
   第一块无上游、最后一块无下游；只给相邻，不给整条链（整条链会把板头变成表格）；
4. **空板恒铺**（与列形态的「干净优先」相反）：四块板的「四」是**结构事实**，
   折叠空板读者就看不出「需求→设计→开发→验证」这条流程；
   空板必须说清是「本阶段还没有任务」还是「被终态折叠」——两者长得一样但意思相反；
5. **标签回落**：`STAGE_LABELS` 缺项时回落成阶段名本身（自定义 `stages` 不崩）；
6. **人读态 = 机器态的渲染**：`boards_lines` 的每一行都来自 `boards_layout`，不另算一套。
"""
from __future__ import annotations

import contextlib
import io
import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine
from disflow.vocab import STAGE_LABELS, STAGES
from disflow.views import board_layout, boards_layout, boards_lines


def _board_section(text: str, marker: str) -> str:
    """从整篇渲染里切出**一块板**的正文（按板头切）——判据要按板看，不能全文混着看。"""
    sections: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("══"):
            sections.append([])
        if sections:
            sections[-1].append(line)
    for section in sections:
        if marker in section[0]:
            return "\n".join(section)
    raise AssertionError(f"没有这块板：{marker}\n{text}")


class BoardsLayoutTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "总目标")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "acceptance": acceptance()},
            {"id": "b", "stage": "verify", "goal": "验证 B", "role": "qa"},
        ])
        self.engine.require("t", "b", "a")
        self.tree = self.engine.tree("t")

    def test_one_board_per_stage_in_configured_order(self):
        """判据 1：四块板，顺序 == 配置顺序，index/total_boards 自报。"""
        layout = boards_layout(self.tree)
        self.assertEqual([b["stage"] for b in layout["boards"]],
                         list(STAGES))
        self.assertEqual([b["index"] for b in layout["boards"]], [1, 2, 3, 4])
        self.assertEqual([b["total_boards"] for b in layout["boards"]], [4, 4, 4, 4])

    def test_boards_follow_custom_stage_config(self):
        """判据 1：板数随配置走；闭集外的 stage **仍要看得见**（追加为板，不静默隐藏）。"""
        boards = boards_layout(self.tree, stages=("verify", "dev"))["boards"]
        self.assertEqual([b["stage"] for b in boards][:2], ["verify", "dev"])
        self.assertEqual(boards[2]["stage"], "req", "root 的 stage 不在自定义闭集里 → 追加")
        self.assertFalse(boards[2]["canonical"], "追加的板必须标非规范")
        self.assertEqual(boards[0]["total_boards"], 3)

    def test_boards_reuse_the_single_layout_implementation(self):
        """判据 2：板 = 列的另一种分组，**分组内容逐字段相同**（不是第二份实现）。"""
        columns = board_layout(self.tree)["columns"]
        boards = boards_layout(self.tree)["boards"]
        self.assertEqual(len(columns), len(boards))
        for column, board in zip(columns, boards, strict=True):
            self.assertEqual(column["stage"], board["stage"])
            self.assertEqual(column["done"], board["done"])
            self.assertEqual(column["tasks"], board["tasks"])
            self.assertEqual(column["groups"], board["groups"], "分组/深度/理由必须同源")

    def test_boards_carry_adjacent_stage_flow(self):
        """判据 3：板间工作流 = 相邻阶段的完成度；首块无上游、末块无下游。"""
        boards = boards_layout(self.tree)["boards"]
        self.assertIsNone(boards[0]["upstream"], "第一块板没有上游")
        self.assertIsNone(boards[-1]["downstream"], "最后一块板没有下游")
        self.assertEqual(boards[1]["upstream"]["stage"], "req")
        self.assertEqual(boards[1]["downstream"]["stage"], "dev")
        self.assertEqual(boards[2]["upstream"]["stage"], "design")
        self.assertEqual(boards[3]["upstream"]["stage"], "dev")
        self.assertEqual(boards[3]["upstream"]["done"], 0)
        self.assertEqual(boards[3]["upstream"]["tasks"], 1)

    def test_flow_reports_peer_counts_not_just_names(self):
        """判据 3：上游报的是**完成度**（d/t），不是一个光秃秃的名字。"""
        self.engine.start("t", "a", holder="w1")
        self.engine.collect("t", "a", result={"status": "ok", "summary": "s",
                                              "changed_files": []})
        self.engine.done("t", "a")
        boards = boards_layout(self.engine.tree("t"))["boards"]
        verify = [b for b in boards if b["stage"] == "verify"][0]
        self.assertEqual(verify["upstream"]["done"], 1)
        self.assertEqual(verify["upstream"]["tasks"], 1)

    def test_stage_label_falls_back_to_the_stage_name(self):
        """判据 5：没登记标签的阶段回落成阶段名（自定义 stages 不崩）。"""
        self.assertEqual(STAGE_LABELS["req"], "需求")
        layout = boards_layout(self.tree, stages=("未来阶段",))
        self.assertEqual(layout["boards"][0]["label"], "未来阶段")

    def test_non_canonical_stage_gets_its_own_board(self):
        """读时容忍：闭集外的历史 stage 仍要看得见（不静默丢），并标非规范。"""
        tree = {"root": "root", "nodes": {
            "a": {"id": "a", "kind": "task", "stage": "dev", "goal": "做 A"},
            "f": {"id": "f", "kind": "task", "stage": "未来的列", "goal": "旧版本写的"},
        }}
        boards = boards_layout(tree)["boards"]
        self.assertEqual([b["stage"] for b in boards][-1], "未来的列")
        self.assertFalse(boards[-1]["canonical"], "非规范板必须标出来")


class BoardsLinesTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "总目标")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "acceptance": acceptance()},
        ])
        self.tree = self.engine.tree("t")

    def test_every_board_is_rendered_even_when_empty(self):
        """判据 4：四块板**一块都不少**——折叠空板就看不出流程本身。"""
        lines = boards_lines(self.tree)
        heads = [line for line in lines if line.startswith("══")]
        self.assertEqual(len(heads), 4, f"必须铺满四块板：{heads}")
        text = "\n".join(lines)
        self.assertIn("板 1/4 · 需求 [req] 0/0", text)
        self.assertIn("板 4/4 · 验证 [verify] 0/0", text)

    def test_empty_board_says_why_it_is_empty(self):
        """判据 4：空板要区分「真没有任务」与「被终态折叠」——两者意思相反。

        `active_only` 是**视图层参数**（CLI 默认 True），视图函数默认 False（= 全量）。
        """
        text = "\n".join(boards_lines(self.tree))
        self.assertIn("（空 —— 本阶段还没有任务）", text)
        self.engine.start("t", "a", holder="w1")
        self.engine.collect("t", "a", result={"status": "ok", "summary": "s",
                                              "changed_files": []})
        self.engine.done("t", "a")
        tree = self.engine.tree("t")
        folded = "\n".join(boards_lines(tree, active_only=True))
        self.assertIn("（终态折叠：--all 展开）", folded)
        dev = _board_section(folded, "[dev]")
        self.assertNotIn("（空 —— 本阶段还没有任务）", dev,
                         "有任务但全折叠 ≠ 空板（别的板空是别的板的事）")
        self.assertIn("✓ a", "\n".join(boards_lines(tree)),
                      "全量态（active_only=False）必须看得见终态行")

    def test_board_head_shows_upstream_completion(self):
        """判据 3：板头把「上一关过了没」直接写出来。"""
        text = "\n".join(boards_lines(self.tree))
        self.assertIn("══ 板 2/4 · 设计 [design] 0/0 ══  ← 需求 0/0", text)
        self.assertNotIn("板 1/4 · 需求 [req] 0/0 ══  ←", text,
                         "第一块板没有上游，不该画箭头")

    def test_empty_boards_can_be_filtered_explicitly(self):
        """`empty_boards=False` 是**显式**收窄（不是默认）——默认必须全铺。"""
        self.assertEqual(len([line for line in boards_lines(self.tree) if line.startswith("══")]), 4)
        folded = [line for line in boards_lines(self.tree, empty_boards=False)
                  if line.startswith("══")]
        self.assertEqual([line for line in folded if "[dev]" in line], folded,
                         "收窄后只该剩有任务的那块板（dev）")

    def test_lines_are_a_rendering_of_the_layout(self):
        """判据 6：人读态的每个节点行都能在布局里找到（同源，不另算一套）。"""
        layout = boards_layout(self.tree)
        lines = boards_lines(self.tree)
        for board in layout["boards"]:
            for group in board["groups"]:
                for row in group["rows"]:
                    self.assertTrue(any(row["node"] in line for line in lines),
                                    f"{row['node']} 在布局里有、渲染里丢了")


class BoardsCliTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "总目标")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "acceptance": acceptance()},
        ])

    def test_by_board_and_stage_are_mutually_exclusive(self):
        """`--by board` 与 `--stage` 互斥：放行会打出「只有一块板」的版面（宽错）。"""
        from disflow.cli import build_parser, cmd_board
        args = build_parser().parse_args(["board", "t", "--by", "board", "--stage", "dev"])
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = cmd_board(self.engine, args)
        self.assertEqual(code, 2, "用法错误 → 退出码 2")
        self.assertIn("互斥", err.getvalue())

    def test_board_by_default_keeps_the_column_form(self):
        """默认仍是列形态（向后兼容）：`--by` 省略 = `column`。"""
        from disflow.cli import build_parser
        args = build_parser().parse_args(["board", "t"])
        self.assertEqual(args.by, "column")


if __name__ == "__main__":
    unittest.main()
