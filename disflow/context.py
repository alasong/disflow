"""disflow · 上下文投影 `brief(n)`。

上下文**只走树**（祖先链，唯一、有限）；前置产出摘要走图（`requires` 的 done 节点）；
`relates` 只给引用句柄，不递归展开。
"""
from __future__ import annotations

from .model import ancestor_chain


def inherited_context(nodes: dict, node_id: str) -> dict:
    """祖先链 context 浅合并（近祖先覆盖远祖先），**并入节点自身的 context**（自身覆盖全部祖先）。

    为何要并入自身（实测缺陷）：`disflow context --set <node>` 把数据写进**节点自己**的
    context，而本函数原先只走 `ancestor_chain`（**不含自身**）⇒ 对一个叶子节点
    `--set` 后，`brief` 里的 context 仍是空的：写进去了、却到不了 worker。
    原先两个测试都只往**祖先**上放 context（`test_inherited_context_near_overrides_far`
    的 `r`/`a`），没有任何测试往**被 brief 的节点自身**放 ⇒ 缺口一直没被看见。

    优先级：自身 > 近祖先 > 远祖先（同一 key 后者被前者覆盖）。

    为何自身优先级最高：祖先的 context 是「给后代的背景」，自身的是「这个节点特有的输入」，
    具体应压过泛化。
    """
    merged: dict = {}
    for node in ancestor_chain(nodes, node_id):
        merged.update(node.get("context") or {})
    own = nodes.get(node_id) or {}
    merged.update(own.get("context") or {})
    return merged


def brief(nodes: dict, node_id: str) -> dict:
    """生成 worker 的输入契约：目标 + 继承上下文 + 前置产出摘要 + 引用 + 验收。"""
    node = nodes.get(node_id)
    if node is None:
        raise KeyError(f"节点不存在：{node_id}")

    prereqs = []
    for dep in node.get("requires") or []:
        target = nodes.get(dep)
        if target is None:
            prereqs.append({"id": dep, "missing": True})
            continue
        if target.get("status") in ("done", "skipped"):
            prereqs.append({"id": dep, "goal": target.get("goal", ""),
                            "produces": target.get("produces") or [],
                            "evidence": target.get("evidence")})

    return {
        "id": node_id,
        "goal": node.get("goal", ""),
        "kind": node.get("kind"),
        "stage": node.get("stage"),
        "role": node.get("role"),
        "entry": node.get("entry", ""),
        "context": inherited_context(nodes, node_id),
        "from_requires": prereqs,
        "relates": list(node.get("relates") or []),
        "consumes": list(node.get("consumes") or []),
        "acceptance": node.get("acceptance"),
        "touch_plan": list(node.get("touch_plan") or []),
    }
