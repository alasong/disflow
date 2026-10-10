"""disflow · 节点模型（树结构原语）。

只做纯函数：节点构造、校验、父子遍历、派生状态。不碰 IO、不碰存储。
树边是 `parent`（唯一父）；这里是树语义的唯一实现处。
"""
from __future__ import annotations

from .spec import KINDS, STORED_STATUS

# 节点在**投影里**的完整字段集
NODE_FIELDS: frozenset[str] = frozenset({
    "id", "parent", "kind", "stage", "role", "goal", "context", "entry",
    "requires", "relates", "produces", "consumes", "touch_plan", "acceptance",
    "awaits", "status", "lease", "evidence",
})

# 运行时/派生字段：由事件流决定，**不允许**在 `expand` 的声明里给
#   - `parent`：由 expand 的父子关系决定（声明另一个父没有意义）
#   - `status` / `lease` / `evidence`：由 `node.start` / `collect` / `done` 等事件驱动
#
# 为何是**拒收**而不是忽略：忽略就是“声明被接受、然后静默丢弃”——同一个 bug 家族
# （`requires` 曾经也被静默丢弃，后果是调度乱序）。要么生效，要么报错。
RUNTIME_FIELDS: frozenset[str] = frozenset({"parent", "status", "lease", "evidence"})

# `expand` 声明可给的字段 = 完整形状 − 运行时字段
DECLARABLE_FIELDS: frozenset[str] = NODE_FIELDS - RUNTIME_FIELDS

_LIST_FIELDS = ("requires", "relates", "produces", "consumes", "touch_plan", "awaits")


def new_node(node_id: str, *, parent: str | None = None, kind: str = "task",
             stage: str = "dev", role: str = "dev", goal: str = "",
             context: dict | None = None, entry: str = "",
             acceptance: dict | None = None,
             touch_plan: list[str] | None = None,
             awaits: list[str] | None = None,
             requires: list[str] | None = None,
             relates: list[str] | None = None,
             produces: list[str] | None = None,
             consumes: list[str] | None = None) -> dict:
    """构造一个 status=pending 的节点（不校验、不入库）。

    四个边字段（`requires`/`relates`/`produces`/`consumes`）**必须在这里收下**：
    它们在 `validate_node_spec` 里是合法声明，若本函数不接，就会"声明被接受、然后静默丢弃"
    —— 实测后果是 `requires` 丢了 ⇒ 调度器**乱序派发**（E1 在 B3/B4 未完工时就被推）。

    `awaits`：跨树完成信号（等待别的树 `closed`）—— 不是图上的 `requires`。
    """
    if kind not in KINDS:
        raise ValueError(f"kind 非法：{kind!r}（应为 {KINDS}）")
    return {
        "id": node_id, "parent": parent, "kind": kind, "stage": stage, "role": role,
        "goal": goal, "context": dict(context or {}), "entry": entry,
        "requires": list(requires or []), "relates": list(relates or []),
        "produces": list(produces or []), "consumes": list(consumes or []),
        "touch_plan": list(touch_plan or []), "awaits": list(awaits or []),
        "acceptance": dict(acceptance) if acceptance else None,
        "status": "pending", "lease": None, "evidence": None,
    }


def validate_node_spec(spec: dict) -> None:
    """校验 expand 传入的子节点声明：字段闭集 + 拒运行时字段 + 取值合法。"""
    unknown = set(spec) - NODE_FIELDS
    if unknown:
        raise ValueError(f"节点声明含未定义字段：{sorted(unknown)}"
                         f"（可声明：{sorted(DECLARABLE_FIELDS)}）")
    runtime = set(spec) & RUNTIME_FIELDS
    if runtime:
        raise ValueError(
            f"这些字段由运行时事件决定，不能在 expand 里声明：{sorted(runtime)}"
            "（声明了也不会生效——`parent` 由 expand 的父子关系决定；"
            "`status`/`lease` 走 start/fail/retry/skip/reap 动词；`evidence` 由 done 自算）")
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
