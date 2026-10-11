#!/usr/bin/env python3
"""项目资产登记扫描器（P2 索引 + P3 可信依赖）。

读 `assets/registry.json`（手写事实源），机械派生可信度，产出：

    assets/index.jsonl   机读索引：一行一资产（AI 定位用，不必整读代码）
    assets/INDEX.md      薄索引：表 + stale 清单（人/AI grep 用）

子命令（每步可带 `--root <项目根>`，默认 = 本脚本上两级目录）：

    scan            校验注册表 + 生成索引（存在 stale 不报错，退出码 0）
    check           校验注册表 + 存在 stale 或缺证据 → 退出码 1（当门用）
    impact <id>     反查：哪些资产（传递地）把 <id> 当依据

`--root` 是把它当**可复用工具**用的入口（如从 fat3 `skills/asset-registry/tools/` 直接
对任意项目跑）；把本脚本拷进 `<项目>/scripts/` 时用默认值即可。

设计判据见 `assets/README.md`。两条硬约束：

- **无状态文件**：stale 完全由 git 事实（未提交改动 / 提交时间）当场复算，不落基线，
  故 clone 后读数一致、无「忘了刷新基线」这类漂移。
- **输出确定性**：不写生成时间戳（否则每次 scan 都产生无意义 diff）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 路径均相对 ROOT 动态求值（`--root` 会改 ROOT，故不能在这里冻结成常量）

def _registry() -> Path:
    return ROOT / "assets" / "registry.json"


def _index_paths() -> tuple[Path, Path]:
    return ROOT / "assets" / "index.jsonl", ROOT / "assets" / "INDEX.md"

TYPES = {"code", "test", "script", "doc", "board", "report", "config"}
# 语义类别（可选字段）：与 type（文件形态）正交——需求/设计/实现/验证/过程…
CATEGORIES = {"requirement", "design", "implementation", "verification",
              "process", "documentation", "config"}
TIERS = ("verified", "asserted", "stub")
REQUIRED = ("type", "path", "purpose", "tier")


# --------------------------------------------------------------------------- git 事实

def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    return out.stdout


def _files_under(rel: str) -> list[str]:
    """路径覆盖的文件集：tracked + untracked 且未被 .gitignore 忽略。

    用 git 而非 `Path.rglob`：后者会把 `__pycache__`、`.ruff_cache` 这类本地产物算进去，
    使 hash 随「谁在什么时候跑过」而变——那种不稳定性会污染 stale 判定。
    """
    out = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", rel)
    return sorted(f for f in out.split("\0") if f)


def content_hash(rel: str) -> str:
    """工作区内容 hash：文件 = blob；目录 = 各文件 (路径, blob) 聚合。"""
    files = _files_under(rel)
    if not files:
        return ""
    blobs = subprocess.run(
        ["git", "hash-object", "--stdin-paths"], cwd=ROOT,
        input="\n".join(files), capture_output=True, text=True,
    ).stdout.splitlines()
    h = hashlib.sha256()
    for f, b in zip(files, blobs, strict=True):
        h.update(f.encode("utf-8"))
        h.update(b.strip().encode("ascii"))
    return h.hexdigest()[:16]


def last_commit(rel: str) -> int:
    """路径最后一次提交的 Unix 时间；从未提交（新文件）→ 0。"""
    out = _git("log", "-1", "--format=%ct", "--", rel).strip()
    return int(out) if out.isdigit() else 0


def dirty(rel: str) -> bool:
    """路径是否有未提交改动（含未跟踪新文件）。"""
    return bool(_git("status", "--porcelain", "--", rel).strip())


# --------------------------------------------------------------------------- 校验

def _load() -> dict[str, dict]:
    registry = _registry()
    if not registry.exists():
        raise SystemExit(f"registry 不存在：{registry}")
    data = json.loads(registry.read_text(encoding="utf-8"))
    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise SystemExit(f"registry 损坏：{registry} 缺少非空 `assets` 字典")
    return assets


def validate(assets: dict[str, dict]) -> None:
    """字段闭集 + 取值合法 + premises 可解 + 无环。任一失败 → 立刻拒收（退出码 1）。"""
    errors: list[str] = []
    for aid, a in assets.items():
        if not isinstance(a, dict):
            errors.append(f"{aid}: 不是对象")
            continue
        for key in REQUIRED:
            if not a.get(key):
                errors.append(f"{aid}: 缺必填字段 {key}")
        if a.get("type") not in TYPES:
            errors.append(f"{aid}: type 非法 {a.get('type')!r}（应为 {sorted(TYPES)}）")
        if a.get("category") and a["category"] not in CATEGORIES:
            errors.append(f"{aid}: category 非法 {a['category']!r}"
                          f"（应为 {sorted(CATEGORIES)} 或省略）")
        if a.get("tier") not in TIERS:
            errors.append(f"{aid}: tier 非法 {a.get('tier')!r}（应为 {list(TIERS)}）")
        if a.get("tier") == "verified" and not a.get("evidence"):
            errors.append(f"{aid}: tier=verified 必须给 evidence（可跑命令）")
        path = a.get("path")
        if isinstance(path, str) and path and not (ROOT / path).exists():
            errors.append(f"{aid}: path 不存在 {path!r}")
        for p in a.get("premises") or []:
            if p == aid:
                errors.append(f"{aid}: premise 自环")
            elif p not in assets:
                errors.append(f"{aid}: premise 指向未登记资产 {p!r}")

    for cycle in _cycles(assets):
        errors.append(f"premises 成环：{' → '.join(cycle)}")
    if errors:
        raise SystemExit("registry 校验失败：\n  - " + "\n  - ".join(errors))


def _cycles(assets: dict[str, dict]) -> list[list[str]]:
    """premises 图上的环（DFS 三色）。成环的两条断言「互为依据」会双双永不 stale。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(assets, WHITE)
    found: list[list[str]] = []

    def visit(node: str, stack: list[str]) -> None:
        color[node] = GREY
        stack.append(node)
        for nxt in assets.get(node, {}).get("premises") or []:
            if nxt not in assets:
                continue
            if color[nxt] == GREY:
                found.append(stack[stack.index(nxt):] + [nxt])
            elif color[nxt] == WHITE:
                visit(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for node in assets:
        if color[node] == WHITE:
            visit(node, [])
    return found


# --------------------------------------------------------------------------- 派生

def derive(assets: dict[str, dict]) -> list[dict]:
    """把声明折叠成索引行：附加 hash / dirty / last_commit / stale_basis / trust。"""
    rows: list[dict] = []
    for aid in sorted(assets):
        a = assets[aid]
        rows.append({
            "id": aid,
            "type": a["type"],
            "category": a.get("category", ""),
            "path": a["path"],
            "purpose": a["purpose"],
            "tier": a["tier"],
            "evidence": a.get("evidence", ""),
            "premises": list(a.get("premises") or []),
            "hash": content_hash(a["path"]),
            "dirty": dirty(a["path"]),
            "last_commit": last_commit(a["path"]),
            "stale_basis": [],
        })
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        basis = []
        for p in r["premises"]:
            pr = by_id[p]
            # 新资产（自身未提交）不判 stale：它尚无「被超越的基线」。
            # 依据物脏（未提交改动）或比本资产更晚提交 → 本资产的断言可能已失真。
            if pr["dirty"] or (r["last_commit"] and pr["last_commit"] > r["last_commit"]):
                basis.append(p)
        r["stale_basis"] = basis
        r["trust"] = "stale" if basis else r["tier"]
    return rows


def reverse_impact(assets: dict[str, dict], target: str) -> list[str]:
    """谁（传递地）把 target 当依据——改 target 后要复查的面。"""
    direct: dict[str, set[str]] = defaultdict(set)
    for aid, a in assets.items():
        for p in a.get("premises") or []:
            direct[p].add(aid)
    seen: set[str] = set()
    stack = list(direct[target])
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        stack.extend(direct[x])
    return sorted(seen)


# --------------------------------------------------------------------------- 渲染

def _row_line(r: dict) -> str:
    return (f"| `{r['id']}` | {r['type']} | {r['tier']} | {r['trust']} | "
            f"`{r['path']}` | {r['purpose']} |")


def render_md(rows: list[dict]) -> str:
    stale = [r for r in rows if r["trust"] == "stale"]
    lines = [
        "# 项目资产索引",
        "",
        "> 由 `scripts/assets_scan.py` 生成，**勿手改**；事实源 `assets/registry.json`。",
        "> 无生成时间戳（输出确定性，避免无谓 diff）。",
        f"> 资产 {len(rows)} 条 · stale {len(stale)} 条。",
        "",
        "| id | type | tier | trust | path | purpose |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [_row_line(r) for r in rows]
    lines += ["", "## 按类别（需求 / 设计 / 实现 / 验证 / 过程…）", "",
              "| 类别 | 资产 |", "| --- | --- |"]
    by_cat: dict[str, list[str]] = {}
    for r in rows:
        by_cat.setdefault(r["category"] or "(未分类)", []).append(r["id"])
    for cat in sorted(by_cat):
        lines.append(f"| {cat} | {', '.join('`' + i + '`' for i in sorted(by_cat[cat]))} |")
    lines += ["", "## stale（依据物已变，结论可能失真）", ""]
    if not stale:
        lines.append("（无）")
    else:
        lines += ["| id | 依据 stale_basis | 说明 |", "| --- | --- | --- |"]
        lines += [f"| `{r['id']}` | {', '.join('`' + p + '`' for p in r['stale_basis'])} | "
                  f"{r['purpose']} |" for r in stale]
    lines += ["", "## premises 图（依据 → 依赖它的资产）", ""]
    from collections import defaultdict as _dd
    incoming: dict[str, list[str]] = _dd(list)
    for r in rows:
        for p in r["premises"]:
            incoming[p].append(r["id"])
    if not incoming:
        lines.append("（无）")
    else:
        lines += ["| 依据 | 依赖它的资产 |", "| --- | --- |"]
        for p in sorted(incoming):
            lines.append(f"| `{p}` | {', '.join('`' + c + '`' for c in sorted(incoming[p]))} |")
    return "\n".join(lines) + "\n"


def _write(rows: list[dict]) -> None:
    index_jsonl, index_md = _index_paths()
    index_jsonl.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n",
        encoding="utf-8")
    index_md.write_text(render_md(rows), encoding="utf-8")


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="项目资产登记扫描器（scan/check/impact）")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=None,
                        help="项目根（默认=本脚本上两级目录；skill 内使用时必须给）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", parents=[common], help="校验并生成索引")
    sub.add_parser("check", parents=[common], help="校验；有 stale 或缺证据则退出码 1")
    imp = sub.add_parser("impact", parents=[common], help="反查依赖指定资产的面")
    imp.add_argument("id")
    args = parser.parse_args(argv)

    global ROOT
    if args.root:
        ROOT = Path(args.root).resolve()

    assets = _load()
    validate(assets)

    if args.cmd == "impact":
        if args.id not in assets:
            raise SystemExit(f"未登记资产：{args.id}（已登记：{sorted(assets)}）")
        hits = reverse_impact(assets, args.id)
        if not hits:
            print(f"{args.id}：无资产以它为依据")
        else:
            print(f"{args.id} 的影响面（{len(hits)} 条，改它后要复查）：")
            for h in hits:
                print(f"  - {h}（{assets[h]['purpose']}）")
        return 0

    rows = derive(assets)
    if args.cmd == "scan":
        _write(rows)
        index_jsonl, index_md = _index_paths()
        print(f"✓ 已写 {index_jsonl.relative_to(ROOT)} 与 {index_md.relative_to(ROOT)}"
              f"（{len(rows)} 条资产）")

    stale = [r for r in rows if r["trust"] == "stale"]
    no_evidence = [r["id"] for r in rows if r["tier"] == "verified" and not r["evidence"]]
    if stale:
        print(f"⚠ {len(stale)} 条 stale（依据物已变）：")
        for r in stale:
            print(f"  - {r['id']} ← {', '.join(r['stale_basis'])}")
    if no_evidence:
        print(f"✗ 声称 verified 但缺 evidence：{no_evidence}")
    if args.cmd == "check" and (stale or no_evidence):
        return 1
    if args.cmd == "check":
        print("✓ 资产登记一致，无 stale")
    return 0


if __name__ == "__main__":
    sys.exit(main())
