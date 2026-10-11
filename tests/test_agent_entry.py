"""AI 入口守卫：常驻的项目指令文件存在，且指向两份索引、指针不失效。

**为什么要有它**（真实故障，非形式主义）：一次被问「disflow 看板数据结构是什么」，
AI 直接从 `ls` + 读源码起手，**没读 `assets/INDEX.md` / `docs/INDEX.md`** —— 而本仓
纪律正是「不登记 = 永不被读到」。病根不在索引缺失，而在**没有常驻文件指向索引**：
仓里没有项目级 `AGENTS.md`，入口指针自己没有入口（详见 `docs/decisions/0004-...md`）。

本守卫把「入口指针存在且有效」变成机检。它**只管指针，不管行为** —— 无法保证 AI 真去读；
那层靠 `AGENTS.md` 常驻 + 纪律（ADR 0004 已如实登记这条边界）。

判据（集合/存在性，不是「文件里有没有某个词」的模糊匹配）：
1. 仓根至少有 `AGENTS.md` 或 `CLAUDE.md` 之一（pi 只认仓根/父目录的这两个名字）；
2. 这些常驻文件合起来**提到了** `docs/INDEX.md` 与 `assets/INDEX.md` 两份索引；
3. 被提到的索引路径**真实存在**（指针不失效）。
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

# pi 的 context-file 发现只认这两种名字（见 pi docs/configuration.md#context-files）。
ENTRY_NAMES = ("AGENTS.md", "CLAUDE.md")
# 本仓发布的两份入口索引：人的入口 + 机器的入口。
INDEXES = ("docs/INDEX.md", "assets/INDEX.md")


class AgentEntryTest(unittest.TestCase):
    def _entry_files(self) -> list[pathlib.Path]:
        return [ROOT / name for name in ENTRY_NAMES if (ROOT / name).is_file()]

    def test_has_always_loaded_entry_file(self):
        self.assertTrue(
            self._entry_files(),
            f"仓根缺常驻入口文件（{ENTRY_NAMES} 至少一个）——没有它，索引就没有入口指针"
            "（见 docs/decisions/0004-ai-入口先读索引.md）")

    def test_entry_points_to_both_indexes(self):
        text = "\n".join(p.read_text(encoding="utf-8") for p in self._entry_files())
        missing = [idx for idx in INDEXES if idx not in text]
        self.assertEqual(
            missing, [],
            f"常驻入口文件没指向这些索引：{missing}——AI 就不知道要先定位（见 ADR 0004）")

    def test_pointed_indexes_exist(self):
        for idx in INDEXES:
            self.assertTrue((ROOT / idx).is_file(), f"入口指向的索引不存在（死指针）：{idx}")


if __name__ == "__main__":
    unittest.main()
