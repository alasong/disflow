"""文档防漂移：文档里的 `disflow <动词>` 必须与 CLI 真实动词集一致。

**为什么要有这个**：手工维护三份文档（需求/设计/开发）时，旧命令名会被悄悄留下——
实测曾留下 `disflow state rebuild`、`disflow checkpoint`（后者根本不是命令）与 `disflow start/check/…`。
不靠人记，靠断言。
"""
from __future__ import annotations

import os
import pathlib
import re
import unittest

from base import DisflowTestCase

from disflow.cli import build_parser

ROOT = pathlib.Path(__file__).resolve().parent.parent
# 历史稿（`design-dispatch-tree-graph.md`）也纳入：它同样会被读者当参考。
DOC_FILES = ["README.md"] + [
    str(p.relative_to(ROOT)) for p in sorted((ROOT / "docs").glob("*.md"))
]

# `[ \t]+` 而非 `\s+`：后者会跨行匹配，把下一行开头的 `python3` 当成动词（实测踩过）。
_REF = re.compile(r"\bdisflow[ \t]+([a-z][a-z-]*(?:[ \t]*/[ \t]*[a-z][a-z-]*)*)")


def _real_verbs() -> set[str]:
    parser = build_parser()
    return set(parser._subparsers._group_actions[0].choices)  # type: ignore[union-attr]


def _mentioned() -> dict[str, set[str]]:
    """文件 → 该文件里 `disflow <动词>` 提到的动词（展开 `a/b/c` 斜杠列表）。"""
    found: dict[str, set[str]] = {}
    for rel in DOC_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        tokens: set[str] = set()
        for match in _REF.finditer(text):
            for token in re.split(r"[ \t]*/[ \t]*", match.group(1)):
                if re.fullmatch(r"[a-z][a-z-]*", token):
                    tokens.add(token)
        found[rel] = tokens
    return found


class DocDriftTest(DisflowTestCase):
    def test_no_doc_mentions_a_nonexistent_verb(self):
        real = _real_verbs()
        offenders: dict[str, list[str]] = {}
        for rel, tokens in _mentioned().items():
            bogus = sorted(tokens - real)
            if bogus:
                offenders[rel] = bogus
        self.assertEqual(
            offenders, {},
            f"文档引用了不存在的动词（真实动词 {len(real)} 个）：{offenders}")

    def test_every_verb_is_documented_somewhere(self):
        seen: set[str] = set()
        for tokens in _mentioned().values():
            seen |= tokens
        self.assertEqual(sorted(_real_verbs() - seen), [],
                         "以下 CLI 动词未在任何文档出现（新增动词请同步设计文档 §9）")

    def test_doc_files_exist(self):
        for rel in DOC_FILES:
            self.assertTrue(os.path.isfile(ROOT / rel), f"文档缺失：{rel}")


if __name__ == "__main__":
    unittest.main()
