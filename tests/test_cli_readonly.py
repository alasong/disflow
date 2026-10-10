"""CLI 装配层测试（C3）：只读动词 drive / brief / plan / status / board / tree / graph / trees /
rebuild / doctor。

两条核心判据：
1. **只读就是只读** —— 这 10 个动词跑一遍，`events.jsonl` 条数必须**一条不变**（比逐个断言状态更强）。
2. **输出可被程序消费** —— `--json` / `--dot` 的产物必须能解析，不能是给人看的糊字。
"""
from __future__ import annotations

import io
import json
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


if __name__ == "__main__":
    unittest.main()
