"""`disflow host-next`：把宿主一轮的六步压成一次只读调用。

对应 brief 的 1–6 条，逐条钉住：

1. **机器可读 JSON + 人类可读态**（沿用本仓 `--json` 约定）；
2. JSON 含**就绪波次** + 每个就绪节点的**完整 brief** + **收尾命令**（`start`/`collect`/`done`）；
3. **纯读**：跑完前后 `events.jsonl` / `state.json` **逐字节不变**（并有**反向对照**证明这把尺子
   真的有效——写动词必须改变事件字节）；
4. **不新增常驻进程**：用「状态目录文件集合不变」做代理判据（本动词不建锁、不起子进程常驻）；
5. **无就绪节点时不静默**：显式报 `complete`（全部完成）/ `stuck`（卡住）/ `waiting`（就绪
   候选全被在跑节点的写面挡下），另附 `running` / `failed` 两种诚实读数；
6. **写面被在跑节点占用 ⇒ 绝不静默丢**：被挡下的候选不进 `plan`，但**与 `drive` 同源**地出现在
   `deferred` 里（点名在跑者 + 重叠路径）；人读态同样报（全被挡 = `state_message`；混合场景
   另打一行 deferred，不重复），无在跑 ⇒ 输出与改动前**逐字节一致**（有基线钉住）。
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

# ── 回归基线：改动前（本节点起点 HEAD）在「无任何节点在跑」时抓取的 `host-next --json` 输出 ──
#   与 `tests/test_cli_readonly.py` 的 `_BASELINE_DRIVE_NO_RUNNING` 同法：逐字节固化，判据是
#   「无在跑 ⇒ 输出与基线一致」（无新闻 = 不加键）。此处用可移植的验收命令 `true`（不含解释器
#   绝对路径）⇒ 基线本身机器无关。`deferred` **仅非空时出现**，故不列进 `_TOP_KEYS`。
_BASELINE_HOST_NEXT_NO_RUNNING = r"""{
  "tree": "t",
  "goal": "宿主下一步",
  "root": "root",
  "state": "ready",
  "state_message": "就绪 1 个（1 波，首波 1 个）：下面每个节点的 brief 即 worker 的 task",
  "progress": {
    "tasks": 1,
    "done": 0,
    "pct": 0
  },
  "running": [],
  "waves": [
    [
      "a"
    ]
  ],
  "ready_wave": [
    "a"
  ],
  "blocked": {},
  "stuck": false,
  "plan": [
    {
      "node": "a",
      "wave": 1,
      "stage": "dev",
      "role": "dev",
      "goal": "A",
      "brief": {
        "id": "a",
        "goal": "A",
        "kind": "task",
        "stage": "dev",
        "role": "dev",
        "entry": "",
        "context": {},
        "from_requires": [],
        "relates": [],
        "consumes": [],
        "acceptance": {
          "cmd": "true",
          "expect": "exit0"
        },
        "touch_plan": [
          "src/a.py"
        ],
        "tree": "t",
        "question_policy": {
          "ask_host": [
            "改 touch_plan / 验收标准 / goal",
            "跨节点的接口约定",
            "与 goal 相冲的取舍"
          ],
          "decide_alone": [
            "实现细节",
            "命名",
            "内部结构"
          ],
          "how": "要问就问：把问题写进 `collect --result` 的 summary 并把 status 置为 'blocked'，然后停。不要自己拍板改范围——宿主读到 blocked 后会决定（rescope / unrequire / 改 goal），再由你继续。status 里没有「我猜了一下」这个选项。"
        },
        "result_contract": {
          "node": "a",
          "required": [
            "status",
            "summary",
            "changed_files"
          ],
          "status_in": [
            "ok",
            "failed",
            "blocked"
          ],
          "question_channel": "有问题就把问题写进 summary 且 status 置为 \"blocked\"（然后停）；不要自己拍板改范围——宿主会读 blocked 并决定",
          "acceptance": {
            "cmd": "true",
            "expect": "exit0"
          }
        },
        "brief_cmd": "disflow brief t a",
        "done_cmd": "disflow done t a --decl '{\"evidence\": …}'",
        "worktree": null,
        "question_protocol": "worker 有疑问时**两条都要做**：① 走宿主通道提问（pi 里即 contact_supervisor，reason=\"need_decision\"）——问题才会到宿主手里；② **同时**在 `collect --result` 里把问题写进 summary 且 `status` 置 \"blocked\"——disflow 事件流才留得下痕（缺②则树上查不到这次提问）。两条都不做 = 自己拍板改范围，禁止。"
      },
      "start_cmd": "disflow start t a --holder <HOST>",
      "collect_cmd": "disflow collect t a --result '{\"status\": \"ok\", \"summary\": \"<一句话>\", \"changed_files\": [\"<路径>\"]}'",
      "done_cmd": "disflow done t a --decl '{\"evidence\": …}'"
    }
  ]
}
"""


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

    def _seed_runner_and_overlapping_candidate(self, with_bystander: bool = False) -> None:
        """在跑节点 `runner`（写面 `src/x.py`）+ 写面重叠的就绪候选 `clash`。

        `runner` 先 `start` ⇒ 租约在手 = 写面已占用；`clash` 与它重叠 ⇒ 该被 `deferred` 挡下。
        `with_bystander=True` 再加一个**不重叠**的就绪候选 `ok`（混合场景：`ok` 该被派，
        `clash` 该进 `deferred`——这就是「被挡节点从 plan/waves 里消失且无人点名」那一格）。
        """
        self._run(["init", "t", "--goal", "写面占用"])
        children = [
            {"id": "runner", "goal": "在跑", "stage": "dev",
             "touch_plan": ["src/x.py"], "acceptance": acceptance()},
            {"id": "clash", "goal": "候选", "stage": "dev",
             "touch_plan": ["src/x.py"], "acceptance": acceptance()},
        ]
        if with_bystander:
            children.append({"id": "ok", "goal": "无关", "stage": "dev",
                             "touch_plan": ["src/ok.py"], "acceptance": acceptance()})
        self._run(["expand", "t", "root", "--children",
                   json.dumps(children, ensure_ascii=False)])
        code, _out, err = self._run(["start", "t", "runner",
                                     "--holder", "host", "--ttl", "3600"])
        self.assertEqual(code, 0, err)

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

    # ── ⑥ 写面被在跑节点占用：deferred 绝不静默丢 + state 诚实 ──────────

    def test_mixed_reports_deferred_with_culprit_and_paths(self):
        """混合场景：`ok` 该派、`clash` 被 `runner` 挡下 —— `clash` 不许从输出里消失。"""
        self._seed_runner_and_overlapping_candidate(with_bystander=True)
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        # 就绪的 `ok` 照派；被挡的 `clash` 不进 plan/waves，但带因进 `deferred`
        self.assertEqual(payload["state"], "ready")
        self.assertEqual(payload["waves"], [["ok"]])
        self.assertEqual([entry["node"] for entry in payload["plan"]], ["ok"])
        self.assertEqual([entry["node"] for entry in payload["deferred"]], ["clash"])
        why = payload["deferred"][0]["why"]
        self.assertIn("runner", why, "理由必须点名在跑的谁")
        self.assertIn("src/x.py", why, "理由必须点名重叠哪条路径")
        # **与 drive 同源**：两个动词的 `deferred` 是同一份（不得各算一遍）
        _code, drive_out, _err = self._run(["drive", "t", "--json"])
        self.assertEqual(payload["deferred"], json.loads(drive_out)["deferred"])
        # 人读态（`state_message`）也点名在跑者与重叠路径
        for token in ("clash", "runner", "src/x.py"):
            self.assertIn(token, payload["state_message"])

    def test_all_ready_candidates_deferred_state_is_waiting_not_ready(self):
        """就绪候选**全**被在跑者挡下 ⇒ `state` 不许报 `ready`（ready + 空 plan 自相矛盾）。"""
        self._seed_runner_and_overlapping_candidate()
        _code, out, _err = self._host_next("--json")
        payload = json.loads(out)
        self.assertEqual(payload["plan"], [])
        self.assertNotEqual(payload["state"], "ready",
                            "ready_wave 因 defer 为空时不许报 ready")
        self.assertEqual(payload["state"], "waiting")
        self.assertNotEqual(payload["state"], "stuck",
                            "waiting 与 stuck 是两种病因，不许合并")
        self.assertFalse(payload["stuck"])
        self.assertEqual([entry["node"] for entry in payload["deferred"]], ["clash"])
        for token in ("clash", "runner", "src/x.py"):
            self.assertIn(token, payload["state_message"])
        # 人读态（无 plan 时 CLI 打印 `state_message`）也逐条点名
        _code, human, _err = self._host_next()
        for token in ("clash", "runner", "src/x.py"):
            self.assertIn(token, human)
        # **不重复打印**：全被挡那格由 `state_message` 说，人读输出不得再打一行 deferred
        self.assertNotIn("写面被在跑节点占用", human)

    def test_mixed_human_output_names_deferred_and_stays_readonly(self):
        """混合场景：`ok` 可派、`clash` 被挡 —— 人读输出必须点名 `clash`（屏幕上不许无声消失）。"""
        self._seed_runner_and_overlapping_candidate(with_bystander=True)
        before_events, before_state = self._events_bytes(), self._state_bytes()
        code, human, err = self._host_next()
        self.assertEqual(code, 0, err)
        # 可派的 `ok` 照常列出
        self.assertIn("disflow start t ok", human)
        # 被挡的 `clash` 必须点名在跑者与重叠路径（不能说去就没了）
        for token in ("clash", "runner", "src/x.py"):
            self.assertIn(token, human)
        self.assertIn("写面被在跑节点占用", human)
        # 纯读：人读打印不得写事件 / 动投影（照本仓既有 byte-identical 测法）
        self.assertEqual(self._events_bytes(), before_events,
                         "host-next 人读态也不许写事件")
        self.assertEqual(self._state_bytes(), before_state,
                         "host-next 人读态也不许改投影")

    def test_no_running_payload_is_byte_identical_to_baseline(self):
        """回归判据：无任何节点在跑时，`host-next --json` 与改动前基线**逐字节一致**。

        验收命令用可移植的 `true`（基线不依赖解释器绝对路径）；种子与基线抓取时完全一致。
        """
        self._run(["init", "t", "--goal", "宿主下一步"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","stage":"dev","touch_plan":["src/a.py"],'
                   '"acceptance":{"cmd":"true","expect":"exit0"}}]'])
        code, out, err = self._host_next("--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(out, _BASELINE_HOST_NEXT_NO_RUNNING,
                         "无在跑节点时输出必须与基线逐字节一致（无新闻 = 不加键）")
        self.assertNotIn("deferred", json.loads(out),
                         "无在跑节点时 `deferred` 不许出现（否则破坏逐字节回归）")


if __name__ == "__main__":
    unittest.main()
