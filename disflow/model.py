"""disflow · 节点模型（树结构原语）。

只做纯函数：节点构造、校验、父子遍历、派生状态。不碰 IO、不碰存储。
树边是 `parent`（唯一父）；这里是树语义的唯一实现处。
"""
from __future__ import annotations

from .spec import KINDS, STORED_STATUS

# 节点字段闭集（expand 输入多出的键即报错）
NODE_FIELDS: frozenset[str] = frozenset({
    "id", "parent", "kind", "stage", "role", "goal", "context", "entry",
    "requires", "relates", "produces", "consumes", "touch_plan", "acceptance",
    "status", "lease", "evidence",
})

_LIST_FIELDS = ("requires", "relates", "produces", "consumes", "touch_plan")


def new_node(node_id: str, *, parent: str | None = None, kind: str = "task",
             stage: str = "dev", role: str = "dev", goal: str = "",
             context: dict | None = None, entry: str = "",
             acceptance: dict | None = None,
             touch_plan: list[str] | None = None) -> dict:
    """构造一个 status=pending 的节点（不校验、不入库）。"""
    if kind not in KINDS:
        raise ValueError(f"kind 非法：{kind!r}（应为 {KINDS}）")
    return {
        "id": node_id, "parent": parent, "kind": kind, "stage": stage, "role": role,
        "goal": goal, "context": dict(context or {}), "entry": entry,
        "requires": [], "relates": [], "produces": [], "consumes": [],
        "touch_plan": list(touch_plan or []),
        "acceptance": dict(acceptance) if acceptance else None,
        "status": "pending", "lease": None, "evidence": None,
    }


def validate_node_spec(spec: dict) -> None:
    """校验 expand 传入的子节点声明：字段闭集 + 必需键 + 取值合法。"""
    unknown = set(spec) - NODE_FIELDS
    if unknown:
        raise ValueError(f"节点声明含未定义字段：{sorted(unknown)}（允许：{sorted(NODE_FIELDS)}）")
    if spec.get("kind", "task") not in KINDS:
        raise ValueError(f"kind 非法：{spec.get('kind')!r}（应为 {KINDS}）")
    for key in _LIST_FIELDS:
        value = spec.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"节点字段 {key} 应为 list，得到 {type(value).__name__}")


def next_id(nodes: dict) -> str:
    """生成下一个空闲节点 id（n0, n1, …）。确定性：只看现有 id，无随机。"""
    used = set(nodes)
    index = 0
    while f"n{index}" in used:
        index += 1
    return f"n{index}"


def children_of(nodes: dict, node_id: str) -> list[str]:
    return sorted(nid for nid, node in nodes.items() if node.get("parent") == node_id)


def ancestor_chain(nodes: dict, node_id: str) -> list[dict]:
    """root → … → parent 的节点列表（不含自身）。用于上下文继承。

    走 `parent` 单链；若检测到环（数据损坏）则报错而不是死循环。
    """
    chain: list[dict] = []
    seen: set[str] = set()
    current = nodes.get(node_id, {}).get("parent")
    while current:
        if current in seen:
            raise ValueError(f"parent 链成环：{node_id} → … → {current}")
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            raise ValueError(f"{node_id} 的祖先 {current} 不存在")
        chain.append(node)
        current = node.get("parent")
    chain.reverse()
    return chain


def descendants(nodes: dict, node_id: str) -> list[str]:
    out: list[str] = []
    stack = children_of(nodes, node_id)
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children_of(nodes, current))
    return out


def is_container_done(node: dict, nodes: dict) -> bool:
    """容器节点：全部子节点 done（或 skipped）即视为完成（派生量，不落库）。"""
    children = children_of(nodes, node["id"])
    if not children:
        return node.get("status") == "done"
    return all(nodes[c].get("status") in ("done", "skipped") for c in children)


def effective_status(node: dict, nodes: dict) -> str:
    """派生状态：容器由子节点推导，任务用存储态。"""
    if node.get("kind") == "container":
        if is_container_done(node, nodes):
            return "done"
        if any(nodes[c].get("status") == "running" for c in descendants(nodes, node["id"])):
            return "running"
        return "pending"
    return node.get("status", "pending")


def check_status(status: str) -> None:
    if status not in STORED_STATUS:
        raise ValueError(f"status 非法：{status!r}（应为 {STORED_STATUS}）")
