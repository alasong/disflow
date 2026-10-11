"""文档索引守卫：`docs/` 的每一篇都在 `docs/INDEX.md` 登记，且登记不指向不存在的文件。

**为什么要有它**（不是形式主义）：文档「东一块西一块」的病根是**没有单一入口** ——
新加一篇文档谁都不知道、删了没人清账。本守卫把它变成机检：

1. **完整性**：`docs/*.md`（含 `README.md`）每一篇都被索引提及 —— 漏登即红；
2. **无死链**：索引提到的每个 `docs/<x>.md` 都真实存在 —— 删了文件不清账即红；
3. **有条目**：索引里每篇都带 `type`（分类），防止悄悄塞一行没有分类的。

判据是**行为**（集合相等），不是「索引里有没有某个词」。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "docs" / "INDEX.md"

# 只认**带 `docs/` 前缀**的 .md 或裸 `README.md`：散文里的 `design.md` 这类相对提及不算登记
# （否则一句话就会把一篇文档「登记」进来，守卫形同虚设）。
_ENTRY = re.compile(r"`(docs/[A-Za-z0-9_./-]+\.md|README\.md)`")
_TYPES = ("requirements", "design", "reference", "process", "decision", "report")


class DocsIndexTest(unittest.TestCase):
    def _registered(self) -> set[str]:
        return set(_ENTRY.findall(INDEX.read_text(encoding="utf-8")))

    def test_every_doc_is_registered(self):
        docs = {f"docs/{p.name}" for p in sorted((ROOT / "docs").glob("*.md"))}
        docs.add("README.md")
        missing = sorted(docs - self._registered())
        self.assertEqual(missing, [],
                         f"这些文档没在 docs/INDEX.md 登记（新增文档必须登记）：{missing}")

    def test_no_registered_doc_is_missing(self):
        dead = sorted(p for p in self._registered() if not (ROOT / p).is_file())
        self.assertEqual(dead, [], f"docs/INDEX.md 登记了不存在的文档（删了要清账）：{dead}")

    def test_index_registers_itself(self):
        self.assertIn("docs/INDEX.md", self._registered())

    def test_each_entry_names_a_type(self):
        lines = INDEX.read_text(encoding="utf-8").splitlines()
        for doc in sorted(self._registered()):
            row = next((line for line in lines if f"`{doc}`" in line), None)
            self.assertIsNotNone(row, f"{doc} 只在散文里出现，没有清单行")
            assert row is not None
            self.assertTrue(any(f"| {kind} |" in row for kind in _TYPES),
                            f"{doc} 的清单行没写 type（{'/'.join(_TYPES)}）：{row}")


if __name__ == "__main__":
    unittest.main()
