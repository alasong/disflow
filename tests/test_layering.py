"""NFR-2 分层自检（沿用 flowcore「lib 零业务判据名」的判据）。

**逆向自检**：扫描核心层文件，命中任何业务词即失败——不是「文档说核心中立」，而是可执行的断言。

核心层 = `core.py` / `store.py` / `graph.py` / `ready.py`（通用机制与纯函数）。
业务层 = `spec.py` / `model.py` / `state.py` / `gates.py` / `views.py` / `context.py` /
`dispatch.py` / `engine.py` / `cli.py` / `import_mgt.py`（本实例的名词与策略）。

**已知边界（诚实标注）**：本自检是**针脚表 + 单词边界**，不是业务词的闭集推导——
新增业务词若未登记为针脚则漏判。因此另设 `MatcherSelfTest` 吃合成样本来证明针脚本身有效
（而非靠「仓库恰好干净」）。
"""
from __future__ import annotations

import os
import re
import unittest

from base import DisflowTestCase

import disflow
from disflow import core

CORE_FILES = ("core.py", "store.py", "graph.py", "ready.py")

# 业务词针脚（本实例的判据名 / 字段名）。用拼接构造，避免本文件被自己命中。
# 子串匹配：词形足够长且无歧义。
SUBSTRING_NEEDLES = (
    "ev" + "aluate",              # entry=…（收益验证）
    "re" + "flect",               # entry=…（经验沉淀）
    "stand" + "ardize",
    "touch" + "_plan",            # 写面声明
    "accept" + "ance",            # 验收声明
    "clo" + "sure",               # 终止条件逃生口
    "cove" + "rage",              # 覆盖门
    "wai" + "ver",                # 豁免
    "size_" + "budget",           # 规模预算
    "看" + "板",                   # 看板
    "do" + "ctor",                # CLI 自检命令
    "m" + "gt",                   # 迁移源
    "br" + "ief",                 # 派发契约
    "har" + "ness",               # 本实例的 harness 名词
)

# 单词边界匹配：短词，防止误命中 `requires`/`request` 之类。
# 视图列取值 req|design|dev|verify 与负责方取值 dev|qa|pm。
#
# **故意排除 `done`**：它是机制终态（`core.TERMINAL`），核心层合法出现，无法作为业务词针脚。
# （2026-10-11 起它**不再是视图列取值**——看板列只有四阶段——但这条排除的理由不变。）
# 此为已知不可判定项，已登记在 docs/design.md §9.1。
WORD_NEEDLES = ("req", "design", "dev", "verify", "qa", "pm", "stage", "role", "board")

_WORD_RE = re.compile(r"\b(" + "|".join(WORD_NEEDLES) + r")\b")


def matcher_hits(text: str) -> list[str]:
    """返回该文本命中的业务词针脚（供自检与 `MatcherSelfTest` 共用）。"""
    hits = [needle for needle in SUBSTRING_NEEDLES if needle in text]
    hits.extend(match.group(0) for match in _WORD_RE.finditer(text))
    return sorted(set(hits))


def _package_dir() -> str:
    return os.path.dirname(os.path.abspath(disflow.__file__))


class MatcherSelfTest(DisflowTestCase):
    """先证明「尺子」有刻度：针脚能抓到已知业务词，也会放过机制词。"""

    def test_catches_business_words(self):
        for sample in ("touch_plan = 1", "x = 'acceptance'", "entry = 'evaluate'",
                       'role = "qa"', 'stage = "verify"', "task_mode = 'design'",
                       "board = []", "看板 = 1"):
            self.assertTrue(matcher_hits(sample), f"漏判：{sample!r}")

    def test_does_not_flag_mechanism_words(self):
        for sample in ("requires", "request", "prefix", 'status = "done"',
                       'kind = "container"', "await_ready", 'terminal = ("done",)'):
            self.assertEqual(matcher_hits(sample), [], f"误判：{sample!r}")

    def test_done_is_an_acknowledged_non_determinable(self):
        """`done` 是机制终态（`core.TERMINAL`）—— 明确记录为不可判定，而非默默漏掉。"""
        self.assertEqual(matcher_hits("done"), [])
        self.assertIn("done", core.TERMINAL, "done 是机制终态，故核心合法出现")
        self.assertNotIn("done", WORD_NEEDLES, "正因如此它不能当业务词针脚")


class LayeringTest(DisflowTestCase):
    def test_core_has_no_business_nouns(self):
        pkg = _package_dir()
        hits: dict[str, list[str]] = {}
        for name in CORE_FILES:
            path = os.path.join(pkg, name)
            self.assertTrue(os.path.isfile(path), f"核心文件缺失：{name}")
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            found = matcher_hits(text)
            if found:
                hits[name] = found
        self.assertEqual(hits, {}, f"核心层出现业务词：{hits}")

    def test_ready_does_not_import_spec(self):
        """依赖方向：核心不许 import 业务层。"""
        with open(os.path.join(_package_dir(), "ready.py"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("from .spec import", text)
        self.assertIn("from .core import", text)

    def test_core_does_not_import_business_modules(self):
        pkg = _package_dir()
        forbidden = ("from .spec ", "from .spec.", "from .model ", "from .state ",
                     "from .gates ", "from .views ", "from .context ", "from .engine ",
                     "from .cli ", "from .import_mgt ")
        offenders: dict[str, list[str]] = {}
        for name in CORE_FILES:
            if name == "core.py":
                continue
            with open(os.path.join(pkg, name), encoding="utf-8") as handle:
                text = handle.read()
            found = [token.strip() for token in forbidden if token in text]
            if found:
                offenders[name] = found
        self.assertEqual(offenders, {}, f"核心层 import 了业务层：{offenders}")


if __name__ == "__main__":
    unittest.main()
