"""看板定义配置化：**独立文件 `boards.json`**（2026-10-11 用户裁决）。

**为什么单独一个文件**（本文件就是这条决定的规格）：四个看板曾写死在 `spec.STAGES` +
`spec.STAGE_LABELS` 里。要做**动态看板**（按需建板），板定义必须先成为**数据**——
否则「新建一块板」等于「改源码」。而它不塞进 `disflow.json`：板定义会**长大**，
变更节奏与引擎参数（超时/预算/租约）不同，混在一起 review 也看不清改的是哪件事。

判据（每条都可跑）：

1. **零配置可用**：两个文件都不存在 ⇒ 内建默认四块板（id + 中文标签）；
2. **单一来源**：`cfg.stages` 是 `cfg.boards` 的**派生属性**（不是字段）—— 不可能分叉；
3. **两种形状都收**：`{"boards": [{"id","label"}]}` 与 `{"boards": ["id"]}`（label 回落成 id）；
4. **响亮报错**：形状错 / 未知键 / 重复 id / 空 id / 保留名（`done`）一律拒收，不猜不补；
5. **退役键不静默回落**：`disflow.json` 里写 `stages` ⇒ 报错 + 迁移指引（静默失效是双源的入口）；
6. **端到端**：改 `boards.json` ⇒ 板名、板数、`stage` 写时校验闭集**同时**跟着变；
7. **逐键报来源**：`describe` 必须把 boards 行的来源报成 `boards.json`（不是 disflow.json）；
8. **默认板也是派生的**：root 落**板序第一块**、任务没声明 `stage` 时落 `dev`（是板集里的
   一块）否则落入口板 —— 写死的 `"req"`/`"dev"` 会让节点落在**一块不存在的板**上。
"""
from __future__ import annotations

import json
import os
import unittest

from base import DisflowTestCase, acceptance

from disflow import boards as boards_mod
from disflow import config, spec, store
from disflow.boards import DEFAULT_BOARDS, Board, BoardsError
from disflow.config import Config, ConfigError
from disflow.engine import Engine, EngineError
from disflow.spec import STAGES
from disflow.views import boards_lines


