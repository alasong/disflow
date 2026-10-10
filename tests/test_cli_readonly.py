"""CLI 装配层测试（C3）：只读动词 drive / brief / plan / status / board / tree / graph / trees /
rebuild / doctor。

两条核心判据：
1. **只读就是只读** —— 这 10 个动词跑一遍，`events.jsonl` 条数必须**一条不变**（比逐个断言状态更强）。
2. **输出可被程序消费** —— `--json` / `--dot` 的产物必须能解析，不能是给人看的糊字。
"""
from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance

from disflow import spec, store
from disflow.cli import main


class CliReadOnlyVerbTest(DisflowTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(spec.SPEC)))

    def _seed(self) -> None:
        self._run(["init", "t", "--goal", "只读测试"])
        acc = json.dumps(acceptance(), ensure_ascii=False)
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","stage":"dev","touch_plan":["src/a.py"],'
                   '"acceptance":' + acc + '},'
                   '{"id":"b","goal":"B","stage":"verify","role":"qa",'
                   '"acceptance":' + acc + '}]'])
        self._run(["require", "t", "b", "a"])
        self._run(["relate", "t", "b", "a"])

    # ── 只读性（强判据）─────────────────────────────────────────────────

    def test_all_readonly_verbs_write_zero_events(self):
        self._seed()
        before = self._events()
        for argv in (["drive", "t"], ["drive", "t", "--json"], ["drive", "t", "--dry-run"],
                     ["brief", "t", "a"], ["plan", "t"], ["status", "t"],
                     ["status", "t", "--json"], ["status", "--json"],
                     ["board", "t"], ["board", "t", "--stage", "dev"],
                     ["tree", "t"], ["graph", "t"], ["graph", "t", "--dot"],
                     ["trees"], ["rebuild", "--check"], ["doctor"]):
            code, _out, err = self._run(argv)
            self.assertEqual(code, 0, f"{argv} 应成功，stderr={err!r}")
        self.assertEqual(self._events(), before, "只读动词不许写事件")

    # ── 输出可机器消费 ───────────────────────────────────────────────────

    def test_drive_json_has_expected_keys(self):
        self._seed()
        code, out, _err = self._run(["drive", "t", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        for key in ("tree", "goal", "live", "running", "ready_wave", "waves",
                    "blocked", "stuck", "progress"):
            self.assertIn(key, payload)
        self.assertEqual(payload["ready_wave"], ["a"])
        self.assertEqual(payload["blocked"]["b"], ["a"])

    def test_drive_dry_run_marks_payload(self):
        self._seed()
        _code, out, _err = self._run(["drive", "t", "--dry-run", "--json"])
        self.assertTrue(json.loads(out)["dry_run"])

    def test_brief_is_a_complete_contract(self):
        self._seed()
        code, out, _err = self._run(["brief", "t", "a"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        for key in ("id", "goal", "kind", "stage", "role", "acceptance",
                    "touch_plan", "tree", "brief_cmd", "done_cmd"):
            self.assertIn(key, payload)
        self.assertEqual(payload["id"], "a")
        self.assertIn("disflow brief t a", payload["brief_cmd"])

    def test_plan_lists_brief_commands_for_ready_nodes(self):
        self._seed()
        code, out, _err = self._run(["plan", "t"])
        self.assertEqual(code, 0)
        self.assertIn("disflow brief t a", out)

    def test_status_prints_summary_and_json_has_nodes(self):
        """`status <tree>` 是**摘要视图**（逐节点明细在 board/tree 与 --json 里）。"""
        self._seed()
        code, out, _err = self._run(["status", "t"])
        self.assertEqual(code, 0)
        self.assertIn("进度 0/2", out)
        self.assertIn("就绪 1", out)
        _code, payload, _err = self._run(["status", "t", "--json"])
        status = json.loads(payload)
        self.assertEqual(sorted(status["nodes"]), ["a", "b", "root"])

    def test_status_json_is_parseable(self):
        self._seed()
        _code, out, _err = self._run(["status", "t", "--json"])
        payload = json.loads(out)
        self.assertIn("nodes", payload)

    def test_board_groups_by_stage_and_filters(self):
        self._seed()
        code, out, _err = self._run(["board", "t"])
        self.assertEqual(code, 0)
        self.assertIn("dev", out)
        _code, only, _err = self._run(["board", "t", "--stage", "verify"])
        self.assertIn("B", only)
        self.assertNotIn("A [", only)   # dev 列的 A 不该出现

    def test_tree_renders_hierarchy(self):
        self._seed()
        code, out, _err = self._run(["tree", "t"])
        self.assertEqual(code, 0)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_graph_shows_edges_and_dot_is_valid(self):
        self._seed()
        code, out, _err = self._run(["graph", "t"])
        self.assertEqual(code, 0)
        self.assertIn("b", out)
        _code, dot, _err = self._run(["graph", "t", "--dot"])
        self.assertIn("digraph", dot)
        self.assertTrue(dot.strip().endswith("}"), "DOT 必须以 } 收尾")

    def test_trees_lists_all_trees(self):
        self._seed()
        self._run(["init", "u", "--goal", "第二棵"])
        code, out, _err = self._run(["trees"])
        self.assertEqual(code, 0)
        self.assertIn("t", out)
        self.assertIn("u", out)

    # ── 自检 ─────────────────────────────────────────────────────────────

    def test_rebuild_check_and_doctor_are_green_on_clean_state(self):
        self._seed()
        self.assertEqual(self._run(["rebuild", "--check"])[0], 0)
        self.assertEqual(self._run(["doctor"])[0], 0)

    def test_doctor_detects_dangling_requires(self):
        """自检必须真有牙：手工损坏投影后 doctor 应报错。"""
        self._seed()
        path = store.projection_path(spec.SPEC)
        state = store.read_json(path)
        state["trees"]["t"]["nodes"]["b"]["requires"] = ["不存在的前置"]
        store.write_json_atomic(path, state)
        code, _out, err = self._run(["doctor"])
        self.assertNotEqual(code, 0, "悬空 requires 必须被 doctor 抓到")
        self.assertIn("✗", err)

    # ── doctor：把“故障”与“需人判断”分开（租约人速假红）──────────────
    #   背景：lease_ttl 默认 900s。人（不是 agent）在一个节点上干超过 15 分钟，
    #   旧行为是 doctor **变红**并把 `reap` 当成唯一修法——而那会把节点从干活的人
    #   手里收回。doctor 无法知道是“worker 崩了”还是“人在干活”，所以不能假装知道。

    def _start_with_expired_lease(self) -> None:
        """起一个节点并让租约**已经过期**——用 `--ttl=-1`（立即过期），
        与本仓既有做法一致（`tests/test_lease.py`），不靠 `sleep`：
        既快又不依赖墙钟（sleep 会造成慢+偶发飘）。"""
        code, out, err = self._run(["start", "t", "a", "--holder", "me", "--ttl=-1"])
        self.assertEqual(code, 0, f"start 失败：{out}{err}")

    def test_expired_lease_is_an_advisory_not_a_failure(self):
        self._seed()
        self._start_with_expired_lease()
        code, out, _err = self._run(["doctor"])
        self.assertEqual(code, 0, "租约过期不该让 doctor 退出码非零（人速干活是正常的）")
        self.assertIn("需人判断", out)
        self.assertIn("租约已过期", out)
        self.assertIn("holder=me", out, "要报出是谁在干")
        self.assertIn("不是故障", out)
        self.assertIn("✓ doctor 全绿", out)

    def test_advisory_states_the_fact_and_both_readings(self):
        """报的必须是**事实 + 两种读法各自的正确做法**，而不是一个断定的病因。"""
        self._seed()
        self._start_with_expired_lease()
        _code, out, _err = self._run(["doctor"])
        self.assertIn("已过期", out)                      # 事实：过期多久（人能据此判断）
        self.assertRegex(out, r"已过期 \d+ 秒")           # 具体读数，不是“已过期”三个字
        self.assertIn("worker 已崩", out)                 # 读法 ①
        self.assertIn("reap", out)                        # ① 的做法
        self.assertIn("人还在干", out)                    # 读法 ②
        self.assertIn("done 不要求租约", out)             # ② 的做法（且说明为何安全）
        self.assertIn("lease_ttl", out)                   # 想避开这条怎么办

    def test_advisory_does_not_hide_a_real_problem(self):
        """两者的错不能互相掩盖：同时存在时，真故障仍然退出码 1、stdout 干净。"""
        self._seed()
        self._start_with_expired_lease()
        path = store.projection_path(spec.SPEC)          # 造一个真故障：投影漂移
        state = store.read_json(path)
        state["trees"]["t"]["nodes"]["b"]["status"] = "done"
        store.write_json_atomic(path, state)
        code, out, err = self._run(["doctor"])
        self.assertEqual(code, 1, "有真故障就必须非零")
        self.assertIn("投影漂移", err)
        self.assertIn("rebuild", err)
        self.assertEqual(out, "", "失败时 stdout 必须干净（既有契约）")

    def test_no_advisory_when_no_lease_has_expired(self):
        self._seed()
        code, out, _err = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertNotIn("需人判断", out, "没有过期租约就不该出现这一节")

    def test_running_with_a_live_lease_is_silent(self):
        self._seed()
        self._run(["start", "t", "a", "--holder", "me", "--ttl", "3600"])
        code, out, _err = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertNotIn("需人判断", out)

    def test_done_does_not_require_a_lease(self):
        """把“意外行为”钉成**有意的契约**：`done` 不要求 `running` 也不要求未过期租约。

        为何这是对的：租约防的是**并发**（两个人同时干同一节点），不是“收工晚了”。
        所以租约过期不该阻止干活的人交活。
        """
        self._seed()
        self._start_with_expired_lease()
        os.makedirs(os.path.join(self.tmp, "src"), exist_ok=True)
        with open(os.path.join(self.tmp, "src", "a.py"), "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        code, out, err = self._run(["done", "t", "a", "--decl", '{"approver":"me"}'])
        self.assertEqual(code, 0, f"租约过期不该拦住 done：{out}{err}")
        self.assertIn("done", out)

    def test_doctor_names_the_remedy_for_projection_drift(self):
        """报故障必须带修法：漂移时要点名 `rebuild`，否则用户只知道“坏了”。"""
        self._seed()
        path = store.projection_path(spec.SPEC)
        state = store.read_json(path)
        state["trees"]["t"]["nodes"]["b"]["status"] = "done"      # 与事件不符 ⇒ 漂移
        store.write_json_atomic(path, state)
        code, out, err = self._run(["doctor"])
        self.assertNotEqual(code, 0)
        self.assertIn("投影漂移", err)
        self.assertIn("rebuild", err, "故障报文必须给出修法")
        # 报的修法必须真能修：跑完 rebuild 后 doctor 应该转绿
        self.assertEqual(self._run(["rebuild"])[0], 0)
        self.assertEqual(self._run(["doctor"])[0], 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
