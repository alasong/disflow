#!/usr/bin/env python3
"""文档章节定位器 —— 「先看节点，再读段落」的确定性工具（零 token 查找）。

**为什么要有它**（不是形式主义）：本仓文档成篇整读很贵（`docs/design.md` 948 行/37 节、
`self-harden-evaluation.md` 739/29…），而 `read` 是整文件注入。代码侧早有
`symbol_locate.py`（符号 → file:start-end）来避免整读，文档侧却只有 `docs/INDEX.md`
的**文档级**一句话 —— 想读某一节只能整读或临场 `grep '^#'`，不可复现、fresh agent 不会做。

本工具把「文档的章节」变成和「代码的符号」同一种东西：确定性、可复现、一条命令拿到
`path:start-end`，直接喂 `read offset/limit` 窗口读。

用法
----
    doc_outline.py <doc.md>                  # 大纲：每条一行 `L<start>-<end>  <级别>  <标题>`
    doc_outline.py <doc.md> <标题子串>        # 解析到一节：输出 `<doc>:<start>-<end>`（喂 read）
    doc_outline.py --json <doc.md>           # 机读：{"path":..., "sections":[...]}
    doc_outline.py --all                     # 所有已登记文档的大纲
    doc_outline.py --write                   # 重生成 docs/outline.jsonl（章节级索引）
    doc_outline.py --check                   # 校验 docs/outline.jsonl 与文档同步；漂移→exit 1

退出码：0=命中/一致，1=未命中/漂移，2=用法错误。零第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTLINE = ROOT / "docs" / "outline.jsonl"

# ATX 标题：1-6 个 `#` + 空格 + 文本。`#` 后有空格才是标题（`#tag` 不算）。
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def sections_of(text: str) -> list[dict]:
    """把 markdown 文本切成章节：每条 {level, title, start, end}（1-based，闭区间）。

    `end` = 下一个「级别 ≤ 本级」的标题的上一行；没有则到文末。这样每节覆盖其子树，
    窗口读一节不会漏掉它的子节。
    """
    lines = text.splitlines()
    heads: list[tuple[int, int, str]] = []  # (行号, 级别, 标题)
    for i, line in enumerate(lines, start=1):
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    out: list[dict] = []
    for idx, (start, level, title) in enumerate(heads):
        end = len(lines)
        for later_start, later_level, _ in heads[idx + 1:]:
            if later_level <= level:
                end = later_start - 1
                break
        out.append({"level": level, "title": title, "start": start, "end": end})
    return out


def _read_doc(path: Path) -> list[dict]:
    return sections_of(path.read_text(encoding="utf-8"))


def _registered_docs() -> list[Path]:
    """`docs/` 下（递归）每个 .md + 仓根 README.md —— 与 docs/INDEX.md 的登记集对齐。"""
    docs = sorted((ROOT / "docs").rglob("*.md"))
    readme = ROOT / "README.md"
    if readme.is_file():
        docs.append(readme)
    return docs


def _rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def _render(doc: Path, secs: list[dict]) -> str:
    lines = [f"# {_rel(doc)}"]
    for s in secs:
        lines.append(f"L{s['start']}-{s['end']}  {'#' * s['level']} {s['title']}")
    return "\n".join(lines)


def _find(secs: list[dict], needle: str) -> dict | None:
    """标题子串匹配（大小写不敏感）；多命中取最浅（顶层）那条。"""
    low = needle.lower()
    hits = [s for s in secs if low in s["title"].lower()]
    if not hits:
        return None
    return min(hits, key=lambda s: (s["level"], s["start"]))


def _build_index() -> list[dict]:
    rows = []
    for doc in _registered_docs():
        rows.append({"path": _rel(doc), "sections": _read_doc(doc)})
    return rows


def _load_index() -> list[dict]:
    if not OUTLINE.is_file():
        return []
    return [json.loads(line) for line in OUTLINE.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="文档章节定位器（大纲 / 解析 / 索引）")
    p.add_argument("doc", nargs="?", help="目标 markdown 文件")
    p.add_argument("heading", nargs="?", help="标题子串；给了就只输出该节的 start-end")
    p.add_argument("--json", action="store_true", help="机读输出")
    p.add_argument("--all", action="store_true", help="所有已登记文档的大纲")
    p.add_argument("--write", action="store_true", help="重生成 docs/outline.jsonl")
    p.add_argument("--check", action="store_true", help="校验 docs/outline.jsonl 是否与文档同步")
    args = p.parse_args(argv)

    if args.write:
        rows = _build_index()
        OUTLINE.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(f"已写 {_rel(OUTLINE)}（{len(rows)} 篇）")
        return 0

    if args.check:
        on_disk = _load_index()
        fresh = _build_index()
        if on_disk != fresh:
            stale = [r["path"] for r in fresh
                     if next((d for d in on_disk if d["path"] == r["path"]), None) != r]
            print(f"✗ {_rel(OUTLINE)} 与文档不同步（{len(stale)} 篇漂移）：{stale}")
            print("  修法：python3 scripts/doc_outline.py --write")
            return 1
        print(f"✓ {_rel(OUTLINE)} 与 {len(fresh)} 篇文档同步")
        return 0

    if args.all:
        for doc in _registered_docs():
            print(_render(doc, _read_doc(doc)))
        return 0

    if not args.doc:
        p.print_usage(sys.stderr)
        return 2

    doc = (ROOT / args.doc) if not Path(args.doc).is_absolute() else Path(args.doc)
    if not doc.is_file():
        print(f"✗ 文档不存在：{args.doc}", file=sys.stderr)
        return 1
    secs = _read_doc(doc)
    if not secs:
        print(f"✗ {args.doc} 没有标题（无法定位章节）", file=sys.stderr)
        return 1

    if args.heading:
        hit = _find(secs, args.heading)
        if hit is None:
            print(f"✗ 未命中标题 '{args.heading}'；可用："
                  + " | ".join(s["title"] for s in secs), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps({"path": _rel(doc), **hit}, ensure_ascii=False))
        else:
            print(f"{_rel(doc)}:{hit['start']}-{hit['end']}")
        return 0

    if args.json:
        print(json.dumps({"path": _rel(doc), "sections": secs}, ensure_ascii=False))
    else:
        print(_render(doc, secs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
