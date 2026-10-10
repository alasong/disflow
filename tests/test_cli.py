"""CLI 层测试：错误以一行 `✗ …` 呈现（不抛 Python 栈），退出码正确。"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase

from disflow import spec, store
from disflow.cli import main
from disflow.state import project


class CliTest(DisflowTestCase):
    def test_corrupt_events_line_reports_not_traceback(self):
        store.append_event(spec.SPEC, "tree.init", project=project, slug="t",
                           extra={"goal": "g"})
        with open(store.events_path(spec.SPEC), "a", encoding="utf-8") as handle:
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


if __name__ == "__main__":
    unittest.main()
