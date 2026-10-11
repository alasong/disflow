#!/usr/bin/env python3
"""跨体系对账（**只读**）：过程树（`events.jsonl` → 投影） vs 产物登记（`registry.json`）。

两个体系各有一根真相源 —— 过程体系的真相是 `disflow.state.project` 折出来的投影（节点带
`produces`/`touch_plan`），产物体系的真相是 `assets/registry.json`（资产带 `owner`/`tier`/
`spec`）。二者之间的**对账线**（谁交付了哪个资产、资产归哪个节点）目前没有任何机械检查，
于是「资产登记说交付了 X，过程树上却没人产出 X」这类**静默断裂**只能靠人肉发现。

本脚本**只读**两边的**事实**（事件流 + 注册表），报**跨体系差异**，不写回任何一边、不落基线、
不取锁。退出码：0=一致，1=有差异。

MVP 检查项（每条给「证据（读到的什么）+ 判据（为什么是差异）」）：

- **C1 owner 悬空**：`registry` 里资产设了 `owner`（形如 `"<tree>/<node>"` 或 `"<tree>"`），
  但投影里没有该 tree / node —— 登记断言指向了一个过程树上不存在的落点。
- **C2 produces 未登记**：节点 `produces` 的名字在 registry 里没有对应 id —— 过程体系记录了
  一次产出，产物体系却不认识它。
- **C3 Spec 交付不达标**：带 `spec` 块的资产，其 `delivers` 中 `tier != verified` 或
  `trust == stale` —— Spec 声称管全量交付，但交付物本身没验证过（或结论已失真）。
- **C4 Spec 无过程链接**：某 Spec 的 `delivers` 里没有任何资产带 `owner`，且没有任何节点
  `produces` 它的 deliverable —— 这个 Spec 悬在两个账本中间，没有任何一线把它连到过程树。
- **C5 闭合后资产未复查**：对每个 `closed` 的 tree，其节点 `touch_plan` 路径经
  `assets_for_paths` 映射到的资产若 `trust == stale` —— 树都关了，它声称改动的资产结论却
  已失真，没人回头复查。

设计判据：**读事实、不重算**。可信度（`trust`/`stale`）与路径映射（`assets_for_paths`）**复用**
`scripts/assets_scan.py` 的既有实现，本脚本不另立一份 —— 同一概念只有一处拼写（见 AGENTS 的
「一份入口索引」纪律）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from disflow import store  # noqa: E402
from disflow.state import project  # noqa: E402
from disflow.vocab import STORAGE  # noqa: E402


def _load_scan():
    """把 `scripts/assets_scan.py` 当模块装载（脚本目录不在包里，用路径加载更稳）。"""
    path = Path(__file__).resolve().parent / "assets_scan.py"
    spec = importlib.util.spec_from_file_location("_reconcile_assets_scan", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != "__main__"` ⇒ 不跑它的 CLI
    return module


scan = _load_scan()


# --------------------------------------------------------------------------- 读事实

def load_state() -> dict:
    """过程体系的事实：事件流是真相，投影由它重放（`state.json` 只是派生缓存）。

    优先重放 `events.jsonl`（真相源）；事件流为空（全新环境）时回落读 `state.json`。两条路径
    都**只读**——不触发任何写盘。
    """
    events = store.load_events(STORAGE)
    if events:
        return project(events)
    return store.read_json(store.projection_path(STORAGE))


def _load_registry() -> dict:
    """产物体系的事实：手写注册表。"""
    return scan._load()


def _nodes(state: dict):
    """遍历投影里的全部节点，产出 `(tree_name, tree, node_id, node)`，确定性排序。"""
    for tree_name in sorted(state.get("trees") or {}):
        tree = state["trees"][tree_name]
        for node_id in sorted(tree.get("nodes") or {}):
            yield tree_name, tree, node_id, tree["nodes"][node_id]


def _finding(check: str, subject: str, evidence: str, judgment: str) -> dict:
    return {"check": check, "id": subject, "evidence": evidence, "judgment": judgment}


# --------------------------------------------------------------------------- 检查项

def _check_owner_dangling(assets: dict, state: dict) -> list[dict]:
    """C1：`owner` 指向投影里不存在的 tree/node。"""
    trees = state.get("trees") or {}
    out: list[dict] = []
    for aid in sorted(assets):
        owner = assets[aid].get("owner")
        if not owner:
            continue
        owner = str(owner)
        tree_name, _, node_id = owner.partition("/")
        if tree_name not in trees:
            out.append(_finding(
                "C1", aid, f"owner={owner!r}",
                f"投影里没有 tree {tree_name!r}（owner 悬空）"))
        elif node_id and node_id not in (trees[tree_name].get("nodes") or {}):
            out.append(_finding(
                "C1", aid, f"owner={owner!r}",
                f"tree {tree_name!r} 里没有 node {node_id!r}（owner 悬空）"))
    return out


def _check_produces_unregistered(assets: dict, state: dict) -> list[dict]:
    """C2：节点 `produces` 的名字在 registry 里没有对应 id。"""
    registered = set(assets)
    out: list[dict] = []
    for tree_name, _tree, node_id, node in _nodes(state):
        for name in node.get("produces") or []:
            if name not in registered:
                out.append(_finding(
                    "C2", str(name),
                    f"{tree_name}/{node_id} produces {name!r}",
                    "registry 无对应资产 id（过程记录了产出，产物体系不认识）"))
    return out


def _check_spec_delivery(assets: dict, rows: list[dict]) -> list[dict]:
    """C3：Spec 的 `delivers` 中 tier != verified 或 trust == stale。"""
    trust_of = {r["id"]: r.get("trust", r.get("tier")) for r in rows}
    out: list[dict] = []
    for sid in sorted(assets):
        spec = assets[sid].get("spec")
        if not isinstance(spec, dict):
            continue
        for did in spec.get("delivers") or []:
            if did not in assets:
                continue                     # 悬空 deliver 由 registry 校验另管，这里只对已登记
            tier = assets[did].get("tier")
            trust = trust_of.get(did, tier)
            if tier != "verified" or trust == "stale":
                out.append(_finding(
                    "C3", sid, f"delivers {did!r} tier={tier} trust={trust}",
                    "交付未达 verified，或依据物已变（stale）"))
    return out


def _check_spec_unlinked(assets: dict, state: dict) -> list[dict]:
    """C4：Spec 的 deliverable 既无 `owner`、也无任何节点 `produces`。"""
    produced: set[str] = set()
    for _tn, _tree, _nid, node in _nodes(state):
        produced.update(node.get("produces") or [])
    out: list[dict] = []
    for sid in sorted(assets):
        spec = assets[sid].get("spec")
        if not isinstance(spec, dict):
            continue
        delivers = [d for d in (spec.get("delivers") or []) if d in assets]
        if not delivers:
            continue
        has_owner = any(assets[d].get("owner") for d in delivers)
        has_produces = any(d in produced for d in delivers)
        if not has_owner and not has_produces:
            out.append(_finding(
                "C4", sid, f"delivers={delivers} 无 owner、无 produces",
                "Spec 未链接到过程（两个账本之间一根线都没接）"))
    return out


def _check_closed_assets_stale(assets: dict, rows: list[dict], state: dict) -> list[dict]:
    """C5：已关闭 tree 的节点 `touch_plan` 命中的资产若 stale。"""
    trust_of = {r["id"]: r.get("trust", r.get("tier")) for r in rows}
    trees = state.get("trees") or {}
    out: list[dict] = []
    for tree_name in sorted(trees):
        tree = trees[tree_name]
        if not tree.get("closed"):
            continue
        for node_id in sorted(tree.get("nodes") or {}):
            touch_plan = tree["nodes"][node_id].get("touch_plan") or []
            if not touch_plan:
                continue
            for aid in scan.assets_for_paths(assets, touch_plan):
                if trust_of.get(aid) == "stale":
                    out.append(_finding(
                        "C5", aid,
                        f"closed tree {tree_name} 的 {node_id} touch_plan 命中 {aid}",
                        "树已关闭但该资产结论已失真（stale），需复查"))
    return out


def build_findings(assets: dict, rows: list[dict], state: dict) -> list[dict]:
    """跑全部检查项，按 (check, id) 排序返回差异清单（纯函数，无 IO）。"""
    findings: list[dict] = []
    findings += _check_owner_dangling(assets, state)
    findings += _check_produces_unregistered(assets, state)
    findings += _check_spec_delivery(assets, rows)
    findings += _check_spec_unlinked(assets, state)
    findings += _check_closed_assets_stale(assets, rows, state)
    return sorted(findings, key=lambda f: (f["check"], f["id"]))


# --------------------------------------------------------------------------- 渲染 + CLI

def _render(findings: list[dict]) -> str:
    if not findings:
        return "✓ 跨体系对账一致：过程树与资产登记无差异"
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["check"]] = counts.get(f["check"], 0) + 1
    summary = "、".join(f"{c}×{counts[c]}" for c in sorted(counts))
    lines = [f"✗ 跨体系对账：{len(findings)} 条差异（{summary}）"]
    for f in findings:
        lines.append(f"  [{f['check']}] {f['id']}：{f['evidence']} —— {f['judgment']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="跨体系只读对账：过程树（events→投影） vs 资产登记（registry）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv)

    assets = _load_registry()
    rows = scan.derive(assets)
    state = load_state()
    findings = build_findings(assets, rows, state)

    if args.json:
        print(json.dumps(
            {"ok": not findings, "count": len(findings), "findings": findings},
            ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render(findings))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
