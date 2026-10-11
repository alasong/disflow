"""看板策略：逐列 **WIP 上限**（FR-30，分发）与逐列 **默认验收**（FR-31，验收）。

**为什么在这里**：ADR 0005 把看板升为一等实体后，「分发器 / 验收器是看板附属」不再是说法，
而是两条可跑判据——策略挂在板/列上，节点不各自携带：

1. **FR-30 WIP**：列内已在跑数达上限 ⇒ 该列其余就绪者**不派**，进 `drive` 的 `deferred`
   并点名原因（绝不静默丢）；无 wip 的板行为与改动前**逐字节一致**。
2. **FR-31 验收**：节点未自带 `acceptance` 时，由**该列**看板的默认验收派生；节点自带则优先。
   一次 `done` 里门 / 空洞判据 / 契约 / 必填声明**同源**（同一份有效验收）。
3. **schema**：`wip` / `acceptance` 进板定义、随事件流持久化、可重放；形状错误**响亮拒收**。
"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance, always_fails

from disflow.boards import Board, BoardsError, dump, from_projection, parse
from disflow.dispatch import drive
from disflow.engine import Engine, GateReject


class BoardSchemaTest(DisflowTestCase):
    """板定义的形状：新策略字段收得进、坏形状拒得掉、空策略不凭空写出。"""

    def test_parse_reads_wip_and_acceptance(self):
        boards = parse({"boards": [{"id": "dev", "label": "开发", "wip": 2,
                                    "acceptance": {"cmd": "true", "expect": "exit0"}}]})
        self.assertEqual(boards[0].wip, 2)
        self.assertEqual(boards[0].acceptance, {"cmd": "true", "expect": "exit0"})

    def test_parse_rejects_bad_wip(self):
        for bad in (0, -1, "2", True, 1.5):
            with self.assertRaises(BoardsError, msg=f"wip={bad!r} 应被拒收"):
                parse({"boards": [{"id": "dev", "wip": bad}]})

    def test_parse_rejects_non_object_acceptance(self):
        with self.assertRaises(BoardsError):
            parse({"boards": [{"id": "dev", "acceptance": "exit0"}]})

    def test_dump_omits_absent_policy(self):
        """零配置 / 旧板条目仍是最小的 `{id,label}`——不凭空多出字段（重放逐字不变）。"""
        self.assertEqual(dump((Board("dev", "开发"),)), [{"id": "dev", "label": "开发"}])

    def test_dump_round_trips_policy(self):
        original = (Board("dev", "开发", wip=2, acceptance={"cmd": "true", "expect": "exit0"}),)
        self.assertEqual(parse({"boards": dump(original)}), original)

    def test_from_projection_reads_policy_and_tolerates_garbage(self):
        boards = from_projection([
            {"id": "dev", "wip": 2, "acceptance": {"cmd": "true"}},
            {"id": "qa", "wip": "bad", "acceptance": "bad"},   # 读时容忍：非法项忽略，不炸
        ])
        self.assertEqual(boards[0].wip, 2)
        self.assertEqual(boards[0].acceptance, {"cmd": "true"})
        self.assertIsNone(boards[1].wip)
        self.assertIsNone(boards[1].acceptance)


class WipPolicyTest(DisflowTestCase):
    """FR-30：逐列 WIP 上限在 `drive` 生效（只推不跑）。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def test_no_wip_keeps_all_ready_tasks_dispatchable(self):
        """无 wip 的板：两就绪任务同波可派（基线与改动前一致）。"""
        self.engine.init_tree("t", "g", boards=(Board("dev", "开发"),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "A"},
                                         {"id": "b", "goal": "B"}])
        facts = drive("t", self.engine.tree("t"))
        self.assertEqual(sum(len(wave) for wave in facts["waves"]), 2)
        self.assertNotIn("deferred", facts)

    def test_full_column_defers_extra_ready_tasks(self):
        self.engine.init_tree("t", "g", boards=(Board("dev", "开发", wip=1),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "A"},
                                         {"id": "b", "goal": "B"}])
        # 开工前：列内无人在跑 ⇒ 两者都可派
        self.assertEqual(sum(len(w) for w in drive("t", self.engine.tree("t"))["waves"]), 2)
        self.engine.start("t", "a")
        facts = drive("t", self.engine.tree("t"))
        self.assertEqual(facts["waves"], [], "列已满，不该再派同列任务")
        deferred = {entry["node"]: entry["why"] for entry in facts.get("deferred", [])}
        self.assertIn("b", deferred)
        self.assertIn("WIP", deferred["b"])

    def test_wip_does_not_block_a_different_column(self):
        """WIP 是**逐列**的：dev 满不挡 verify 的活。"""
        self.engine.init_tree("t", "g", boards=(Board("dev", "开发", wip=1),
                                                Board("verify", "验证")))
        self.engine.expand("t", "root", [{"id": "a", "stage": "dev", "goal": "A"},
                                         {"id": "v", "stage": "verify", "goal": "V"}])
        self.engine.start("t", "a")
        facts = drive("t", self.engine.tree("t"))
        self.assertEqual([nid for wave in facts["waves"] for nid in wave], ["v"])


class AcceptancePolicyTest(DisflowTestCase):
    """FR-31：逐列默认验收在 `done` 派生（节点自带优先）。"""

    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def test_column_acceptance_is_used_when_node_has_none(self):
        self.engine.init_tree("t", "g", boards=(
            Board("dev", "开发", acceptance=acceptance("board-ok")),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "做"}])
        self.engine.start("t", "a")
        self.engine.done("t", "a")                      # 节点没写 acceptance，门由看板列派生
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "done")

    def test_column_acceptance_failure_rejects_done(self):
        self.engine.init_tree("t", "g", boards=(
            Board("dev", "开发", acceptance=always_fails()),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "做"}])
        self.engine.start("t", "a")
        with self.assertRaises(GateReject):
            self.engine.done("t", "a")

    def test_node_acceptance_wins_over_column_default(self):
        """节点自带验收优先：列默认必失败，节点自带必通过 ⇒ 应通过。"""
        self.engine.init_tree("t", "g", boards=(
            Board("dev", "开发", acceptance=always_fails()),))
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "做", "acceptance": acceptance("node-ok")}])
        self.engine.start("t", "a")
        self.engine.done("t", "a")
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "done")

    def test_without_any_acceptance_done_still_rejects(self):
        """回归基线：列与节点都无验收 ⇒ 仍是「缺 acceptance.cmd」拒收（FR-31 不放水）。"""
        self.engine.init_tree("t", "g", boards=(Board("dev", "开发"),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "做"}])
        self.engine.start("t", "a")
        with self.assertRaises(GateReject):
            self.engine.done("t", "a")

    def test_policy_survives_events_and_is_used_after_reload(self):
        """策略随事件流持久化：换 Engine 实例（重放投影）后仍由列验收派生。"""
        self.engine.init_tree("t", "g", boards=(
            Board("dev", "开发", acceptance=acceptance("board-ok")),))
        self.engine.expand("t", "root", [{"id": "a", "goal": "做"}])
        self.engine.start("t", "a")
        fresh = Engine(cwd=self.tmp)                    # 重新读投影
        fresh.done("t", "a")
        self.assertEqual(fresh.tree("t")["nodes"]["a"]["status"], "done")


if __name__ == "__main__":
    unittest.main()
