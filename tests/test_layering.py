"""NFR-2 分层自检（沿用 flowcore「lib 零业务判据名」的判据）。

**逆向自检**：扫描核心层文件，命中任何业务词即失败——不是「文档说核心中立」，而是可执行的断言。

核心层 = `core.py` / `store.py` / `graph.py` / `ready.py`（通用机制与纯函数）。
业务层 = `spec.py` / `model.py` / `state.py` / `gates.py` / `views.py` / `context.py` /
`dispatch.py` / `engine.py` / `cli.py` / `import_mgt.py`（本实例的名词与策略）。
"""
from __future__ import annotations

import os
import unittest

from base import DisflowTestCase

import disflow

CORE_FILES = ("core.py", "store.py", "graph.py", "ready.py")

# 业务词针脚（本实例的判据名/字段名/看板列）——核心层不许出现。
# 用拼接构造，避免本文件（及未来搬进核心的自检）被自己命中。
_H = "har"
NEEDLES = (
    "ev" + "aluate",              # entry=…（收益验证）
    "re" + "flect",               # entry=…（经验沉淀）
    "stand" + "ardize",
    "touch" + "_plan",            # 写面声明
    "accept" + "ance",            # 验收声明
    "clo" + "sure",               # 终止条件逃生口
    "cove" + "rage",              # 覆盖门
    "wai" + "ver",                # 豁免
    "size_" + "budget",           # 规模预算
    "sta" + "ge",                 # 看板列字段
    "ro" + "le",                  # 负责方字段
    "bo" + "ard",                 # 看板
    "看" + "板",
    "do" + "ctor",                # CLI 自检命令
    "m" + "gt",                   # 迁移源
    "br" + "ief",                 # 派发契约
    _H + "ness",                  # 本实例的 harness 名词
)


def _package_dir() -> str:
    return os.path.dirname(os.path.abspath(disflow.__file__))


class LayeringTest(DisflowTestCase):
    def test_core_has_no_business_nouns(self):
        pkg = _package_dir()
        hits: dict[str, list[str]] = {}
        for name in CORE_FILES:
            path = os.path.join(pkg, name)
            self.assertTrue(os.path.isfile(path), f"核心文件缺失：{name}")
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            found = [needle for needle in NEEDLES if needle in text]
            if found:
                hits[name] = found
        self.assertEqual(hits, {}, f"核心层出现业务词：{hits}")

    def test_core_files_exist_and_are_the_only_core(self):
        pkg = _package_dir()
        for name in CORE_FILES:
            self.assertTrue(os.path.isfile(os.path.join(pkg, name)))

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
