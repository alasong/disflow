"""CLI 层测试：错误以一行 `✗ …` 呈现（不抛 Python 栈），退出码正确。"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import vocab, store
from disflow.cli import main
from disflow.state import project


class CliTest(DisflowTestCase):
    def test_corrupt_events_line_reports_not_traceback(self):
        store.append_event(vocab.STORAGE, "tree.init", project=project, slug="t",
                           extra={"goal": "g"})
        with open(store.events_path(vocab.STORAGE), "a", encoding="utf-8") as handle:
            handle.write("{this is not json}\n")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["rebuild", "--check"])
        self.assertEqual(code, 1)
        self.assertIn("✗", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_missing_tree_reports_error(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["status", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("✗", stderr.getvalue())

    def test_bad_json_argument_reports_error(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            main(["init", "t", "--goal", "g"])
            code = main(["expand", "t", "root", "--children", "{not json}"])
        self.assertEqual(code, 1)
        self.assertIn("✗", stderr.getvalue())

    # ── CLI 层预演：旗标必须真的接上引擎（曾被漏传）──────────────

    def _rows(self) -> int:
        return len(store.read_jsonl(store.events_path(vocab.STORAGE)))

    def _state(self) -> dict:
        return store.read_json(store.projection_path(vocab.STORAGE))

    def test_close_dry_run_via_cli_writes_no_event(self):
        """回归：`cmd_close` 曾漏传 `dry_run`，导致 `--dry-run` 真关树。"""
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            main(["init", "t", "--goal", "g"])
            main(["context", "t", "root", "--set", '{"closure": "not_applicable: 测试用"}'])
            before = self._rows()
            code = main(["close", "t", "--dry-run"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self._rows(), before, "CLI 预演不许落事件")
        self.assertFalse(self._state()["trees"]["t"].get("closed"), "CLI 预演不许真关树")
        self.assertIn("预演通过", out.getvalue())

    def test_done_dry_run_via_cli_writes_no_event(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            main(["init", "t", "--goal", "g"])
            main(["expand", "t", "root", "--children",
                  '[{"id":"a","goal":"干活","acceptance":{"cmd":"true","expect":"exit0"}}]'])
            main(["start", "t", "a"])
            main(["collect", "t", "a", "--result",
                  '{"status":"ok","summary":"s","changed_files":[]}'])
            before = self._rows()
            code = main(["done", "t", "a", "--dry-run"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self._rows(), before, "CLI 预演不许落事件")
        self.assertEqual(self._state()["trees"]["t"]["nodes"]["a"]["status"], "running")

    def test_start_dry_run_via_cli_writes_no_event(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            main(["init", "t", "--goal", "g"])
            main(["expand", "t", "root", "--children",
                  '[{"id":"a","goal":"干活","acceptance":{"cmd":"true","expect":"exit0"}}]'])
            before = self._rows()
            code = main(["start", "t", "a", "--dry-run"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(self._rows(), before)
        self.assertIsNone(self._state()["trees"]["t"]["nodes"]["a"]["lease"])


if __name__ == "__main__":
    unittest.main()
