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

from disflow import vocab, store
from disflow.cli import main

# ── 回归基线：改动前（本节点起点 HEAD）在「无任何节点在跑」时抓取的 `drive --json` 输出 ──
#   逐字节固化在此，用于判据「无在跑 ⇒ 输出与基线一致」（design.md 对 drive 的「两次同输入
#   同输出」承诺）。带末尾换行，与 `_dump` 的 `print(json.dumps(..., indent=2))` 同形。
_BASELINE_DRIVE_NO_RUNNING = """\
{
  "tree": "t",
  "goal": "只读测试",
  "root": "root",
  "live": [
    "a",
    "b"
  ],
  "running": [],
  "ready_wave": [
    "a"
  ],
  "waves": [
    [
      "a"
    ]
  ],
  "blocked": {
    "b": [
      "a"
    ]
  },
  "stuck": false,
  "progress": {
    "tasks": 2,
    "done": 0,
    "pct": 0
  }
}
"""


class CliReadOnlyVerbTest(DisflowTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(vocab.STORAGE)))

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
                     ["board", "t", "--json"],
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

    # ── 写面占用：在跑节点的 touch_plan 算已占用（不许派出与在跑者抢同一文件的节点）──

    def _seed_two_writers(self, running_touch: str, other_touch: str) -> None:
        """两个就绪节点 runner / clash，写面可配 —— 用于「在跑占用」判据。"""
        self._run(["init", "t", "--goal", "在跑占用"])
        acc = json.dumps(acceptance(), ensure_ascii=False)
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"runner","goal":"在跑","stage":"dev","touch_plan":['
                   + json.dumps(running_touch) + '],"acceptance":' + acc + '},'
                   '{"id":"clash","goal":"候选","stage":"dev","touch_plan":['
                   + json.dumps(other_touch) + '],"acceptance":' + acc + '}]'])

    def test_drive_defers_clash_with_running_writer_and_reports_why(self):
        """与在跑节点写面重叠 ⇒ 不进 ready_wave，但**必须报因**（绝不静默丢）。"""
        self._seed_two_writers("disflow/engine.py", "disflow/engine.py")
        code, _out, err = self._run(["start", "t", "runner",
                                     "--holder", "host", "--ttl", "3600"])
        self.assertEqual(code, 0, err)
        _code, out, _err = self._run(["drive", "t", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["ready_wave"], [], "重叠候选不许进就绪首波")
        self.assertEqual(payload["waves"], [])
        self.assertEqual([entry["node"] for entry in payload["deferred"]], ["clash"])
        why = payload["deferred"][0]["why"]
        self.assertIn("runner", why, "理由必须点名在跑的谁")
        self.assertIn("disflow/engine.py", why, "理由必须点名重叠哪条路径")
        # 人读态同样要报（两条路都要有）
        _code, human, _err = self._run(["drive", "t"])
        self.assertIn("clash", human)
        self.assertIn("runner", human)

    def test_drive_keeps_non_overlapping_candidate_in_ready_wave(self):
        """不重叠 ⇒ 并行照旧：在跑节点不误伤无关候选（也不冒 deferred 键）。"""
        self._seed_two_writers("src/a.py", "src/b.py")
        self._run(["start", "t", "runner", "--holder", "host", "--ttl", "3600"])
        _code, out, _err = self._run(["drive", "t", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["ready_wave"], ["clash"])
        self.assertNotIn("deferred", payload)

    def test_drive_no_running_matches_baseline_byte_for_byte(self):
        """回归判据：无任何节点在跑时，`drive --json` 必须与改动前基线**逐字节一致**。"""
        self._seed()
        code, out, _err = self._run(["drive", "t", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(out, _BASELINE_DRIVE_NO_RUNNING,
                         "无在跑节点时输出必须与基线逐字节一致（无新闻 = 不加键）")
        self.assertNotIn("deferred", json.loads(out))

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
        self.assertNotIn("[dev]", only, "--stage 过滤不许泄漏别的列")

    def test_board_shows_all_four_flow_columns_even_when_empty(self):
        """四列都在（含空列）；`done` 不是列。默认**空列不铺版面**（干净），
        但事实不丢 —— 表头「列」摘要照报四列；`--all-columns` 把空列也铺成列头。"""
        self._seed()
        code, out, _err = self._run(["board", "t"])
        self.assertEqual(code, 0)
        summary = next(line for line in out.splitlines() if line.startswith("列 "))
        self.assertEqual(summary, "列 req 0/0 · design 0/0 · dev 0/1 · verify 0/1",
                         "空列的事实必须看得到（只是不占版面）")
        self.assertNotIn("[design]", out, "为空且无正文的列默认不铺")
        code2, out2, _err2 = self._run(["board", "t", "--all-columns"])
        self.assertEqual(code2, 0)
        headers = [line for line in out2.splitlines() if line.startswith("[")]
        self.assertEqual(headers, ["[req] 0/0", "[design] 0/0", "[dev] 0/1", "[verify] 0/1"])

    def test_board_columns_come_from_the_trees_boards(self):
        """列定义**单一来源** = **该树**的板集（`tree.init` 时从配置播种进事件流）。

        逐树覆盖（2026-10-11）：板定义随树走，进程级 `boards.json` 只是**建树时的播种值**。
        """
        # 用 `$DISFLOW_BOARDS` 指到临时文件：CLI 的 cwd 是仓根（不是 self.tmp），
        # 写 `<cwd>/boards.json` 会污染仓库。板定义已从 `disflow.json` 的 `stages` 搬走。
        path = os.path.join(self.tmp, "board-config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"boards": ["todo", "doing"]}, handle)
        os.environ["DISFLOW_BOARDS"] = path
        self.addCleanup(os.environ.pop, "DISFLOW_BOARDS", None)
        self._run(["init", "t", "--goal", "播种板"])
        code, out, _err = self._run(["board", "t"])
        self.assertEqual(code, 0)
        summary = next(line for line in out.splitlines() if line.startswith("列 "))
        self.assertTrue(summary.startswith("列 todo 0/0 · doing 0/0"),
                        f"列定义/顺序跟着**该树**的板集走：{summary!r}")
        code2, out2, _err2 = self._run(["board", "t", "--all-columns"])
        self.assertEqual(code2, 0)
        headers = [line for line in out2.splitlines() if line.startswith("[")]
        self.assertEqual(headers[:2], ["[todo] 0/0", "[doing] 0/0"])

    def test_board_columns_ignore_a_later_config_change(self):
        """改 `boards.json` **不**追溯改变旧树的列 —— 板是事件里的事实（可重放）。

        这正是逐树看板进事件流的理由：若列由**进程级配置**决定，同一棵树在不同
        机器/时刻会渲染出不同的列 ⇒ 事件流不可重放。
        """
        self._seed()                                  # 内建四板建树
        path = os.path.join(self.tmp, "board-config.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"boards": ["todo", "doing"]}, handle)
        os.environ["DISFLOW_BOARDS"] = path
        self.addCleanup(os.environ.pop, "DISFLOW_BOARDS", None)
        _code, out, _err = self._run(["board", "t"])
        summary = next(line for line in out.splitlines() if line.startswith("列 "))
        self.assertTrue(summary.startswith("列 req 0/0 · design 0/0 · dev 0/1 · verify 0/1"),
                        f"旧树读数不该被配置改动改掉：{summary!r}")

    def test_board_empty_legal_column_prints_zero_and_exits_zero(self):
        """合法但**为空**的列：打 `0/0`、退出码 0（空列是事实，不是错误）。"""
        self._seed()
        code, out, _err = self._run(["board", "t", "--stage", "design"])
        self.assertEqual(code, 0)
        self.assertIn("[design] 0/0", out.splitlines(), "显式选列：空列也照打 0/0")

    def test_board_rejects_an_illegal_stage_column(self):
        """非法列名**响亮拒收**（2 = 用法错误，与 argparse 同款）：放行会打出 `[bogus] 0/0`，
        读者会当成「这一列是空的」，实际是**拼错了**。"""
        self._seed()
        code, out, err = self._run(["board", "t", "--stage", "bogus"])
        self.assertEqual(code, 2, "非法列名 = 用法错误")
        self.assertNotIn("[bogus]", out, "拒收不许打出假列")
        self.assertIn("bogus", err)
        for allowed in ("req", "design", "dev", "verify"):
            self.assertIn(allowed, err, "拒收报文必须报出允许的列")

    def test_board_req_column_shows_the_tree_goal(self):
        """`[req]` 列显示**树自身的目标**（root 容器的 `goal`）——它是需求，不是任务行。"""
        self._seed()
        code, out, _err = self._run(["board", "t", "--stage", "req"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("[req] 0/0", lines, "容器不计入任务计数（它不是该列的任务行）")
        self.assertTrue(lines[0].startswith("══ 看板 t ══"), "表头第一行 = 看板名 + 树目标")
        self.assertTrue(any("◦ root" in line for line in lines), "root 以结构行出现")
        self.assertIn("只读测试", out, "[req] 列必须打出树自身的目标")

    def test_board_json_is_the_same_layout_as_the_human_view(self):
        """`--json` 与人读态**同源**（同一份 `board_layout`），且必须带**层级**（父子/深度）。"""
        self._seed()
        code, out, _err = self._run(["board", "t", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["tree"], "t")
        self.assertEqual([col["stage"] for col in payload["columns"]],
                         ["req", "design", "dev", "verify"], "列 = 配置顺序（含空列）")
        rows = [row for col in payload["columns"] for group in col["groups"]
                for row in group["rows"]]
        by_id = {row["node"]: row for row in rows}
        self.assertEqual(by_id["a"]["parent"], "root")
        self.assertEqual(by_id["a"]["depth"], 0)
        for row in rows:
            self.assertIn("parent", row, "运行视图要据此分层：每行必备 parent")
            self.assertIn("depth", row, "运行视图要据此缩进：每行必备 depth")
        # 同源：人读态**显式展开**（--all --all-columns）后，列头与组标签就是这份 layout 的渲染
        # （顺序、文本逐字对得上）；默认折叠是**渲染开关**，不是另一份布局。
        code2, human, _err = self._run(["board", "t", "--all", "--all-columns"])
        self.assertEqual(code2, 0)
        self.assertEqual([line for line in human.splitlines() if line.startswith("[")],
                         [col["header"] for col in payload["columns"]])
        labels = [group["label"] for col in payload["columns"] for group in col["groups"]]
        self.assertEqual([line for line in human.splitlines() if line in labels], labels)

    def test_board_is_read_only_byte_for_byte(self):
        """纯读：跑 `board`（含过滤/非法列/json）前后，`events.jsonl` 与 `state.json`
        必须**逐字节不变**（比只数事件条数更强：投影被改写也算破）。"""
        self._seed()
        paths = [store.events_path(vocab.STORAGE), store.projection_path(vocab.STORAGE)]

        def snapshot() -> list[bytes]:
            out: list[bytes] = []
            for path in paths:
                with open(path, "rb") as handle:
                    out.append(handle.read())
            return out

        before = snapshot()
        for argv in (["board", "t"], ["board", "t", "--stage", "dev"],
                     ["board", "t", "--stage", "design"], ["board", "t", "--json"],
                     ["board", "t", "--stage", "bogus"]):
            self._run(argv)
        self.assertEqual(snapshot(), before, "board 是纯读：两个文件必须逐字节不变")

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
        path = store.projection_path(vocab.STORAGE)
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
        path = store.projection_path(vocab.STORAGE)          # 造一个真故障：投影漂移
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
        # 宿主循环契约：done 前先 collect 落盘（collect 同样不要求未过期租约）
        code, out, err = self._run(["collect", "t", "a", "--result",
                                    '{"status":"ok","summary":"s","changed_files":[]}'])
        self.assertEqual(code, 0, f"collect 也不该被租约拦住：{out}{err}")
        code, out, err = self._run(["done", "t", "a", "--decl", '{"approver":"me"}'])
        self.assertEqual(code, 0, f"租约过期不该拦住 done：{out}{err}")
        self.assertIn("done", out)

    def test_doctor_names_the_remedy_for_projection_drift(self):
        """报故障必须带修法：漂移时要点名 `rebuild`，否则用户只知道“坏了”。"""
        self._seed()
        path = store.projection_path(vocab.STORAGE)
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
