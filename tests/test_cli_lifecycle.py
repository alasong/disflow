"""CLI 装配层测试（C2）：生命周期动词 start / collect / done / fail / retry / skip / reap。

重点验证两件装配层容易漏的事（都能一路漏到生产）：
1. **旗标真的接上了引擎**（`--dry-run` / `--ttl` 曾被漏传，见 V3 在 `cmd_close` 抓到的缺陷）
2. **非法状态转移被拒**（不是每个动词都能在任何状态下调用）
"""
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance, always_fails

from disflow import spec, store
from disflow.cli import main


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class CliLifecycleVerbTest(DisflowTestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _state(self) -> dict:
        return store.read_json(store.projection_path(spec.SPEC))

    def _node(self, node: str, tree: str = "t") -> dict:
        return self._state()["trees"][tree]["nodes"][node]

    def _events(self) -> int:
        return len(store.read_jsonl(store.events_path(spec.SPEC)))

    def _one_node_tree(self, accept: dict | None = None) -> None:
        payload = _json(accept or acceptance())
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"干活","acceptance":' + payload + '}]'])

    # ── start ─────────────────────────────────────────────────────────────

    def test_start_sets_running_and_lease(self):
        self._one_node_tree()
        code, _out, _err = self._run(["start", "t", "a", "--holder", "w1"])
        self.assertEqual(code, 0)
        node = self._node("a")
        self.assertEqual(node["status"], "running")
        self.assertEqual(node["lease"]["holder"], "w1")
        self.assertIn("expires_at", node["lease"])

    def test_start_blocked_node_is_rejected(self):
        payload_a, payload_b = _json(acceptance()), _json(acceptance())
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","acceptance":' + payload_a
                   + '},{"id":"b","goal":"B","acceptance":' + payload_b + '}]'])
        self._run(["require", "t", "b", "a"])
        code, _out, err = self._run(["start", "t", "b"])
        self.assertNotEqual(code, 0, "前置未 done 时不许开工")
        self.assertIn("未就绪", err)

    def test_start_dry_run_does_not_lease_or_write(self):
        """回归守卫：`--dry-run` 必须真的接上引擎（V3 抓到过漏传 `dry_run`）。"""
        self._one_node_tree()
        before = self._events()
        code, out, _err = self._run(["start", "t", "a", "--dry-run"])
        self.assertEqual(code, 0, out)
        self.assertIn("预演", out)
        self.assertEqual(self._events(), before)
        node = self._node("a")
        self.assertEqual(node["status"], "pending")
        self.assertIsNone(node["lease"])

    # ── collect ───────────────────────────────────────────────────────────

    def test_collect_stores_result_without_done(self):
        self._one_node_tree()
        self._run(["start", "t", "a"])
        code, _out, _err = self._run(["collect", "t", "a", "--result",
                                      '{"status":"ok","summary":"做完了","changed_files":[]}'])
        self.assertEqual(code, 0)
        node = self._node("a")
        self.assertEqual(node["status"], "running", "collect 不许写 done（FR-15）")
        self.assertEqual(node["collected"]["summary"], "做完了")

    def test_collect_rejects_broken_contract(self):
        self._one_node_tree()
        self._run(["start", "t", "a"])
        code, _out, err = self._run(["collect", "t", "a", "--result", '{"summary":"缺字段"}'])
        self.assertNotEqual(code, 0, "结果契约不符必须被拒")
        self.assertIn("✗", err)

    # ── done ──────────────────────────────────────────────────────────────

    def _collect(self, node: str) -> None:
        """过一遍宿主循环契约（CLI `done` 无已落盘 collect 会拒收）。"""
        code, _out, err = self._run(["collect", "t", node, "--result",
                                     '{"status":"ok","summary":"s","changed_files":[]}'])
        self.assertEqual(code, 0, err)

    def test_done_without_acceptance_is_rejected(self):
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children", '[{"id":"a","goal":"无判据"}]'])
        self._run(["start", "t", "a"])
        self._collect("a")               # 先过宿主循环契约，才测得到门
        code, _out, err = self._run(["done", "t", "a"])
        self.assertNotEqual(code, 0, "无验收命令不许 done（FR-18）")
        self.assertIn("验收", err)

    def test_done_with_failing_acceptance_is_rejected(self):
        self._one_node_tree(always_fails())
        self._run(["start", "t", "a"])
        code, _out, err = self._run(["done", "t", "a"])
        self.assertNotEqual(code, 0)
        self.assertIn("✗", err)

    def test_done_requires_declared_fields(self):
        payload = _json(dict(acceptance(), require_declarations=["approver"]))
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"批准","acceptance":' + payload + '}]'])
        self._run(["start", "t", "a"])
        self._collect("a")               # 先过宿主循环契约，才测得到缺必填声明
        self.assertNotEqual(self._run(["done", "t", "a"])[0], 0, "缺必填声明必须被拒")
        code, _out, _err = self._run(["done", "t", "a", "--decl", '{"approver":"song"}'])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["status"], "done")

    # ── fail / retry / skip ───────────────────────────────────────────────

    def test_fail_then_retry_returns_to_pending(self):
        self._one_node_tree()
        self._run(["start", "t", "a"])
        code, _out, _err = self._run(["fail", "t", "a", "--reason", "依赖挂了"])
        self.assertEqual(code, 0)
        self.assertEqual(self._node("a")["status"], "failed")
        self.assertEqual(self._run(["retry", "t", "a"])[0], 0)
        self.assertEqual(self._node("a")["status"], "pending")
        self.assertIsNone(self._node("a")["lease"])

    def test_skip_records_reason(self):
        self._one_node_tree()
        code, _out, _err = self._run(["skip", "t", "a", "--reason", "不做了"])
        self.assertEqual(code, 0)
        node = self._node("a")
        self.assertEqual(node["status"], "skipped")
        self.assertEqual(node["skip_reason"], "不做了")

    def test_skip_unblocks_dependents(self):
        """`skip` 是终态 → 后继可开工（TERMINAL 语义，设计 §3）。"""
        payload_a, payload_b = _json(acceptance()), _json(acceptance())
        self._run(["init", "t", "--goal", "g"])
        self._run(["expand", "t", "root", "--children",
                   '[{"id":"a","goal":"A","acceptance":' + payload_a
                   + '},{"id":"b","goal":"B","acceptance":' + payload_b + '}]'])
        self._run(["require", "t", "b", "a"])
        self._run(["skip", "t", "a", "--reason", "放弃"])
        code, out, _err = self._run(["drive", "t"])
        self.assertEqual(code, 0)
        self.assertIn("b", out)

    # ── reap ──────────────────────────────────────────────────────────────

    def test_reap_reclaims_expired_lease(self):
        self._one_node_tree()
        self._run(["start", "t", "a", "--holder", "w1", "--ttl", "-1"])   # 立即过期
        self.assertEqual(self._node("a")["status"], "running")
        code, out, _err = self._run(["reap", "t"])
        self.assertEqual(code, 0)
        self.assertIn("回收", out)
        node = self._node("a")
        self.assertEqual(node["status"], "pending")
        self.assertIsNone(node["lease"])

    def test_reap_without_expired_lease_is_noop(self):
        self._one_node_tree()
        self._run(["start", "t", "a", "--holder", "w1"])   # 默认 900s，未过期
        code, out, _err = self._run(["reap", "t"])
        self.assertEqual(code, 0)
        self.assertIn("无过期", out)
        self.assertEqual(self._node("a")["status"], "running")

    # ── rescope（写面声明的当口改动，§7.2）───────────────────────────────

    def test_rescope_wires_touch_and_reason_to_the_engine(self):
        self._one_node_tree()
        self._run(["start", "t", "a", "--holder", "w1"])
        code, out, _err = self._run(["rescope", "t", "a",
                                     "--touch", '["new.py"]', "--reason", "要新建"])
        self.assertEqual(code, 0)
        self.assertIn("new.py", out)
        node = self._node("a")
        self.assertEqual(node["touch_plan"], ["new.py"])
        self.assertEqual(node["rescope_log"][0]["reason"], "要新建")

    def test_rescope_dry_run_does_not_write(self):
        self._one_node_tree()
        self._run(["start", "t", "a", "--holder", "w1"])
        before = self._events()
        code, out, _err = self._run(["rescope", "t", "a", "--touch", '["n.py"]',
                                     "--reason", "预演", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("未落事件", out)
        self.assertEqual(self._events(), before)

    def test_rescope_rejection_goes_to_stderr(self):
        """错误报文必须走 stderr（否则脚本 2>/dev/null 会把拒收看成功）。"""
        self._one_node_tree()
        self._run(["start", "t", "a", "--holder", "w1"])
        before = self._events()
        code, out, err = self._run(["rescope", "t", "a", "--touch", '["x.py"]',
                                    "--reason", "   "])
        self.assertNotEqual(code, 0)
        self.assertIn("✗", err)
        self.assertEqual(out, "")
        self.assertEqual(self._events(), before)          # 拒收不得落事件


if __name__ == "__main__":
    unittest.main()
