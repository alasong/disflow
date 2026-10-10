"""disflow · mgt 导入器（D90）。

把 fat3 `skills/mgt` 的 `.fat/**/state.json`（TODO 定义）+ `.fat/registry.json`（全局状态）
映射为 disflow 的树 + 图。映射表见 `docs/design.md` §10。

**要点**：`contains` 依赖丢弃（= `parent`）；`blocks` → `requires`；`informs` → `relates`；
已完成的项经 `node.import` 落库并标记 `imported` —— **不声称经过本引擎的门**。
"""
from __future__ import annotations

import json
import os

_KIND = {"R": "container", "O": "container", "T": "task", "S": "task"}
_STATUS = {"pending": "pending", "ready": "pending", "iterating": "running", "done": "done"}
_GLOBAL_DONE = {"dev_done", "verified", "approved", "closed"}


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_dep(dep) -> dict:
    if isinstance(dep, str):
        return {"id": dep, "type": "blocks"}
    if isinstance(dep, dict):
        return {"id": dep.get("id", ""), "type": dep.get("type", "blocks")}
    return {"id": "", "type": "blocks"}


def load_mgt(project_root: str) -> dict:
    """读 mgt 状态：优先 `.fat/mgt/state.json`，否则任一 `.fat/<org>/state.json`；再读 registry。"""
    candidates = [os.path.join(project_root, ".fat", "mgt", "state.json")]
    fat = os.path.join(project_root, ".fat")
    try:
        names = sorted(os.listdir(fat))
    except OSError:
        names = []
    candidates += [os.path.join(fat, name, "state.json") for name in names]

    todos: list[dict] = []
    source = ""
    for path in candidates:
        data = _read_json(path)
        if isinstance(data, dict) and data.get("todos"):
            todos = data["todos"]
            source = path
            break
    registry = _read_json(os.path.join(project_root, ".fat", "registry.json")) or {}
    return {"todos": todos, "registry": registry, "source": source,
            "project": os.path.basename(os.path.abspath(project_root))}


def build_import(mgt: dict) -> dict:
    """纯变换：mgt 状态 → 导入计划（不改任何状态）。"""
    registry = (mgt.get("registry") or {}).get("todos") or {}
    nodes: list[dict] = []
    requires: list[tuple[str, str]] = []
    relates: list[tuple[str, str]] = []
    dropped: list[str] = []

    for todo in mgt.get("todos", []):
        node_id = str(todo.get("id") or "").strip()
        if not node_id:
            continue
        item_type = str(todo.get("type") or "T").upper()
        global_status = (registry.get(node_id) or {}).get("global_status")
        status = _STATUS.get(str(todo.get("status") or "pending"), "pending")
        if global_status in _GLOBAL_DONE:
            status = "done"
        nodes.append({
            "id": node_id,
            "parent": todo.get("parent") or None,
            "kind": _KIND.get(item_type, "task"),
            "stage": "verify" if global_status in ("verified", "approved") else "dev",
            "role": todo.get("org") or "dev",
            "goal": todo.get("title") or "",
            "entry": todo.get("mode") or "",
            "touch_plan": list(todo.get("files") or []),
            "status": status,
            "context": {"mgt": {"type": item_type, "layer": todo.get("layer"),
                                "domain": todo.get("domain"), "phase": todo.get("phase"),
                                "priority": todo.get("priority"),
                                "global_status": global_status}},
        })
        for raw in todo.get("deps") or []:
            dep = _normalize_dep(raw)
            if dep["type"] == "blocks":
                requires.append((node_id, dep["id"]))
            elif dep["type"] == "informs":
                relates.append((node_id, dep["id"]))
            else:
                dropped.append(f"{node_id} -{dep['type']}-> {dep['id']}")

    return {"goal": f"imported from mgt:{mgt.get('source', '?')}",
            "nodes": nodes, "requires": requires, "relates": relates, "dropped": dropped}


def apply_import(engine, tree: str, plan: dict) -> dict:
    """把导入计划落进引擎。返回统计（含被跳过的边，不静默）。"""
    engine.init_tree(tree, plan["goal"])
    by_parent: dict[str, list[dict]] = {}
    for node in plan["nodes"]:
        by_parent.setdefault(node.get("parent") or "root", []).append(node)

    queue = ["root"]
    created: set[str] = set()
    while queue:
        parent = queue.pop(0)
        children = by_parent.get(parent)
        if not children:
            continue
        specs = [{"id": n["id"], "kind": n["kind"], "stage": n["stage"], "role": n["role"],
                  "goal": n["goal"], "entry": n["entry"], "touch_plan": n["touch_plan"],
                  "context": n["context"]} for n in children]
        engine.expand(tree, parent, specs)
        created.update(n["id"] for n in children)
        queue.extend(n["id"] for n in children)

    orphans = [n["id"] for n in plan["nodes"] if n.get("parent") and n["parent"] not in created]
    skipped_edges: list[str] = []
    for src, dst in plan["requires"]:
        if src in created and dst in created:
            try:
                engine.require(tree, src, dst)
            except Exception as exc:                 # 环/悬空：跳过并报告
                skipped_edges.append(f"{src} requires {dst}: {exc}")
        else:
            skipped_edges.append(f"{src} requires {dst}: 端点缺失")
    for src, dst in plan["relates"]:
        if src in created and dst in created:
            engine.relate(tree, src, dst)

    imported = 0
    for node in plan["nodes"]:
        if node["status"] != "pending" and node["id"] in created:
            engine.import_node(tree, node["id"], node["status"], note="imported from mgt")
            imported += 1

    return {"tree": tree, "nodes": len(created), "edges": len(plan["requires"]),
            "orphans": orphans, "skipped_edges": skipped_edges,
            "imported_done": imported, "dropped_deps": plan["dropped"]}
