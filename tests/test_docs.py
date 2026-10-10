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
# `(?:\.cli)?` 必须允许：用法块里写的是 `python3 -m disflow.cli <动词>`（点），
# 而散文里写的是 `disflow <动词>`（空格）——两种都是**合法文档**。
# 曾经只认后者 ⇒ 把已经在用法块里写明的动词谎报成“未文档化”（实测：`unrelate`）。
_REF = re.compile(r"\bdisflow(?:\.cli)?[ \t]+([a-z][a-z-]*(?:[ \t]*/[ \t]*[a-z][a-z-]*)*)")


def _real_verbs() -> set[str]:
    parser = build_parser()
    return set(parser._subparsers._group_actions[0].choices)  # type: ignore[union-attr]


def _verbs_in(text: str) -> set[str]:
    """一段文本里提到的 CLI 动词（展开 `a/b/c` 斜杠列表）。

    抽成函数是为了让**尺子本身可测**（与 `test_layering.py` 的 `MatcherSelfTest` 同理）：
    否则「仓库恰好干净」与「尺子有效」分不开。
    """
    tokens: set[str] = set()
    for match in _REF.finditer(text):
        for token in re.split(r"[ \t]*/[ \t]*", match.group(1)):
            if re.fullmatch(r"[a-z][a-z-]*", token):
                tokens.add(token)
    return tokens


def _mentioned() -> dict[str, set[str]]:
    """文件 → 该文件里提到的动词。"""
    found: dict[str, set[str]] = {}
    for rel in DOC_FILES:
        found[rel] = _verbs_in((ROOT / rel).read_text(encoding="utf-8"))
    return found


def _cells(row: str) -> int:
    """一行 markdown 表格的列数（转义竖线 `\\|` 不计）。"""
    return len(re.findall(r"(?<!\\)\|", row)) - 1


class DocDriftTest(DisflowTestCase):
    def test_the_matcher_recognizes_both_documented_forms(self):
        """**尺子自测**：两种写法都是合法文档，都得认。

        实测踩过：只认 `disflow <动词>`（空格）时，用法块里的
        `python3 -m disflow.cli <动词>`（点）认不出来 ⇒ 把**已经写明**的动词谎报成“未文档化”。
        """
        self.assertEqual(_verbs_in("python3 -m disflow.cli unrelate a b"), {"unrelate"})
        self.assertEqual(_verbs_in("用 `disflow unrequire` 删掉它"), {"unrequire"})
        self.assertEqual(_verbs_in("disflow rebuild / reap"), {"rebuild", "reap"})
        # 反向：没有提到任何动词的文本必须是空集（否则断言就是摆设）
        for text in ("disflow.cli", "disflow.cli \"$@\"", "disflow.cli <tree>",
                     "完全没有相关文字"):
            self.assertEqual(_verbs_in(text), set(), f"不该凭空认出动词：{text!r}")

    def test_the_matcher_still_rejects_a_bogus_verb(self):
        """反向保护：编造的动词必须被认出（否则“未文档化”那条断言永远绿）。"""
        self.assertEqual(_verbs_in("disflow.cli 不存在的动词", ), set())
        self.assertEqual(_verbs_in("disflow.cli nonexist a b"), {"nonexist"})

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

    def test_markdown_tables_are_rectangular(self):
        """每个 markdown 表格的所有行（含 `---` 分隔行）必须与表头**同列数**。

        为何要断言：分隔行写少一列时，markdown-lint 不报，但渲染结果是错的
        （实测：`| --- | --- |` 配 3 列表头，表格在 GitHub 上直接不成立）。
        转义竖线 `\\|` 不算分隔（`devflow handoff brief\\|collect` 这种写法合法）。
        """
        offenders: list[str] = []
        for rel in DOC_FILES:
            lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
            index = 0
            while index < len(lines):
                if not lines[index].startswith("|"):
                    index += 1
                    continue
                end = index                        # 表格块 = 连续以 | 开头的行
                while end < len(lines) and lines[end].startswith("|"):
                    end += 1
                head = _cells(lines[index])
                for offset in range(index, end):
                    if _cells(lines[offset]) != head:
                        offenders.append(
                            f"{rel}:{offset + 1} {_cells(lines[offset])}列 vs 表头{head}列")
                index = end
        self.assertEqual(offenders, [], f"表格列数不齐（渲染会错）：{offenders}")


if __name__ == "__main__":
    unittest.main()
