"""disflow · 投影 reducer（事件流 → 状态）。

事件是唯一真相；本模块把事件折叠成投影 `{"trees": {name: {goal, root, nodes, assets}}}`。
未知 kind 一律报错（事件闭集，不静默丢弃）。
"""
from __future__ import annotations

from .model import new_node, next_id, validate_node_spec
from .spec import EVENT_KINDS

ROOT_ID = "root"


def empty() -> dict:
    return {"trees": {}}


def project(events: list[dict]) -> dict:
    state = empty()
    for event in events:
        apply_event(state, event)
    return state


def apply_event(state: dict, event: dict) -> None:
    kind = str(event.get("kind") or "")
    handler = _HANDLERS.get(kind)
    if handler is None:
        raise ValueError(f"未知事件 kind：{kind!r}（闭集：{sorted(EVENT_KINDS)}）")
    handler(state, event)


def _tree(state: dict, name: str) -> dict:
    return state["trees"].setdefault(name, {"goal": "", "root": None, "nodes": {}, "assets": {}})


def _node(state: dict, tree_name: str, node_id: str) -> dict:
    tree = _tree(state, tree_name)
    node = tree["nodes"].get(node_id)
    if node is None:
        raise ValueError(f"节点不存在：{tree_name}/{node_id}")
    return node


def _on_tree_init(state: dict, event: dict) -> None:
    tree = _tree(state, event["slug"])
    tree["goal"] = event.get("goal", "")
    if tree["root"] is None:
        tree["root"] = ROOT_ID
        tree["nodes"][ROOT_ID] = new_node(
            ROOT_ID, parent=None, kind="container", stage="req", role="pm",
            goal=tree["goal"])


def _on_tree_goal(state: dict, event: dict) -> None:
    tree = _tree(state, event["slug"])
    tree["goal"] = event.get("goal", "")
    root = tree["nodes"].get(tree["root"] or ROOT_ID)
    if root is not None:
        root["goal"] = tree["goal"]


def _on_node_expand(state: dict, event: dict) -> None:
    tree_name = event["slug"]
    parent = _node(state, tree_name, event["node"])
    tree = state["trees"][tree_name]
    for spec in event.get("children", []):
        validate_node_spec(spec)
        node_id = spec.get("id") or next_id(tree["nodes"])
        if node_id in tree["nodes"]:
            raise ValueError(f"节点 id 已存在：{node_id}")
        tree["nodes"][node_id] = new_node(
            node_id, parent=parent["id"],
            kind=spec.get("kind", "task"), stage=spec.get("stage", "dev"),
            role=spec.get("role", "dev"), goal=spec.get("goal", ""),
            context=spec.get("context"), entry=spec.get("entry", ""),
            acceptance=spec.get("acceptance"),
            touch_plan=spec.get("touch_plan"), awaits=spec.get("awaits"))


def _append_unique(node: dict, key: str, value: str) -> None:
    bucket = node.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def _on_node_require(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "requires", event["dep"])


def _on_node_relate(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "relates", event["other"])


def _on_asset_produce(state: dict, event: dict) -> None:
    tree = state["trees"][event["slug"]]
    _append_unique(_node(state, event["slug"], event["node"]), "produces", event["name"])
    tree["assets"][event["name"]] = event["node"]


def _on_asset_consume(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "consumes", event["name"])


def _on_node_start(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "running"
    node["lease"] = event.get("lease")
    node["baseline"] = event.get("baseline") or []


def _on_node_collect(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["collected"] = event.get("result")


def _on_node_done(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "done"
    node["lease"] = None
    node["evidence"] = event.get("evidence")


def _on_node_fail(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "failed"
    node["lease"] = None
    node["failure"] = event.get("reason")


def _on_node_retry(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "pending"
    node["lease"] = None


def _on_node_skip(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "skipped"
    node["lease"] = None
    node["skip_reason"] = event.get("reason")


def _on_node_import(state: dict, event: dict) -> None:
    """导入既有状态（如从 mgt）——标记 imported，**不声称经过本引擎的门**。"""
    node = _node(state, event["slug"], event["node"])
    node["status"] = event.get("status", "pending")
    node["imported"] = True
    node["evidence"] = {"imported": True, "note": event.get("note", "")}


def _on_node_context(state: dict, event: dict) -> None:
    """合并节点 context（供后代继承 / 树根声明 closure）。"""
    node = _node(state, event["slug"], event["node"])
    node.setdefault("context", {}).update(event.get("context") or {})


def _on_tree_close(state: dict, event: dict) -> None:
    state["trees"][event["slug"]]["closed"] = True


_HANDLERS = {
    "tree.init": _on_tree_init,
    "tree.goal": _on_tree_goal,
    "node.expand": _on_node_expand,
    "node.require": _on_node_require,
    "node.relate": _on_node_relate,
    "asset.produce": _on_asset_produce,
    "asset.consume": _on_asset_consume,
    "node.start": _on_node_start,
    "node.collect": _on_node_collect,
    "node.done": _on_node_done,
    "node.fail": _on_node_fail,
    "node.retry": _on_node_retry,
    "node.skip": _on_node_skip,
    "node.import": _on_node_import,
    "node.context": _on_node_context,
    "tree.close": _on_tree_close,
}
