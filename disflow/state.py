"""disflow · 投影 reducer（事件流 → 状态）。

事件是唯一真相；本模块把事件折叠成投影 `{"trees": {name: {goal, root, nodes, assets}}}`。
未知 kind 一律报错（事件闭集，不静默丢弃）。
"""
from __future__ import annotations

from . import graph
from .model import new_node, next_id, validate_node_spec

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
        # 报错文本用 `_HANDLERS`（**真正可派发**的集合），不用 `EVENT_KINDS`：
        # 后者是 spec 里的声明，两者可漂移；测试 `test_event_kinds.py` 把它钉住。
        raise ValueError(f"未知事件 kind：{kind!r}（可派发：{sorted(_HANDLERS)}）")
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
    pending: list[tuple[dict, str]] = []
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
            touch_plan=spec.get("touch_plan"), awaits=spec.get("awaits"),
            requires=spec.get("requires"), relates=spec.get("relates"),
            produces=spec.get("produces"), consumes=spec.get("consumes"))
        pending.append((spec, node_id))
    # 两阶段 + 分相：先建齐**所有**节点，再接线。否则同批兄弟之间互相声明（B produces X、
    # A consumes X；或 B requires A）会被误判为「悬空」。分相让同批内的声明顺序无关。
    _link_relates(tree, pending)
    _link_produces(tree, pending)
    _link_consumes(tree, pending)
    _link_requires(tree, pending)


def _declared(spec: dict, key: str) -> list[str]:
    return [str(item) for item in (spec.get(key) or [])]


def _link_relates(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`relates` 是软边（可成环）—— 只查两端存在。"""
    for spec, node_id in pending:
        for other in _declared(spec, "relates"):
            if other not in tree["nodes"]:
                raise ValueError(f"{node_id} relates 悬空：{other}")
            _append_unique(tree["nodes"][node_id], "relates", other)


def _link_produces(tree: dict, pending: list[tuple[dict, str]]) -> None:
    for spec, node_id in pending:
        for name in _declared(spec, "produces"):
            _append_unique(tree["nodes"][node_id], "produces", name)
            tree["assets"][name] = node_id


def _link_consumes(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`consumes` 必须指向已登记资产（与 `consume` 动词同一判据）——故在 produces 之后跑。"""
    for spec, node_id in pending:
        for name in _declared(spec, "consumes"):
            if name not in tree["assets"]:
                raise ValueError(f"{node_id} consumes 未登记资产：{name}")
            _append_unique(tree["nodes"][node_id], "consumes", name)


def _link_requires(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`requires` 是调度边 —— 与 `require` 动词同一套校验：拒悬空 / 自环 / 成环。"""
    for spec, node_id in pending:
        for dep in _declared(spec, "requires"):
            if dep not in tree["nodes"]:
                raise ValueError(f"{node_id} requires 悬空：{dep}")
            if dep == node_id:
                raise ValueError(f"{node_id} 不能依赖自身")
            if graph.would_cycle(tree["nodes"], node_id, dep):
                raise ValueError(f"加边即成环：{node_id} requires {dep}")
            _append_unique(tree["nodes"][node_id], "requires", dep)


def _append_unique(node: dict, key: str, value: str) -> None:
    bucket = node.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def _on_node_require(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "requires", event["dep"])


def _on_node_unrequire(state: dict, event: dict) -> None:
    """删 `requires` 边（建错依赖的合法出口）。

    边不存在即 `ValueError`：`project` 是**语义闸门**——非法事件既不许落盘也不许重放。
    有了这条，被拒的 unrequire 不会在事件流里留下痕（与 `store._persist` 的先试算一致）。
    """
    node = _node(state, event["slug"], event["node"])
    dep = str(event["dep"])
    if dep not in (node.get("requires") or []):
        raise ValueError(f"{event['node']} 没有 requires 边：{dep}")
    node["requires"] = [item for item in node["requires"] if item != dep]


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
    raw = event.get("baseline") or {}
    # 基线统一为 {路径: 内容指纹}；旧事件（路径列表）归一为 None 指纹 ⇒ 一律判为已改（保守）
    node["baseline"] = raw if isinstance(raw, dict) else dict.fromkeys(raw)


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


def _on_node_rescope(state: dict, event: dict) -> None:
    """改节点的写面声明：`touch_plan` 换成新值，并把**变更历史**留在 `rescope_log`。

    历史必须留：否则“计划先于改动”这个前提交代不清，事后无法审计
    （谁在什么时候把范围改宽了、理由是什么）。
    """
    node = _node(state, event["slug"], event["node"])
    old = list(node.get("touch_plan") or [])
    node["touch_plan"] = list(event.get("touch_plan") or [])
    node.setdefault("rescope_log", []).append({
        "from": old, "to": list(node["touch_plan"]),
        "reason": event.get("reason") or "",
    })


def _on_tree_close(state: dict, event: dict) -> None:
    state["trees"][event["slug"]]["closed"] = True


def _on_node_reclaim(state: dict, event: dict) -> None:
    """回收过期租约：回到 pending，保留回收原因（可审计）。"""
    node = _node(state, event["slug"], event["node"])
    node["status"] = "pending"
    node["lease"] = None
    node["reclaimed"] = event.get("reason", "")


_HANDLERS = {
    "tree.init": _on_tree_init,
    "tree.goal": _on_tree_goal,
    "node.expand": _on_node_expand,
    "node.require": _on_node_require,
    "node.unrequire": _on_node_unrequire,
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
    "node.rescope": _on_node_rescope,
    "tree.close": _on_tree_close,
    "node.reclaim": _on_node_reclaim,
}
