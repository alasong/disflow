"""文档防漂移：文档里的 `disflow <动词>` 必须与 CLI 真实动词集一致。

**为什么要有这个**：手工维护三份文档（需求/设计/开发）时，旧命令名会被悄悄留下——
实测曾留下 `disflow state rebuild`、`disflow checkpoint`（后者根本不是命令）与 `disflow start/check/…`。
不靠人记，靠断言。
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import subprocess
import sys
import unittest
from typing import Any

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
# 匹配「disflow <动词>」形态。
# **排除词表**（2026-10-11 实测踩到）：裸 `disflow\s+([a-z-]+)` 会把「disflow vs kaa」的
# `vs`、以及英文行文里的介词/连词/系动词当成 CLI 动词 ⇒ 报「文档引用了不存在的动词」。
# 这是**尺子的缺陷**，不是文档的错：「disflow vs X」是极常见的对比写法。
# 判据：排除词是**完整单词**（`\b` 级），不需要排除带连字符的复合词（它们很少是英文虚词）。
_NOT_A_VERB: frozenset[str] = frozenset({
    "vs", "and", "or", "is", "are", "the", "a", "an", "in", "on", "of", "to", "for",
    "with", "by", "as", "at", "from", "than", "that", "this", "it", "was", "be",
    "can", "will", "not", "no", "all", "any", "its", "your", "our", "their",
})

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
            if re.fullmatch(r"[a-z][a-z-]*", token) and token not in _NOT_A_VERB:
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


# README 声明的变异数：`python3 scripts/mutation_check.py    # 变异测试：<c>/<t>`。
_README_MUTATION_COUNT = re.compile(
    r"scripts/mutation_check\.py\s+#\s*变异测试：(\d+)/(\d+)")


# README 声明的测试数：`python3 -m unittest discover -s tests -t tests    # <N> passed`。
# 抽成正则是为了让「本仓恰好写对」与「尺子有效」分得开（下同）。
_README_TEST_COUNT = re.compile(
    r"python3 -m unittest discover -s tests -t tests\s+#\s*(\d+)\s+passed")

_PARTIAL_HEADING = "## [◐ 部分缺口]"

# 本轮新沉淀的教训文档（n13-reflect）。它会被新读者照着走，所以里面的仓内路径不许失效。
LESSONS = ROOT / "docs/self-harden-lessons.md"

# 仓内路径引用的形状：只认这四类顶层目录开头的**完整相对路径**。
# `disflow/<tree>/<node>` 是占位（不是文件）；`.agents/skills/disflow/SKILL.md` 由
# 负向后顾挡住（否则会被读成 `disflow/SKILL.md` 这种不存在的缩写而假红）。
_REPO_REF = re.compile(
    r"(?<![\w./-])((?:disflow|tests|scripts|docs)/[A-Za-z0-9_./-]+\.(?:py|md|sh|json))")


def _repo_refs(text: str) -> set[str]:
    """文本里引用的仓内路径集合（形如 `tests/test_docs.py`）。"""
    return set(_REPO_REF.findall(text))


def _options_without_help() -> tuple[int, int]:
    """当场数 CLI 选项说明：`(help 为空的个数, 选项总数)`。

    抽成函数与 `_verbs_in` 同理：让「手册恰好写对」与「尺子有效」分得开。
    为何要它：`.agents/skills/disflow/SKILL.md` §5 声明「0 个无说明」——那是必须与当场
    读数对账的数字（n11 修之前它是 14，别让它悄悄回去）。
    """
    parser = build_parser()
    sub: Any = parser._subparsers._group_actions[0]  # type: ignore[union-attr]
    total = missing = 0
    for subparser in sub.choices.values():
        for action in subparser._actions:
            for _opt in action.option_strings:
                total += 1
                if not action.help:
                    missing += 1
    return missing, total


def _cli_suite_size() -> int:
    """当场装载整个测试套件的用例数（= `unittest discover` 报告的 `Ran N tests`）。"""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"),
                            top_level_dir=str(ROOT / "tests"))
    return suite.countTestCases()


def _mutation_harness_size() -> int:
    """当场装载 `scripts/mutation_check.py` 的变异表长度（= 该 harness 的 `N/N` 分母）。"""
    spec = importlib.util.spec_from_file_location(
        "_n10_mutation_check", ROOT / "scripts" / "mutation_check.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != __main__` ⇒ 不会跑 harness
    return len(module.MUTATIONS)


def _partial_count_in_summary(text: str) -> int:
    """报告摘要行里 `[◐ 部分缺口]` 后声明的条数。"""
    for line in text.splitlines():
        if "另有" in line and "◐ 部分缺口" in line:
            tail = line.split("◐ 部分缺口", 1)[1]
            match = re.search(r"(\d+)\s*条", tail)
            if match is None:
                raise AssertionError(f"摘要行里找不到 ◐ 计数：{line!r}")
            return int(match.group(1))
    raise AssertionError("报告里没有 ◐ 部分缺口的摘要行")


def _entries_in_section(text: str, heading: str) -> int:
    """某节正文里 `- **` 起头的条目数（到下一个 `## ` 为止）。"""
    count = 0
    inside = False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line.startswith(heading)
            continue
        if inside and line.startswith("- **"):
            count += 1
    return count


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

    def test_english_prose_is_not_mistaken_for_a_verb(self):
        """回归：裸 `disflow\\s+([a-z-]+)` 会把「disflow vs kaa」的 `vs` 当成动词。

        实测踩到：`docs/kaa-comparison.md` 标题写「disflow vs kaa」⇒ 文档门报
        「引用了不存在的动词 `vs`」。**这是尺子的缺陷**，不是文档的错。
        判据：英文虚词（`vs/and/is/of/…`）永不作为动词候选；真动词仍需被抓。
        """
        for prose in ("disflow vs kaa", "disflow and kaa", "disflow is a tool",
                      "disflow in action", "disflow to the rescue"):
            self.assertEqual(_verbs_in(prose), set(), f"{prose!r} 不应产出动词")
        # 反向：真动词仍要被抓（证明不是把整条规则关掉）
        self.assertEqual(_verbs_in("disflow board t"), {"board"})
        self.assertEqual(_verbs_in("disflow start/done t n"), {"start", "done"})

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


class SelfHardenSedimentTest(DisflowTestCase):
    """n13-reflect 的沉淀守卫：钉住本轮**改对/新增**的手册事实，防它们回退。

    为何要这个类：本轮实测「同一批节点会互相把对方的说法写旧」——SKILL §3 第 4 条的机制
    解释被同批的 n6 推翻而无人管（n12 评估记为 U2）。既有守卫抓不到它：
    `test_worker_contract_forbids_self_commit` 只断言 `git commit` 与「宿主统一提交」还在，
    把机制解释改回旧说法它照绿。

    只钉**机器读得出来**的那部分：过时说法在不在、声明数字与当场读数是否一致、新节是否
    还在、新文档引用的仓内路径是否失效。**事实对不对仍是人读的**，这里不冒充。
    """

    def test_skill_section_3_drops_the_stale_blind_gate_story(self):
        """§3 不得再讲「提交了门就看不见」——n6 之后 `start` **一律**记 `head_sha`。

        负向对照（已跑）：把这句改回过时说法 ⇒ 本测试变红。
        """
        section = _section(SKILL.read_text(encoding="utf-8"), "3")
        for stale in ("不看已提交改动", "门彻底看不见改动",
                      "vacuous=[design_first, size_budget]"):
            self.assertNotIn(stale, section, f"§3 又回到过时的机制解释：{stale!r}")
        for fresh in ("head_sha", "一律", "worktree"):
            self.assertIn(fresh, section, f"§3 缺少修正后的机制事实：{fresh!r}")

    def test_skill_section_5_help_claim_matches_the_live_parser(self):
        """§5 声明的「无说明选项数」必须等于当场数出来的（数字过时 = 手册说假话）。"""
        section = _section(SKILL.read_text(encoding="utf-8"), "5")
        match = re.search(r"(\d+) 个无说明", section)
        self.assertIsNotNone(match, "§5 必须声明「N 个无说明」以便与当场读数对账")
        assert match is not None
        missing, total = _options_without_help()
        self.assertEqual(int(match.group(1)), missing,
                         f"§5 说 {match.group(1)} 个无说明，当场数出 {missing}/{total}")

    def test_skill_has_the_sedimented_hard_facts_section(self):
        """必须存在 §10（本轮沉淀），且点名要求的事实都在里面。"""
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(_section_heading(text, "10"), "缺少 §10 沉淀节（新事实没有落点）")
        section = _section(text, "10")
        for needed in ("contact_supervisor", "deferred", "--force", "1236", "407"):
            self.assertIn(needed, section, f"§10 缺少本轮硬事实：{needed!r}")

    def test_lessons_doc_exists_and_names_the_host_errors(self):
        """`docs/self-harden-lessons.md` 必须存在，且点名本轮指定的宿主错误。"""
        self.assertTrue(os.path.isfile(LESSONS), f"教训文档缺失：{LESSONS}")
        text = LESSONS.read_text(encoding="utf-8")
        for token in ("origin/", "PYTHONPATH", "引号", "reap", "sed"):
            self.assertIn(token, text, f"教训文档缺指定宿主错误的关键词：{token!r}")

    def test_the_repo_ref_ruler_is_not_vacuous(self):
        """**尺子自测**：路径提取要能认活路径、报死路径，且不把占位当文件。"""
        sample = "看 `tests/test_docs.py` 与 `docs/does-not-exist.md`。"
        self.assertEqual(_repo_refs(sample),
                         {"tests/test_docs.py", "docs/does-not-exist.md"})
        self.assertEqual(_repo_refs("`disflow/<tree>/<node>` 与 `<state_dir>/x.json`"), set())

    def test_lessons_doc_repo_refs_all_exist(self):
        """教训文档引用的**仓内路径**不得失效（新文档是新读者会照着走的入口）。"""
        refs = _repo_refs(LESSONS.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(refs), 3, "尺子没抓到足够引用，守卫形同虚设")
        dead = sorted(ref for ref in refs if not (ROOT / ref).exists())
        self.assertEqual(dead, [], f"教训文档引用了不存在的仓内路径：{dead}")


class DocsTruthTest(DisflowTestCase):
    """文档里的**数字与断言**必须与当场实测一致（否则文档在向人说实话之外的地方说假话）。

    钉三处实测漂移：
    ① README 的测试数曾写 301，而当场 `unittest discover` 报 555；
    ② README 曾把「没改任何东西就 `done`」写成「退出码 2」，实测是 exit 0 + `⚠️ 门空洞通过`
       ——见 `.agents/skills/disflow/SKILL.md` §5：「门空洞通过」是**警告不是拒收**；
    ③ `docs/fr-nfr-gap-report.md` 的头写「◐ 部分缺口 2 条」，而正文里一条自己写着
       「本条已降级：需求已满足」⇒ 计数与正文自相矛盾。
    """

    def _read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_readme_test_count_matches_the_loaded_suite(self):
        """README 声明的测试数 == 当场装载的测试数（数字过时 = 文档说假话）。"""
        numbers = _README_TEST_COUNT.findall(self._read("README.md"))
        self.assertEqual(len(numbers), 1,
                         "README 必须恰好声明一处测试数（正则会命中 0 或 >1 处）")
        actual = _cli_suite_size()
        self.assertEqual(int(numbers[0]), actual,
                         f"README 说 {numbers[0]} passed，当场装载 {actual} 个测试")

    def test_readme_does_not_pair_nothing_changed_with_exit_code_2(self):
        """「没改任何东西」与「退出码 2」不许同时出现——这对措辞对不上实测。

        实测：什么都没改就 `done` ⇒ exit 0 + `⚠️ 门空洞通过（无东西可验）`。门拦的是
        **验收不匹配**，不是「没改动」。
        """
        text = self._read("README.md")
        self.assertFalse(
            "没改任何东西" in text and "退出码 2" in text,
            "README 同时出现「没改任何东西」与「退出码 2」：这对措辞对不上实测"
            "（没改任何东西 → exit 0 + ⚠️ 门空洞通过警告，不是退出码 2）")

    def test_readme_mutation_count_matches_the_harness(self):
        """README 声明的变异数 == 当场装载的变异表（分母漂了 / 把漏抓写成通过都算假话）。

        只钉得动**分母**与**自洽性**：真跑变异 harness 要 ~20 分钟，不适合塞进单测；
        「有漏抓」由 `scripts/mutation_check.py` 自己的 exit 1 报出（其契约）。
        """
        numbers = _README_MUTATION_COUNT.findall(self._read("README.md"))
        self.assertEqual(len(numbers), 1, "README 必须恰好声明一处变异数")
        caught, total = (int(x) for x in numbers[0])
        harness = _mutation_harness_size()
        self.assertEqual(total, harness,
                         f"README 说变异总数 {total}，harness 里 {harness} 条"
                         "（新增/删改变异请同步 README）")
        self.assertEqual(caught, total,
                         f"README 写 {caught}/{total}：不许把「有漏抓」写成通过"
                         "（harness 自己会 exit 1）")

    def test_partial_gap_count_matches_the_listed_entries(self):
        """报告头声明的 ◐ 条数 == 正文里真正标 ◐ 的条数（计数不许与正文打架）。"""
        text = self._read("docs/fr-nfr-gap-report.md")
        declared = _partial_count_in_summary(text)
        listed = _entries_in_section(text, _PARTIAL_HEADING)
        self.assertEqual(declared, listed,
                         f"报告说 ◐ 部分缺口 {declared} 条，正文里却列了 {listed} 条")

    def test_report_regenerates_byte_for_byte(self):
        """报告必须仍能由生成器逐字节重生成（否则手改报告会悄悄漂移）。"""
        proc = subprocess.run(
            [sys.executable, "scripts/fr_check.py", "--report"],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, f"生成器失败：{proc.stderr}")
        self.assertEqual(
            proc.stdout, self._read("docs/fr-nfr-gap-report.md"),
            "docs/fr-nfr-gap-report.md 与 `python3 scripts/fr_check.py --report` 不一致；"
            "请重跑 `python3 scripts/fr_check.py --report > docs/fr-nfr-gap-report.md`")

    def test_the_partial_gap_ruler_is_not_vacuous(self):
        """**尺子自测**：分节与计数两个尺子都要真能数（否则断言永远绿）。"""
        sample = (
            "摘要…另有 `[◐ 部分缺口]` 3 条。\n"
            "\n## [◐ 部分缺口]（标题）\n\n"
            "- **A / 子句**\n  - 缺什么：x\n"
            "- **B / 子句**\n  - 缺什么：y\n"
            "\n## [已复核·非缺口]（标题）\n\n- **C / 子句**\n  - 复核结论：z\n")
        self.assertEqual(_partial_count_in_summary(sample), 3)
        self.assertEqual(_entries_in_section(sample, _PARTIAL_HEADING), 2)
        # 反向：不存在的节 → 0（否则断言就是摆设）
        self.assertEqual(_entries_in_section(sample, "## 不存在"), 0)


class MachineCheckGuardTest(DisflowTestCase):
    """本仓的**可执行判据脚本**必须当场 exit 0。

    为什么要把这两个脚本拉进 unittest（实测事故，本节点成立的理由）：
    `scripts/fr_check.py`（32 FR + 7 NFR）与 `scripts/goal_check.py`（病根/保留项/目标）
    **不在** `unittest discover` 的覆盖里。节点 `n7-accounting` 给 CLI 加了「`done` 前先
    `collect`」契约，却漏改 `fr_check.py` 的 FR-18/FR-20 探针 —— 两个机检脚本红了约 40 分钟：
    n7 的验收（只跑 unittest 与自举探针）与全量 unittest 都看不见它。守卫在此，
    下次同类漂移会被当场跑红。

    为何不把 `scripts/mutation_check.py` 也拉进来：真跑一次 ~4 分钟，塞进单测会把套件拖垮
    （它自己的 exit 1 契约仍在，只是不在此守卫内）。
    """

    def _run_machine_check(self, script: str) -> None:
        proc = subprocess.run(
            [sys.executable, f"scripts/{script}"], cwd=ROOT,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(
            proc.returncode, 0,
            f"scripts/{script} 必须 exit 0（实测 {proc.returncode}）。"
            f"尾部：\n{(proc.stdout + proc.stderr)[-1500:]}")

    def test_fr_check_passes_on_the_current_tree(self):
        """`python3 scripts/fr_check.py`（32 FR + 7 NFR 逐条机检）必须全过。"""
        self._run_machine_check("fr_check.py")

    def test_goal_check_passes_on_the_current_tree(self):
        """`python3 scripts/goal_check.py`（病根/保留项/目标/非目标）必须全过。"""
        self._run_machine_check("goal_check.py")


if __name__ == "__main__":
    unittest.main()
