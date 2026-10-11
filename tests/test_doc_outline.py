"""文档章节定位器守卫：① 切节边界正确；② 命中/未命中行为；③ `docs/outline.jsonl`
与文档**同步**（漂移即红，与 `docs/INDEX.md` 同一套「不登记=读不到」纪律）。

**为什么要有它**：`scripts/doc_outline.py` 是「读文档先看节点、再窗口读段落」的确定性入口
（替代整读）。它一旦与文档漂移，给出的 `start-end` 就是**死指针** —— 静默地把你领到错误的
段落。本守卫把这条变成机检：索引必须等于当场重算。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("_doc_outline", ROOT / "scripts" / "doc_outline.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != __main__` ⇒ 不跑 main
    return module


mod = _load()


class SectionsOfTest(unittest.TestCase):
    def test_parent_covers_its_subtree(self):
        md = "# A\nx\n## B\ny\n### C\nz\n## D\nw\n"
        got = [(s["title"], s["start"], s["end"]) for s in mod.sections_of(md)]
        # A 覆盖全篇；B 到下一个「级别≤2」（D）前一行；C 到文末时被 D 截断。
        self.assertEqual(got, [("A", 1, 8), ("B", 3, 6), ("C", 5, 6), ("D", 7, 8)])

    def test_hash_without_space_is_not_a_heading(self):
        # `#tag` 不是标题（ATX 要求 `#` 后有空格），只有 `## ok` 算。
        self.assertEqual(mod.sections_of("#tag\n## ok\n"),
                         [{"level": 2, "title": "ok", "start": 2, "end": 2}])

    def test_no_heading_yields_empty(self):
        self.assertEqual(mod.sections_of("just text\n"), [])


class FindTest(unittest.TestCase):
    def test_case_insensitive(self):
        secs = mod.sections_of("# Topic\n## topic sub\n")
        self.assertEqual(mod._find(secs, "TOPIC")["level"], 1)

    def test_multi_hit_prefers_shallowest(self):
        secs = mod.sections_of("# Topic\n## topic sub\n")
        # 两个都命中 'topic'，取最浅（顶层）那条 —— 窗口读顶层不会漏子节。
        self.assertEqual(mod._find(secs, "topic")["start"], 1)

    def test_miss_returns_none(self):
        self.assertIsNone(mod._find(mod.sections_of("# A\n"), "zzz"))


class IndexSyncTest(unittest.TestCase):
    def test_index_matches_docs(self):
        # 守卫本体：docs/outline.jsonl 必须等于当场重算（漂移即红）。
        self.assertEqual(mod._load_index(), mod._build_index(),
                         "docs/outline.jsonl 与文档漂移 —— 跑 python3 scripts/doc_outline.py --write")

    def test_check_exit_zero(self):
        self.assertEqual(mod.main(["--check"]), 0)

    def test_every_registered_doc_is_indexed(self):
        paths = {r["path"] for r in mod._build_index()}
        for doc in mod._registered_docs():
            self.assertIn(mod._rel(doc), paths, f"{doc} 没进章节级索引（入口不可见）")


class CliTest(unittest.TestCase):
    def test_resolves_real_doc_heading_to_window(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.main([str(ROOT / "docs" / "design.md"), "门与证据"])
        self.assertEqual(rc, 0)
        self.assertRegex(buf.getvalue().strip(), r"^docs/design\.md:\d+-\d+$")

    def test_missing_heading_is_exit_1(self):
        rc = mod.main([str(ROOT / "docs" / "design.md"), "绝不存在的一节zzz"])
        self.assertEqual(rc, 1)

    def test_missing_doc_is_exit_1(self):
        self.assertEqual(mod.main(["docs/__nope__.md"]), 1)


if __name__ == "__main__":
    unittest.main()
