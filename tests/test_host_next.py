"""`disflow host-next`：把宿主一轮的六步压成一次只读调用。

对应 brief 的 1–6 条，逐条钉住：

1. **机器可读 JSON + 人类可读态**（沿用本仓 `--json` 约定）；
2. JSON 含**就绪波次** + 每个就绪节点的**完整 brief** + **收尾命令**（`start`/`collect`/`done`）；
3. **纯读**：跑完前后 `events.jsonl` / `state.json` **逐字节不变**（并有**反向对照**证明这把尺子
   真的有效——写动词必须改变事件字节）；
4. **不新增常驻进程**：用「状态目录文件集合不变」做代理判据（本动词不建锁、不起子进程常驻）；
5. **无就绪节点时不静默**：显式报 `complete`（全部完成）或 `stuck`（卡住），另附 `running` /
   `failed` 两种诚实读数。
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

# host-next JSON 的顶层键（形状即使命，防止字段悄悄消失）
_TOP_KEYS = ("tree", "goal", "root", "state", "state_message", "progress",
             "running", "waves", "ready_wave", "blocked", "stuck", "plan")
# 每个派活条目必须带的键
_ENTRY_KEYS = ("node", "wave", "stage", "role", "goal", "brief",
               "start_cmd", "collect_cmd", "done_cmd")


class HostNextTest(DisflowTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    # ── 现场读数（逐字节）────────────────────────────────────────────────

    def _read_bytes(self, path: str) -> bytes:
        if not os.path.isfile(path):
            return b""
        with open(path, "rb") as handle:
            return handle.read()

    def _events_bytes(self) -> bytes:
        return self._read_bytes(store.events_path(spec.SPEC))

    def _state_bytes(self) -> bytes:
        return self._read_bytes(store.projection_path(spec.SPEC))

    def _state_files(self) -> list[str]:
        return sorted(os.listdir(store.state_dir(spec.SPEC)))

    # ── 种子 ────────────────────────────────────────────────────────────

    def _seed_one(self) -> None:
        """一个就绪任务节点 a（写面 src/a.py）。"""
        self._run(["init", "t", "--goal", "宿主下一步"])
        acc = json.dumps(acceptance(), ensure_ascii=False)
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","stage":"dev","touch_plan":["src/a.py"],'
                   '"acceptance":' + acc + '}]'])

    def _seed_two_overlapping(self) -> None:
        """两个就绪节点、写面重叠 ⇒ 引擎把它们拆成两波（同波不重叠）。"""
        self._run(["init", "t", "--goal", "宿主下一步"])
        acc = json.dumps(acceptance(), ensure_ascii=False)
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","stage":"dev","touch_plan":["src/a.py"],'
                   '"acceptance":' + acc + '},'
                   '{"id":"c","goal":"C","stage":"dev","touch_plan":["src/a.py"],'
                   '"acceptance":' + acc + '}]'])

    def _host_next(self, *extra: str) -> tuple[int, str, str]:
        return self._run(["host-next", "t", *extra])

    # ── ① 机器可读 + 人类可读 ────────────────────────────────────────────

    def test_json_is_machine_readable_and_has_ready_state(self):
        self._seed_one()
        code, out, err = self._host_next("--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)               # 可解析 = 机器可读
        for key in _TOP_KEYS:
            self.assertIn(key, payload)
        self.assertEqual(payload["tree"], "t")
        self.assertEqual(payload["state"], "ready")
        self.assertTrue(payload["state_message"])
        self.assertEqual(payload["ready_wave"], ["a"])

    def test_human_readable_lists_node_and_finish_commands(self):
        self._seed_one()
        code, human, err = self._host_next()
        self.assertEqual(code, 0, err)
        self.assertIn("a", human)
        self.assertIn("disflow start t a", human)
        self.assertIn("disflow collect t a", human)
        self.assertIn("disflow done t a", human)

    # ── ② 就绪波次 + 完整 brief + 收尾命令 ───────────────────────────────

    def test_plan_carries_full_brief_and_finish_commands(self):
        self._seed_one()
        # 与 `disflow brief t a` 必须是**同一份**（host-next 只是多补 question_protocol）
        _code, brief_out, _err = self._run(["brief", "t", "a"])
        brief = json.loads(brief_out)
        _code, out, _err = self._host_next("--json")
        entry = json.loads(out)["plan"][0]
        self.assertEqual(entry["node"], "a")
        self.assertEqual(entry["wave"], 1)
        self.assertEqual(entry["brief"]["id"], "a")
        for key, value in brief.items():
            self.assertEqual(entry["brief"][key], value, f"brief 与 disflow brief 不同源：{key}")
        # 提问纪律：宿主通道 + collect 里标 blocked，**两条都要有**
        self.assertIn("question_protocol", entry["brief"])
        self.assertIn("contact_supervisor", entry["brief"]["question_protocol"])
        self.assertIn("blocked", entry["brief"]["question_protocol"])
        # 收尾命令完整、可直接照抄
        self.assertTrue(entry["start_cmd"].startswith("disflow start t a"))
        self.assertTrue(entry["collect_cmd"].startswith("disflow collect t a --result"))
        self.assertIn('"status": "ok"', entry["collect_cmd"])
        self.assertTrue(entry["done_cmd"].startswith("disflow done t a --decl"))

    def test_plan_covers_every_ready_node_across_waves(self):
        self._seed_two_overlapping()
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        # 写面重叠 ⇒ 拆成两波；但两个节点都就绪，brief 都要给
        self.assertEqual(payload["waves"], [["a"], ["c"]])
        self.assertEqual(payload["ready_wave"], ["a"])
        entries = {entry["node"]: entry for entry in payload["plan"]}
        self.assertEqual(sorted(entries), ["a", "c"])
        self.assertEqual(entries["a"]["wave"], 1)
        self.assertEqual(entries["c"]["wave"], 2)
        for entry in payload["plan"]:
            for key in _ENTRY_KEYS:
                self.assertIn(key, entry)

    # ── ③ 纯读：事件流 / 投影逐字节不变 ──────────────────────────────────

    def test_readonly_events_and_projection_are_byte_identical(self):
        self._seed_one()
        self._run(["start", "t", "a", "--holder", "me"])   # 先制造一条事件
        before_events, before_state, before_files = (
            self._events_bytes(), self._state_bytes(), self._state_files())
        code, _out, err = self._host_next("--json")
        self.assertEqual(code, 0, err)
        code, _out, err = self._host_next()                # 人类态同样只读
        self.assertEqual(code, 0, err)
        self.assertEqual(self._events_bytes(), before_events,
                         "host-next 不许写事件（事件流必须逐字节不变）")
        self.assertEqual(self._state_bytes(), before_state,
                         "host-next 不许改投影（state.json 必须逐字节不变）")
        # ④ 不新增常驻进程的代理判据：状态目录文件集合不变（不建锁、不落 pid）
        self.assertEqual(self._state_files(), before_files,
                         "host-next 不许新增状态目录文件")
        # **反向对照**：这把尺子有效——一个写动词必须改变事件字节（否则上面是空断言）
        self._run(["fail", "t", "a", "--reason", "对照"])
        self.assertNotEqual(self._events_bytes(), before_events,
                            "尺子失效：写动词竟没改变事件流字节")

    # ── ⑤ 无就绪：显式报 complete / stuck ────────────────────────────────

    def test_no_ready_all_done_reports_complete(self):
        self._seed_one()
        self._run(["skip", "t", "a", "--reason", "不做了"])
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["state"], "complete")
        self.assertIn("全部完成", payload["state_message"])
        _code, human, _err = self._host_next()
        self.assertIn("全部完成", human)

    def test_no_ready_empty_tree_reports_complete_not_silent(self):
        self._run(["init", "t", "--goal", "空树"])
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["state"], "complete")
        self.assertIn("无任务节点", payload["state_message"])

    def test_no_ready_stuck_reports_stuck_with_blockers(self):
        """跨树 `awaits` 未满足 ⇒ pending 却无就绪、无运行 = stuck。"""
        self._run(["init", "t", "--goal", "卡住"])
        acc = json.dumps(acceptance(), ensure_ascii=False)
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","awaits":["other"],"acceptance":' + acc + '},'
                   '{"id":"b","goal":"B","requires":["a"],"acceptance":' + acc + '}]'])
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["state"], "stuck")
        self.assertTrue(payload["stuck"])
        self.assertIn("卡住", payload["state_message"])
        self.assertEqual(sorted(payload["blocked"]), ["a", "b"])
        _code, human, _err = self._host_next()
        self.assertIn("卡住", human)

    def test_no_ready_running_reports_running(self):
        self._seed_one()
        self._run(["start", "t", "a", "--holder", "me"])
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["state"], "running")
        self.assertIn("运行中", payload["state_message"])

    def test_no_ready_failed_reports_failed_not_stuck(self):
        self._seed_one()
        self._run(["fail", "t", "a", "--reason", "炸了"])
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertEqual(payload["state"], "failed")
        self.assertFalse(payload["stuck"], "failed 不算 stuck")


if __name__ == "__main__":
    unittest.main()
