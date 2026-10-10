"""视图层测试（D80）：三视图同源、渲染正确；看板 = **阶段列 × 树层级 × 状态分组**。

看板布局的判据（2026-10-11 用户要求「看板的运行视图，能按照阶段列出来，并且能按树结构的
类别分层」）：

1. **四个阶段列按配置顺序全出现**（`req/design/dev/verify`，空列显式 `0/0`）——只打出现过的
   列时，读者看不出这是开发流程，也看不出「某阶段为空」这个事实；
2. **列内树层级**：容器（`kind=container`）是**结构行**，任务按 `parent` 缩进；深度按
   **列内**祖先链算——祖先不在本列时子节点照旧出现（不许丢、不许负缩进）；
3. **`[req]` 列 = 树自身的目标**：root 是容器，其 `goal` 就是需求；容器**不是**该列的
   任务行，所以不计入 `done/total`（与 `ready.progress`「容器不量化」同一口径）；
4. **列内按状态分组**（运行中/可派/待前置/被在跑挡住/完成/跳过/失败）——某组只要有一个节点
   就必须出现；**可派性与 `drive` 同源**（直接消费 `dispatch.drive` 的读数，视图层不重算写面冲突）；
5. **非规范 stage 追加而不丢**（旧版本/别的配置写下的历史值仍要看得见，只标注不隐藏）；
6. **`--stage` 过滤不变**（只输出被选中的那一列；合法空列仍打 `0/0`；`done` 不是列）；
7. **`board_layout` 是人读态与 `--json` 的**唯一**来源**（带父子/深度，供运行视图消费）；
8. **goal 呈现（2026-10-11 用户当场提）**：人读态终态行（`✓ 完成` / `— 跳过`）**不打印 goal**
   （只给 `标记 + id + 状态`），其余行走 `truncate_goal`（首行、≤80 字、`…` 结尾）；
   **完整 goal 只在 `board --json`**；`active_only=True` 只留「运行中 + 待完成」那些组。
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow import store
from disflow.dispatch import drive
from disflow.engine import Engine, EngineError
from disflow.spec import STAGES
from disflow.views import (
    board_layout,
    board_lines,
    graph_edges,
    graph_lines,
    to_dot,
    tree_lines,
    truncate_goal,
)


class ViewsTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "dev", "goal": "做 A", "acceptance": acceptance()},
            {"id": "b", "stage": "verify", "goal": "验证 B", "role": "qa"},
        ])
        self.engine.require("t", "b", "a")
        self.engine.relate("t", "b", "a")
        self.tree = self.engine.tree("t")

    # ── 列：配置顺序 + 空列 + 非规范列 ─────────────────────────────────────

    def test_board_groups_by_stage(self):
        lines = board_lines(self.tree)
        self.assertIn("[dev] 0/1", lines)
        self.assertIn("[verify] 0/1", lines)
        self.assertTrue(any("a" in line and "做 A" in line for line in lines))

    def test_board_shows_every_configured_column_even_when_empty(self):
        """固定列：需求/设计两列**没有任务也要出现**（0/0）——否则看不出流程与「设计为空」。"""
        lines = board_lines(self.tree)
        self.assertEqual([line for line in lines if line.startswith("[")],
                         ["[req] 0/0", "[design] 0/0", "[dev] 0/1", "[verify] 0/1"])

    def test_board_column_order_follows_the_configured_stages(self):
        """列顺序 == 配置顺序（含自定义配置）——视图层不与配置争主权。"""
        self.assertEqual(STAGES, ("req", "design", "dev", "verify"))
        lines = board_lines(self.tree, stages=("verify", "req", "design", "dev"))
        self.assertEqual([line for line in lines if line.startswith("[")],
                         ["[verify] 0/1", "[req] 0/0", "[design] 0/0", "[dev] 0/1"])

    def test_stage_done_is_not_a_column_and_is_rejected(self):
        """`done` 是**状态**不是列：默认闭集里没有它，写进去必须**响亮拒收且不落事件**。"""
        self.assertNotIn("done", STAGES)
        before = len(store.load_events(self.engine.spec))
        with self.assertRaises(EngineError) as ctx:
            self.engine.set_stage("t", "a", "done", reason="done 是状态不是列")
        self.assertIn("stage 取值非法", str(ctx.exception))
        self.assertEqual(len(store.load_events(self.engine.spec)), before, "拒收不许落事件")
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["stage"], "dev", "拒收不许改投影")

        with self.assertRaises(EngineError) as ctx_expand:
            self.engine.expand("t", "root", [{"id": "c", "stage": "done",
                                               "goal": "建节点也走同一套闭集"}])
        self.assertIn("stage 取值非法", str(ctx_expand.exception))
        self.assertEqual(len(store.load_events(self.engine.spec)), before, "拒收不许落事件")

    def test_non_canonical_stage_is_appended_and_marked(self):
        """不在闭集里的历史 stage **不许丢**：追加在末尾并标注（读时容忍 ≠ 静默隐藏）。"""
        tree = {"root": "root", "nodes": {
            "a": {"id": "a", "kind": "task", "stage": "dev", "goal": "做 A"},
            "f": {"id": "f", "kind": "task", "stage": "未来的列", "goal": "旧版本写的"},
        }}
        lines = board_lines(tree)
        headers = [line for line in lines if line.startswith("[")]
        self.assertEqual(headers, ["[req] 0/0", "[design] 0/0", "[dev] 0/1", "[verify] 0/0",
                                   "[未来的列] 0/1  ⚠ 非规范列（不在配置的 stages 内）"])
        self.assertTrue(any("f" in line and "旧版本写的" in line for line in lines),
                        "非规范列里的节点必须仍然看得见")

    def test_board_stage_filter(self):
        lines = board_lines(self.tree, stage="verify")
        self.assertIn("[verify] 0/1", lines)
        self.assertFalse(any(line.startswith("[dev]") for line in lines))

    def test_board_stage_filter_emits_only_the_selected_column(self):
        """`--stage` 过滤不变：只有被选中的那一列（其余列一个字符都不出现）。"""
        for wanted, header in (("dev", "[dev] 0/1"), ("verify", "[verify] 0/1")):
            with self.subTest(stage=wanted):
                lines = board_lines(self.tree, stage=wanted)
                self.assertEqual([line for line in lines if line.startswith("[")], [header])
        # 被选中的列没有节点时显式报 0（改动前什么都不打，与「列名拼错」分不开）
        self.assertEqual(board_lines(self.tree, stage="design"), ["[design] 0/0"])

    # ── 层级：结构行 + 列内缩进 ────────────────────────────────────────────

    def _fixture(self) -> dict:
        """摆出**六类状态 + 嵌套结构**的树（手工构造，让渲染判据确定、可读）。

        为何手工构造而不是跑一串动词：本文件测的是**渲染**——status 由事件流写入，视图只读它；
        把六类状态与三种深度一次摆全，比在引擎里跑十来个动词更快也更清楚。
        """
        def task(node_id: str, parent: str, stage: str, goal: str, status: str = "pending",
                 **extra) -> dict:
            node = {"id": node_id, "parent": parent, "kind": "task", "stage": stage,
                    "goal": goal, "status": status}
            node.update(extra)
            return node

        return {"root": "root", "goal": "树目标：看板=阶段列+树层级", "nodes": {
            "root": {"id": "root", "parent": None, "kind": "container", "stage": "req",
                     "goal": "树目标：看板=阶段列+树层级"},
            "box": {"id": "box", "parent": "root", "kind": "container", "stage": "dev",
                    "goal": "开发容器（结构行）"},
            "inner": task("inner", "box", "dev", "容器里的任务"),
            "deep": task("deep", "inner", "dev", "更深的子任务"),
            "runner": task("runner", "root", "dev", "在跑的", status="running",
                           lease={"holder": "host", "expires_at": "2999-01-01T00:00:00"},
                           touch_plan=["disflow/views.py"]),
            "go": task("go", "root", "dev", "可派的", touch_plan=["src/go.py"]),
            "hold": task("hold", "root", "verify", "前置（在别的列）"),
            "late": task("late", "root", "dev", "等前置的", requires=["hold"]),
            "clash": task("clash", "root", "dev", "被在跑挡住的",
                          touch_plan=["disflow/views.py"]),
            "ok": task("ok", "root", "dev", "完成的", status="done"),
            "skipper": task("skipper", "root", "dev", "跳过的", status="skipped"),
            "bad": task("bad", "root", "dev", "失败的", status="failed"),
            "weird": task("weird", "root", "dev", "非规范状态的", status="wip"),
        }}

    def _column(self, layout: dict, stage: str) -> dict:
        return next(col for col in layout["columns"] if col["stage"] == stage)

    def _rows(self, layout: dict, stage: str) -> dict[str, dict]:
        column = self._column(layout, stage)
        return {row["node"]: row for group in column["groups"] for row in group["rows"]}

    def _group_rows(self, layout: dict, stage: str, group: str) -> list[str]:
        column = self._column(layout, stage)
        return [row["node"] for grp in column["groups"] if grp["group"] == group
                for row in grp["rows"]]

    def test_board_container_is_a_structural_row_not_a_counted_task(self):
        """容器是**结构行**（`◦`），不是任务行：出列但不计入 `done/total`。"""
        layout = board_layout(self._fixture())
        req = self._column(layout, "req")
        self.assertEqual(req["header"], "[req] 0/0", "容器不计入任务计数")
        self.assertEqual(self._group_rows(layout, "req", "structure"), ["root"])
        root_row = self._rows(layout, "req")["root"]
        self.assertEqual(root_row["kind"], "container")
        self.assertEqual(root_row["mark"], "◦")
        dev = self._column(layout, "dev")
        self.assertEqual(self._group_rows(layout, "dev", "structure"), ["box"])
        self.assertEqual(dev["tasks"], 10, "dev 列的任务行 = 10（两个容器都不算）")

    def test_board_req_column_shows_the_tree_goal(self):
        """`[req]` 列显示**树自身的目标**（root 的 goal）——容器被排除时需求就被藏了。"""
        fixture = self._fixture()
        layout = board_layout(fixture, tree_name="t")
        lines = board_lines(fixture)
        self.assertEqual(layout["goal"], "树目标：看板=阶段列+树层级")
        self.assertEqual(self._rows(layout, "req")["root"]["goal"], fixture["goal"])
        self.assertEqual(lines[0], "[req] 0/0")
        self.assertTrue(any("◦ root" in line and fixture["goal"] in line for line in lines),
                        "[req] 列里必须看见需求本身")

    def test_board_indents_tasks_by_parent_within_the_column(self):
        """列内缩进反映父子：`box → inner → deep` 三层的深度必须是 0/1/2。"""
        layout = board_layout(self._fixture())
        rows = self._rows(layout, "dev")
        self.assertEqual((rows["box"]["depth"], rows["inner"]["depth"], rows["deep"]["depth"]),
                         (0, 1, 2))
        lines = board_lines(self._fixture())
        inner = next(line for line in lines if "inner" in line)
        deep = next(line for line in lines if "deep" in line)
        go = next(line for line in lines if " go  " in line)
        self.assertTrue(inner.startswith("    · inner"), f"子任务应缩进一层：{inner!r}")
        self.assertTrue(deep.startswith("      · deep"), f"孙任务应缩进两层：{deep!r}")
        self.assertTrue(go.startswith("  · go"), f"顶层任务不缩进：{go!r}")

    def test_board_depth_ignores_ancestors_outside_the_column(self):
        """祖先不在本列时：子节点**照旧出现**、深度回到本列顶层（不许丢、不许负缩进）。"""
        layout = board_layout(self._fixture())
        rows = self._rows(layout, "dev")
        self.assertEqual(rows["runner"]["parent"], "root", "root 在 req 列，不在 dev 列")
        self.assertEqual(rows["runner"]["depth"], 0)
        self.assertEqual(rows["late"]["depth"], 0)
        # 反过来：`hold` 自己那列里它就是顶层（它是 verify 列唯一节点）
        self.assertEqual(self._rows(layout, "verify")["hold"]["depth"], 0)

    # ── 状态分组 ──────────────────────────────────────────────────────────

    def test_board_groups_every_status_category(self):
        """六类状态 + 结构行 + 非规范兜底：**每类只要有一个节点就必须出现**（不许静默丢）。"""
        layout = board_layout(self._fixture())
        dev = self._column(layout, "dev")
        self.assertEqual([group["label"] for group in dev["groups"]],
                         ["◦ 结构行（容器）", "▶ 运行中", "· 可派", "· 待前置", "· 被在跑挡住",
                          "✓ 完成", "— 跳过", "✗ 失败", "? 非规范状态（原样显示）"])
        self.assertEqual(self._group_rows(layout, "dev", "running"), ["runner"])
        self.assertEqual(self._group_rows(layout, "dev", "ready"), ["inner", "deep", "go"])
        self.assertEqual(self._group_rows(layout, "dev", "blocked"), ["late"])
        self.assertEqual(self._group_rows(layout, "dev", "deferred"), ["clash"])
        self.assertEqual(self._group_rows(layout, "dev", "done"), ["ok"])
        self.assertEqual(self._group_rows(layout, "dev", "skipped"), ["skipper"])
        self.assertEqual(self._group_rows(layout, "dev", "failed"), ["bad"])
        self.assertEqual(self._group_rows(layout, "dev", "other"), ["weird"])
        placed = {row["node"] for group in dev["groups"] for row in group["rows"]}
        self.assertEqual(placed, set(self._fixture()["nodes"]) - {"root", "hold"},
                         "dev 列的每个节点都必须落进某一组（含 root 之外的容器）")

    def test_board_group_details_name_holder_requires_and_overlap(self):
        """理由要**点名**：谁在跑 / 缺哪个前置 / 与谁重叠哪些路径——不许只给一个状态词。"""
        lines = board_lines(self._fixture())
        running = next(line for line in lines if "runner" in line)
        blocked = next(line for line in lines if "late" in line)
        deferred = next(line for line in lines if "clash" in line)
        other = next(line for line in lines if "weird" in line)
        self.assertIn("（holder=host）", running)
        self.assertIn("（缺 hold）", blocked)
        self.assertIn("runner", deferred)
        self.assertIn("disflow/views.py", deferred)
        self.assertIn("status='wip'", other)

    def test_board_readiness_is_the_same_source_as_drive(self):
        """可派性**与 `drive` 同源**：看板的分组必须与 `drive` 的读数是同一份判据。"""
        fixture = self._fixture()
        facts = drive("t", fixture)
        layout = board_layout(fixture)
        ready_rows = self._group_rows(layout, "dev", "ready")
        blocked_rows = self._group_rows(layout, "dev", "blocked")
        wave = [nid for wave in facts["waves"] for nid in wave]
        self.assertEqual(set(ready_rows) | {"hold"}, set(wave),
                         "可派 = drive 的波次内容（hold 在 verify 列，不在这段取样里）")
        self.assertEqual(self._group_rows(layout, "verify", "ready"), ["hold"])
        self.assertEqual({entry["node"] for entry in facts["deferred"]}, {"clash"})
        self.assertEqual(self._group_rows(layout, "dev", "deferred"), ["clash"])
        self.assertEqual(set(blocked_rows), set(facts["blocked"]), "待前置 = drive 的 blocked")

    # ── 布局单一来源（人读态 = --json 的渲染）────────────────────────────

    def test_board_layout_carries_hierarchy_for_the_run_view(self):
        """`--json` 的输入就是这份 layout：每行都带 `parent` + `depth`（运行视图据此分层）。"""
        layout = board_layout(self._fixture(), tree_name="t")
        self.assertEqual(layout["tree"], "t")
        rows = [row for col in layout["columns"] for group in col["groups"]
                for row in group["rows"]]
        for row in rows:
            self.assertIn("parent", row)
            self.assertIn("depth", row)
        by_id = {row["node"]: row for row in rows}
        self.assertIsNone(by_id["root"]["parent"])
        self.assertEqual((by_id["inner"]["parent"], by_id["inner"]["depth"]), ("box", 1))
        self.assertEqual((by_id["deep"]["parent"], by_id["deep"]["depth"]), ("inner", 2))

    def test_board_lines_render_that_layout(self):
        """人读态**只是** layout 的渲染：列头与组标签（顺序、文本）逐一对得上。

        终态行（`✓ 完成`/`— 跳过`）只给 `标记 + id + 状态`（**不打印 goal**）——整段 goal
        会把屏幕冲垮，完整文本留在 `--json`；其余行的 goal 原样渲染（本例目标都很短）。
        """
        layout = board_layout(self._fixture())
        lines = board_lines(self._fixture())
        self.assertEqual([line for line in lines if line.startswith("[")],
                         [col["header"] for col in layout["columns"]])
        labels = [group["label"] for col in layout["columns"] for group in col["groups"]]
        self.assertEqual([line for line in lines if line in labels], labels)
        for col in layout["columns"]:
            for group in col["groups"]:
                for row in group["rows"]:
                    indent = "  " * (row["depth"] + 1)
                    head = f"{indent}{row['mark']} {row['node']}  "
                    line = next((x for x in lines if x.startswith(head)), None)
                    self.assertIsNotNone(
                        line, f"{row['node']} 没按列内深度 {row['depth']} 渲染")
                    assert line is not None
                    if group["group"] in ("done", "skipped"):
                        self.assertNotIn(row["goal"], line,
                                         "终态行默认不打印 goal（完整文本在 --json）")
                    else:
                        self.assertEqual(line[len(head):].split("  （")[0], row["goal"],
                                         "非终态行 goal 原样（截断只对超长目标生效）")

    # ── goal 呈现：单一截断助手 + 终态行不打 goal ─────────────────────────

    def test_truncate_goal_is_the_single_truncation_helper(self):
        """截断助手（views **唯一实现**）：取首行、折叠空白、超长以 `…` 结尾且总长 ≤ limit。"""
        self.assertEqual(truncate_goal("短目标"), "短目标")
        self.assertEqual(truncate_goal("首行要有\n第二行不要"), "首行要有")
        self.assertEqual(truncate_goal(None), "")
        self.assertEqual(truncate_goal("  \n  "), "")
        cut = truncate_goal("目" * 200)
        self.assertEqual(len(cut), 80, "截断后总长 ≤ 80（含省略号）")
        self.assertEqual(cut, "目" * 79 + "…")
        self.assertEqual(len(truncate_goal("目" * 200, limit=10)), 10)

    def test_board_lines_omit_the_goal_on_done_and_skipped_rows(self):
        """`✓ 完成` / `— 跳过` 行默认只给 `标记 + id + 状态`（整段 goal 让屏幕没法扫）。"""
        long_goal = "把这件事做完 —— " + "细节" * 120
        tree = {"root": "root", "goal": "树目标", "nodes": {
            "root": {"id": "root", "parent": None, "kind": "container", "stage": "req",
                     "goal": "树目标"},
            "ok": {"id": "ok", "parent": "root", "kind": "task", "stage": "dev",
                   "goal": long_goal, "status": "done"},
            "sk": {"id": "sk", "parent": "root", "kind": "task", "stage": "dev",
                   "goal": long_goal, "status": "skipped"},
            "run": {"id": "run", "parent": "root", "kind": "task", "stage": "dev",
                    "goal": long_goal, "status": "pending"},
        }}
        lines = board_lines(tree)
        done = next(line for line in lines if line.lstrip().startswith("✓ ok"))
        skip = next(line for line in lines if line.lstrip().startswith("— sk"))
        for line in (done, skip):
            self.assertNotIn(long_goal, line)
            self.assertNotIn("细节", line, "终态行一个 goal 片段都不得出现")
        self.assertIn("完成", done)
        self.assertIn("跳过", skip)
        self.assertFalse(any(long_goal in line for line in lines), "整段 goal 不出现在人读态")
        pending = next(line for line in lines if line.lstrip().startswith("· run"))
        self.assertTrue(pending.endswith("…"), "超长 goal 截断到 80 字并以 … 结尾")
        self.assertIn("细节", pending)

    def test_board_layout_keeps_the_full_goal_for_the_json_view(self):
        """完整 goal 不丢：`board --json`（layout 的 `goal`）仍是全文——截断只属于人读态。"""
        long_goal = "很长的目标：" + "内容" * 200
        tree = {"root": "root", "goal": "树目标", "nodes": {
            "root": {"id": "root", "parent": None, "kind": "container", "stage": "req",
                     "goal": "树目标"},
            "run": {"id": "run", "parent": "root", "kind": "task", "stage": "dev",
                    "goal": long_goal, "status": "pending"},
        }}
        layout = board_layout(tree)
        row = next(r for col in layout["columns"] for g in col["groups"]
                   for r in g["rows"] if r["node"] == "run")
        self.assertEqual(row["goal"], long_goal)

    def test_active_only_hides_terminal_and_non_canonical_groups(self):
        """`active_only`（运行视图 `--active`）：只留「运行中 + 待完成」（含结构行），
        隐藏 `✓/—/✗` 与非规范兜底；**列头计数不改写事实**；默认不变。"""
        fixture = self._fixture()
        layout = board_layout(fixture, active_only=True)
        labels = [g["label"] for col in layout["columns"] for g in col["groups"]]
        self.assertEqual(labels, ["◦ 结构行（容器）", "◦ 结构行（容器）", "▶ 运行中",
                                  "· 可派", "· 待前置", "· 被在跑挡住", "· 可派"])
        self.assertFalse({"✓ 完成", "— 跳过", "✗ 失败", "? 非规范状态（原样显示）"}
                         & set(labels))
        rows = {r["node"] for col in layout["columns"] for g in col["groups"]
                for r in g["rows"]}
        self.assertEqual(rows, {"root", "box", "inner", "deep", "runner", "go", "late",
                                "clash", "hold"})
        for hidden in ("ok", "skipper", "bad", "weird"):
            self.assertNotIn(hidden, rows)
        self.assertEqual(self._column(layout, "dev")["header"], "[dev] 2/10",
                         "隐藏 ≠ 改写事实：列头计数照旧是全列口径")
        # 默认（不加旗标）行为不变：九组照旧全出现
        default_labels = [g["label"] for col in board_layout(fixture)["columns"]
                          for g in col["groups"]]
        self.assertIn("✓ 完成", default_labels)
        self.assertIn("✗ 失败", default_labels)

    def test_tree_indents_by_parent(self):
        lines = tree_lines(self.tree)
        self.assertTrue(lines[0].startswith("◦ root"))
        self.assertTrue(any(line.startswith("  · a") for line in lines))

    def test_graph_edges_and_dot(self):
        edges = graph_edges(self.tree)
        self.assertIn(("b", "requires", "a"), edges)
        self.assertIn(("b", "relates", "a"), edges)
        self.assertTrue(any("b ─▶ a" in line for line in graph_lines(self.tree)))
        dot = to_dot(self.tree)
        self.assertTrue(dot.startswith("digraph"))
        self.assertIn('"b" -> "a"', dot)

    def test_views_are_same_projection(self):
        # 同一投影：三种视图各自渲染，节点集合一致（看板按父/子排，容器是结构行）
        nodes_in_tree = {line.split()[1] for line in tree_lines(self.tree)[1:]}
        layout = board_layout(self.tree)
        nodes_in_board = {row["node"] for col in layout["columns"]
                          for group in col["groups"] for row in group["rows"]}
        self.assertEqual(nodes_in_tree, {"a", "b"})
        self.assertEqual(nodes_in_board, {"a", "b", "root"})


if __name__ == "__main__":
    unittest.main()