def write_boards(test: DisflowTestCase, payload: object) -> str:
    path = os.path.join(test.tmp, boards_mod.BOARDS_BASENAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


class BoardsFileTest(DisflowTestCase):
    # ── 判据 1：零配置 = 内建默认 ─────────────────────────────────────────
    def test_missing_file_falls_back_to_builtin_defaults(self):
        self.assertFalse(os.path.isfile(boards_mod.boards_path(self.tmp)))
        self.assertEqual(boards_mod.load_boards(self.tmp), DEFAULT_BOARDS)
        self.assertEqual([b.id for b in DEFAULT_BOARDS], list(STAGES))
        self.assertEqual([b.label for b in DEFAULT_BOARDS], ["需求", "设计", "开发", "验证"])

    def test_config_without_boards_file_keeps_builtin_boards(self):
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.stages, STAGES)
        self.assertEqual(cfg.labels["dev"], "开发")

    def test_boards_file_alone_is_enough(self):
        """两个文件**互不为前提**：只有 boards.json 也要生效。"""
        write_boards(self, {"boards": [{"id": "todo", "label": "待办"}]})
        self.assertFalse(os.path.isfile(config.config_path(self.tmp)))
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.stages, ("todo",))
        self.assertEqual(cfg.labels, {"todo": "待办"})

    # ── 判据 2：单一来源（派生属性）────────────────────────────────────────
    def test_stages_is_derived_from_boards(self):
        write_boards(self, {"boards": ["a", "b", "c"]})
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.stages, tuple(b.id for b in cfg.boards))
        self.assertEqual(cfg.stages, ("a", "b", "c"))

    # ── 判据 3：两种形状 ────────────────────────────────────────────────
    def test_object_entries_carry_labels(self):
        write_boards(self, {"boards": [{"id": "req", "label": "需求"}, "dev"]})
        self.assertEqual(boards_mod.load_boards(self.tmp),
                         (Board("req", "需求"), Board("dev", "dev")))

    def test_string_entries_fall_back_to_id_as_label(self):
        write_boards(self, {"boards": ["alpha", "beta"]})
        self.assertEqual([b.label for b in boards_mod.load_boards(self.tmp)],
                         ["alpha", "beta"])

    def test_blank_label_falls_back_to_id(self):
        write_boards(self, {"boards": [{"id": "req", "label": "   "}]})
        self.assertEqual(boards_mod.load_boards(self.tmp), (Board("req", "req"),))

    # ── 判据 4：响亮报错 ────────────────────────────────────────────────
    def test_shape_errors_are_rejected_loudly(self):
        cases = (
            ([1, 2], "顶层应为对象"),
            ({"boards": 5}, "应为数组"),
            ({"boards": []}, "空数组"),
            ({"board": []}, "未知键"),
            ({"boards": [5]}, "应为字符串或对象"),
            ({"boards": [{"label": "甲"}]}, "缺 'id'"),
            ({"boards": [{"id": "a", "name": "甲"}]}, "未知子键"),
            ({"boards": [{"id": 5}]}, "'id' 应为字符串"),
            ({"boards": [{"id": "a", "label": 5}]}, "'label' 应为字符串"),
            ({"boards": [""]}, "不能为空"),
            ({"boards": ["a", "a"]}, "id 重复"),
        )
        for payload, expect in cases:
            with self.subTest(payload=payload):
                write_boards(self, payload)
                with self.assertRaises(BoardsError) as ctx:
                    boards_mod.load_boards(self.tmp)
                self.assertIn(expect, str(ctx.exception))

    def test_top_level_array_tells_you_to_wrap_it(self):
        """顶层直接写数组是最容易犯的错 —— 报错必须**教怎么改**，不是只说错了。"""
        write_boards(self, ["req", "dev"])
        with self.assertRaises(BoardsError) as ctx:
            boards_mod.load_boards(self.tmp)
        self.assertIn('{"boards": [...]}', str(ctx.exception))

    def test_done_is_rejected_as_a_board_id(self):
        """`done` 是**节点状态**不是板：同名板会被读成「干完了」那块，实际按 stage 分组。"""
        self.assertIn("done", boards_mod.RESERVED_IDS)
        write_boards(self, {"boards": [{"id": "done", "label": "完成"}]})
        with self.assertRaises(BoardsError) as ctx:
            boards_mod.load_boards(self.tmp)
        self.assertIn("节点状态", str(ctx.exception))

    def test_malformed_json_is_rejected(self):
        path = os.path.join(self.tmp, boards_mod.BOARDS_BASENAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{不是 JSON")
        with self.assertRaises(BoardsError) as ctx:
            boards_mod.load_boards(self.tmp)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_env_var_can_point_elsewhere(self):
        """与 `$DISFLOW_CONFIG` 同款：环境变量只用于**指文件在哪**。"""
        other = os.path.join(self.tmp, "别处.json")
        with open(other, "w", encoding="utf-8") as handle:
            json.dump({"boards": [{"id": "x", "label": "埃克斯"}]}, handle)
        os.environ[boards_mod.ENV_BOARDS_PATH] = other
        self.addCleanup(os.environ.pop, boards_mod.ENV_BOARDS_PATH, None)
        self.assertEqual(boards_mod.boards_path(self.tmp), os.path.abspath(other))
        self.assertEqual(boards_mod.load_boards(self.tmp), (Board("x", "埃克斯"),))

    # ── 判据 5：退役键 ──────────────────────────────────────────────────
    def test_retired_stages_key_errors_with_migration_hint(self):
        with open(os.path.join(self.tmp, config.CONFIG_BASENAME), "w",
                  encoding="utf-8") as handle:
            json.dump({"stages": ["a"]}, handle)
        with self.assertRaises(ConfigError) as ctx:
            config.load(self.tmp)
        self.assertIn("已退役", str(ctx.exception))
        self.assertIn("boards.json", str(ctx.exception))

    # ── 判据 7：逐键报来源 ──────────────────────────────────────────────
    def test_describe_reports_per_key_sources(self):
        write_boards(self, {"boards": [{"id": "req", "label": "需求"}]})
        cfg_path = os.path.join(self.tmp, config.CONFIG_BASENAME)
        with open(cfg_path, "w", encoding="utf-8") as handle:
            json.dump({"size_budget": 7}, handle)
        # 用真实 cfg（而非 DEFAULTS）——来源列要反映**实际读了哪个文件**。
        cfg = config.load(self.tmp)
        rows = {key: (value, source) for key, value, source in config.describe(cfg, self.tmp)}
        self.assertEqual(rows["boards"][0], "req=需求")
        self.assertEqual(rows["boards"][1], boards_mod.boards_path(self.tmp),
                         "boards 行的来源必须是 boards.json，不是 disflow.json")
        self.assertEqual(rows["size_budget"][1], cfg_path)


class BoardsEndToEndTest(DisflowTestCase):
    """判据 6：改 `boards.json` ⇒ 板名 / 板数 / `stage` 写时校验闭集**同时**跟着变。"""

    def setUp(self) -> None:
        super().setUp()
        write_boards(self, {"boards": [{"id": "todo", "label": "待办"},
                                       {"id": "doing", "label": "进行中"},
                                       {"id": "check", "label": "验收"}]})
        self.engine = Engine(cwd=self.tmp)
        self.engine.init_tree("t", "总目标")
        self.engine.expand("t", "root", [
            {"id": "a", "stage": "doing", "goal": "做 A", "acceptance": acceptance()},
        ])

    def test_stage_write_validation_follows_boards_json(self):
        """板定义是 `stage` 取值的闭集 ⇒ 新板可写、旧板（req）被拒。"""
        self.assertEqual(self.engine.cfg.stages, ("todo", "doing", "check"))
        self.engine.expand("t", "root", [
            {"id": "b", "stage": "check", "goal": "验收 B", "acceptance": acceptance()}])
        with self.assertRaises(EngineError) as ctx:
            self.engine.expand("t", "root", [
                {"id": "c", "stage": "dev", "goal": "旧板名", "acceptance": acceptance()}])
        self.assertIn("dev", str(ctx.exception))

    def test_board_view_shows_the_configured_boards_with_their_labels(self):
        lines = boards_lines(self.engine.tree("t"), stages=self.engine.cfg.stages,
                             labels=self.engine.cfg.labels)
        text = "\n".join(lines)
        self.assertIn("板 1/3 · 待办 [todo] 0/0", text)
        self.assertIn("板 2/3 · 进行中 [doing] 0/1", text)
        self.assertIn("板 3/3 · 验收 [check] 0/0", text)
        self.assertNotIn("[dev]", text, "旧板名不该再出现")

    def test_labels_come_from_the_file_not_from_code(self):
        """标签是**数据**：改文件即改渲染，代码里那份中文名只是内建默认。"""
        write_boards(self, {"boards": [{"id": "todo", "label": "Backlog"},
                                       {"id": "doing", "label": "WIP"}]})
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.labels, {"todo": "Backlog", "doing": "WIP"})
        text = "\n".join(boards_lines(self.engine.tree("t"), stages=cfg.stages,
                                      labels=cfg.labels))
        self.assertIn("Backlog [todo]", text)
        self.assertIn("WIP [doing]", text)


class StageDefaultTest(DisflowTestCase):
    """判据 8：`entry_stage` / `default_stage` 都是**派生**的（不再写死 `"req"`/`"dev"`）。

    为何必须派生：板定义可配后，写死的 stage 会让节点落在**一块不存在的板**上 ——
    写时校验看不见它（`_validate_vocabulary` 只在键存在时校验），视图只能把它当
    「非规范列」追加到末尾。一个静默错位，正是本次配置化要消灭的那类 bug。
    """

    def test_zero_config_keeps_the_builtin_defaults(self):
        """零配置：入口板 `req`、默认板 `dev` —— **行为与配置化之前逐字相同**。"""
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.entry_stage, "req")
        self.assertEqual(cfg.default_stage, "dev")

    def test_entry_board_follows_the_board_order(self):
        """入口板 = 板序第一块；板集里没有 `dev` ⇒ 默认板回落入口板。"""
        write_boards(self, {"boards": ["todo", "doing", "check"]})
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.entry_stage, "todo")
        self.assertEqual(cfg.default_stage, "todo", "板集里没有 dev ⇒ 回落入口板")

    def test_default_board_stays_dev_when_dev_is_a_board(self):
        """`dev` 还在板集里 ⇒ 内建策略不变（`dev` 是「干活的板」，不是从板序能推出来的）。"""
        write_boards(self, {"boards": ["todo", "dev", "ship"]})
        cfg = config.load(self.tmp)
        self.assertEqual(cfg.entry_stage, "todo")
        self.assertEqual(cfg.default_stage, "dev")

    def test_root_and_children_take_the_derived_stages_end_to_end(self):
        write_boards(self, {"boards": ["todo", "doing", "check"]})
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "总目标")
        engine.expand("t", "root", [
            {"id": "a", "goal": "没写 stage", "acceptance": acceptance()},
            {"id": "b", "stage": "check", "goal": "写了", "acceptance": acceptance()},
        ])
        nodes = engine.tree("t")["nodes"]
        self.assertEqual(nodes["root"]["stage"], "todo", "root = 板序第一块")
        self.assertEqual(nodes["a"]["stage"], "todo", "没声明 ⇒ 默认板（此板集里 = 入口板）")
        self.assertEqual(nodes["b"]["stage"], "check", "显式声明不被默认值覆盖")

    def test_stage_is_written_into_the_events_not_read_by_the_reducer(self):
        """默认板在**写时**落进事件 ⇒ reducer 不读配置 ⇒ 事件流可重放。

        负向对照（已跑）：去掉 `Engine.init_tree` 里的 `stage` ⇒ 本测试变红（root 退回 `req`）。
        """
        write_boards(self, {"boards": ["todo", "doing"]})
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "总目标")
        events = store.read_jsonl(store.events_path(spec.SPEC))
        init = next(e for e in events if e["kind"] == "tree.init")
        self.assertEqual(init.get("stage"), "todo",
                         "入口板必须由**写时**给出（reducer 一读配置，事件流就不可重放了）")

    def test_undeclared_stage_is_filled_in_at_write_time(self):
        """同上，子节点：事件里的 `children[].stage` 也必须是显式值。"""
        write_boards(self, {"boards": ["todo", "doing"]})
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "总目标")
        engine.expand("t", "root", [
            {"id": "a", "goal": "没写 stage", "acceptance": acceptance()}])
        events = store.read_jsonl(store.events_path(spec.SPEC))
        expand = next(e for e in events if e["kind"] == "node.expand")
        self.assertEqual(expand["children"][0]["stage"], "todo")


if __name__ == "__main__":
    unittest.main()
