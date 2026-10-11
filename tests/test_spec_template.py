"""`templates/` 的守卫：模板是**给人拷去用的**，写坏了不会有人发现。

为何要有（不是洁癖）：模板**不在任何运行时路径上**——它的错误不会像代码那样在
`finish.py` 里炸出来。实测同类教训：`v0.2-backlog.md` 曾列出触发条件却没被任何
机检覆盖，于是内容腐烂了很久没人知。模板更脆：它是**给别人复制的**，
一个失效的路径/命令会被复制到别的项目里去。

三条硬判据（对应模板的三个承诺）：
  ① **PDCA 四段齐全**：模板自称按 PDCA，那四个段标题必须真的存在（否则名不副实）。
  ② **可复制**：README 说的「拷到项目根改名」不能有死角——不能被 `docs/INDEX.md`
     的守卫误伤（它只扫 `docs/`），也不能含本仓专属路径（别人拷过去就找不到）。
  ③ **附录的命令是真命令**：模板附录给的 `disflow` 子命令必须在 CLI 里存在，
     否则照抄的人会撞「unrecognized arguments」。
"""
from __future__ import annotations

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "templates", "spec-pdca")


class SpecTemplateTest(unittest.TestCase):
    def _read(self, name: str) -> str:
        with open(os.path.join(TPL, name), encoding="utf-8") as fh:
            return fh.read()

    def test_template_has_all_four_pdca_sections(self):
        """① PDCA 四段齐全——模板自称按 PDCA，段标题必须真在。"""
        text = self._read("SPEC-模板.md")
        for letter, word in (("P", "计划"), ("D", "执行"), ("C", "检查"), ("A", "改进")):
            pattern = rf"^##\s*{letter}\s*·\s*{word}"
            # 用 `re.search(..., re.M)` 而非 `assertRegex`：后者不接受 flags 位置参数
            # （它的签名是 `(text, regex, msg)`），只能传一个已编译的 pattern。
            # `re.M` 必须有：`^` 只在 MULTILINE 下匹配行首；四段标题都在文本中间，
            # 少了它四条会全报「缺 X 段」（实测踩到）。
            self.assertIsNotNone(
                re.search(pattern, text, re.MULTILINE),
                f"缺 {letter}（{word}）段——模板号称 PDCA，四段必须齐全")

    def test_readme_names_the_template_file(self):
        """README 说的模板文件名必须真实存在（改名不改 README = 指路指到空地）。"""
        readme = self._read("README.md")
        self.assertIn("SPEC-模板.md", readme)
        self.assertTrue(os.path.isfile(os.path.join(TPL, "SPEC-模板.md")))

    def test_every_template_file_named_in_readme_exists(self):
        """README 里提到的**每个 `*-模板.md`** 都必须真实存在。

        为何单独要这一条（实测踩到）：README 的目录树里列了 `constitution.md`，
        但**没有对应模板文件** —— 读者找它时会扑空。当时只钉了 `SPEC-模板.md` 一个，
        所以漏掉了。改成**通扫**：README 里出现的任何 `xxx-模板.md` 都要对得上实物。
        """
        readme = self._read("README.md")
        named = set(re.findall(r"([\w\u4e00-\u9fff-]+-模板\.md)", readme))
        self.assertTrue(named, "README 应至少提到一个 `*-模板.md`")
        missing = sorted(n for n in named if not os.path.isfile(os.path.join(TPL, n)))
        self.assertEqual(missing, [], f"README 提到但实物不存在的模板：{missing}")

    def test_template_keeps_the_openspec_derived_columns(self):
        """模板必须保留**取自 OpenSpec 的栏目**（P1 注释里声明了来源）。

        为何打钉子：这些栏目是从 OpenSpec 的 `spec-driven` schema 抄来的
        （proposal / spec / design / tasks 四个模板），它们比自创的骨架精细：
        特别是 WHEN/THEN 场景、被否的替代方案、破坏性变更标记——
        删了就退回到「泛泛的需求描述」。
        """
        text = self._read("SPEC-模板.md")
        for token in (
            "Why",                       # proposal 的 Why
            "Impact",                    # proposal 的 Impact
            "**BREAKING**",              # proposal 要求标破坏性变更
            "Scenario",                  # delta spec 的场景
            "**WHEN**", "**THEN**",      # 场景的两个关键字
            "Goals / Non-Goals",        # design 的目标/非目标
            "被否的替代方案",             # design 的 Decisions 要求列 alternatives
            "Risks / Trade-offs",        # design 的风险
            "Open Questions",           # design 的遗留问题
        ):
            self.assertIn(token, text, f"模板缺 OpenSpec 栏目「{token}」")

    def test_requirement_uses_normative_language(self):
        """需求必须用 SHALL/MUST 这类规范词（OpenSpec 原文：避免 should/may）。"""
        text = self._read("SPEC-模板.md")
        self.assertTrue("SHALL" in text or "MUST" in text,
                        "需求语言要用 SHALL/MUST（should/may 不具约束力）")

    def test_scenario_headings_use_four_hashes(self):
        """场景标题必须是**四个** `#`。

        为何是硬判据（OpenSpec 原文警告）：`CRITICAL`: Scenarios MUST use
        exactly 4 hashtags（`####`）。**Using 3 hashtags or bullets will fail
        silently.** —— 静默失败那类缺陷，必须有牙。
        """
        text = self._read("SPEC-模板.md")
        # 恰好四个 `#`——**不是「至少四个」**。
        # OpenSpec 原文：`CRITICAL`: Scenarios MUST use exactly 4 hashtags (`####`)。
        # 当初我写了 `{4,5}`，于是模板里错用 5 个 `#` 也放行（实测踩到）。
        bad = re.findall(r"^###[^#]\s*场景", text, re.MULTILINE)
        self.assertEqual(bad, [], f"场景标题用了 3 个 # （会静默失效）：{bad}")
        too_deep = re.findall(r"^#{5,}\s*场景", text, re.MULTILINE)
        self.assertEqual(too_deep, [], f"场景标题层级过深（应恰好 4 个 #）：{too_deep}")
        self.assertRegex(text, r"(?m)^#{4}\s*场景", "场景标题应为恰好四个 #")
        """README 里提到的**每个 `*-模板.md`** 都必须真实存在。

        为何单独要这一条（实测踩到）：README 的目录树里列了 `constitution.md`，
        但**没有对应模板文件** —— 读者找它时会扑空。当时只钉了 `SPEC-模板.md` 一个，
        所以漏掉了。改成**通扫**：README 里出现的任何 `xxx-模板.md` 都要对得上实物。
        """
        readme = self._read("README.md")
        named = set(re.findall(r"([\w\u4e00-\u9fff-]+-模板\.md)", readme))
        self.assertTrue(named, "README 应至少提到一个 `*-模板.md`")
        missing = sorted(n for n in named if not os.path.isfile(os.path.join(TPL, n)))
        self.assertEqual(missing, [], f"README 提到但实物不存在的模板：{missing}")

    def test_readme_states_the_openspec_provenance(self):
        """README 必须声明**栏目取自 OpenSpec**，并列出 P1–P5 与它的对应。

        为何是硬判据（实测踩到）：模板重写成 OpenSpec 栏目后，README 滞后了一版（
        还在说「业界四步 Specify/Plan/Tasks/Implement」）。**来源声明过期比没有更坏**——
        读者会按错的出处去查证。所以两边一起钉：
          ① README 要提到 OpenSpec 与它具体的 schema 名；
          ② README 要出现 P1…P5 这五个子节（否则读者不知道 P 段里有什么）。
        """
        readme = self._read("README.md")
        self.assertIn("OpenSpec", readme, "README 必须声明栏目来源")
        self.assertIn("spec-driven", readme, "要写出具体是哪个 schema")
        for sub in ("P1", "P2", "P3", "P4", "P5"):
            self.assertIn(sub, readme, f"README 缺 {sub} 的说明")

    def test_entry_name_matches_template_sections(self):
        """README 列的子节名，必须在模板正文里真存在（否则只是宣传）。"""
        readme = self._read("README.md")
        tpl = self._read("SPEC-模板.md")
        for sub in ("P1", "P2", "P3", "P4", "P5"):
            self.assertIn(sub, readme)
            self.assertIn(sub, tpl, f"README 提到 {sub}，但模板正文没有它")

    def test_template_cites_the_openspec_source(self):
        """模板必须给出 OpenSpec 的**可查证出处**（URL）。

        为何：模板里有 6 处写「OpenSpec 原文的约束」——没出处的话，读者既无法查证，
        也无法判断这些规矩是不是我编的。**引用不给源**等于不可审计。
        """
        text = self._read("SPEC-模板.md")
        self.assertRegex(text, r"https://openspec\.dev/\S+", "要给出可查证的 OpenSpec 链接")

    def test_constitution_template_has_the_red_lines(self):
        """铁律模板必须包含红线段与「怎么维护」——否则它只是一张空表。"""
        text = self._read("constitution-模板.md")
        for token in ("红线", "Ask first", "Always", "pre-commit"):
            self.assertIn(token, text, f"constitution 模板缺「{token}」")

    def test_appendix_commands_exist_in_the_cli(self):
        """③ 附录里的 `disflow <动词>` 必须真是 CLI 动词（照抄不能撞墙）。"""
        text = self._read("SPEC-模板.md")
        # `[\w-]+` 而非 `\w+`：CLI 动词含连字符（`host-next`）——实测踩到，
        # 用 `\w+` 会把它截成 `host` ⇒ 误报「不存在的动词」。
        verbs = set(re.findall(r"^disflow ([\w-]+)", text, re.MULTILINE))
        self.assertTrue(verbs, "附录应给出可照抄的 disflow 命令")
        from disflow.cli import build_parser
        known = set(build_parser()._subparsers._group_actions[0].choices)  # type: ignore[attr-defined]
        unknown = verbs - known
        self.assertEqual(unknown, set(),
                         f"模板里用了不存在的 CLI 动词：{unknown}（现有 {sorted(known)}）")

    def test_template_does_not_leak_repo_specific_paths(self):
        """② 不被本仓专属路径绑死：拷去别的项目后仍能用。

        判据：模板正文里若出现 `docs/INDEX.md` / `assets/registry.json` 这类
        **本仓**路径，那是**举例**可以，但**不得当成必填动作**（`必须`/`先跑` 之类）。
        这里只钉最硬的一条：不得出现本仓的绝对路径。
        """
        for name in ("README.md", "SPEC-模板.md"):
            text = self._read(name)
            self.assertNotIn(ROOT, text, f"{name} 里不得写本仓绝对路径（别人拷过去失效）")


if __name__ == "__main__":
    unittest.main()
