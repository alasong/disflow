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
# 宿主手册：不在 docs/（那是给使用者的文档），但读者同样会照它做——同样要防漂移。
SKILL = ROOT / ".agents/skills/disflow/SKILL.md"

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


def _section(text: str, number: str) -> str:
    """取 `## <number> …` 到下一个同级 `## ` 之间的**正文**（不含标题行）。

    抽成函数与 `_verbs_in` 同理：让「仓库恰好干净」与「尺子有效」分得开。
    """
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(f"## {number} ")
            continue                      # 标题行本身不进正文
        if inside:
            out.append(line)
    return "\n".join(out)


def _section_heading(text: str, number: str) -> str:
    """`## <number> …` 标题行（层级/编号变化时守卫要能看见）。"""
    return next((line for line in text.splitlines()
                 if line.startswith(f"## {number} ")), "")


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


class HostManualTest(DisflowTestCase):
    """宿主手册 `.agents/skills/disflow/SKILL.md` 的防漂移守卫。

    钉的是两个实测会让使用者踩坑的缺陷：
    ① §2 宿主循环漏了 `start` —— 不 start 就没有开工基线，门在空基线上跑；
    ② 没写明「worker 不许自己 commit」（自提交后工作区干净 ⇒ 门看不见改动、exit 0）。
    """

    def test_matcher_extracts_a_section_and_ignores_the_rest(self):
        """**尺子自测**：分节提取要正确切片，否则下面的断言可能是摆设。"""
        sample = "## 1 甲\n正文一\n$D start a b\n\n## 2 乙\n正文二\n## 3 丙\n正文三\n"
        self.assertIn("$D start a b", _section(sample, "1"))
        self.assertIn("正文二", _section(sample, "2"))
        self.assertNotIn("正文三", _section(sample, "2"))
        self.assertEqual(_section_heading(sample, "2"), "## 2 乙")
        # 反向：不存在的编号 → 空节（否则断言永远绿）
        self.assertEqual(_section(sample, "9"), "")

    def test_host_manual_exists(self):
        self.assertTrue(os.path.isfile(SKILL), f"宿主手册缺失：{SKILL}")

    def test_host_loop_includes_the_start_step(self):
        """§2 的每轮循环必须含 `start`：它领租约并拍开工基线。

        实测：不 start 直接 `collect`+`done` 也能 exit 0，但门在**空基线**上跑——
        开工前己脏的文件被算成本节点引入的改动，或（真没改时）`introduced=[]` 空洞通过。
        """
        text = SKILL.read_text(encoding="utf-8")
        loop = _section(text, "2")
        self.assertRegex(loop, r"\$D start <tree> <node>",
                         "§2 宿主循环必须列出 start 步骤（否则派活没有开工基线）")
        self.assertIn("七步", _section_heading(text, "2"),
                      "§2 标题的步数要随步骤数走（补了 start 就是七步）")

    def test_worker_contract_forbids_self_commit(self):
        """§3 worker 契约必须写明「worker 不许自己 commit、宿主统一提交」。"""
        contract = _section(SKILL.read_text(encoding="utf-8"), "3")
        self.assertIn("git commit", contract,
                      "§3 要点必须出现 `git commit` 这条硬纪律")
        self.assertIn("宿主统一提交", contract,
                      "§3 必须写明提交由宿主统一做")

    def test_start_trap_is_documented(self):
        """§5 陷阱必须写明「不 start 就没有开工基线」。"""
        traps = _section(SKILL.read_text(encoding="utf-8"), "5")
        self.assertIn("开工基线", traps, "§5 陷阱必须写明不 start 就没有开工基线")


if __name__ == "__main__":
    unittest.main()
