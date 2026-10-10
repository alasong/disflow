"""disflow · 就绪判据（唯一函数）。

`ready` 是**派生量**，不落库。分发器与 worker 共用同一份判定，两面不分叉。
"""
from __future__ import annotations

from .graph import unmet_requires
from .spec import TERMINAL

RUNNING = "running"


def is_ready(nodes: dict, node_id: str) -> bool:
    """任务节点就绪 = pending ∧ 未被租约占用 ∧ requires 全 done。

    容器节点不派发（由子节点推导完成），恒 False。
    """
    node = nodes.get(node_id)
    if node is None:
        raise KeyError(f"节点不存在：{node_id}")
    if node.get("kind") != "task":
        return False
    if node.get("status") != "pending":
        return False
    if node.get("lease"):
        return False
    return not unmet_requires(node, nodes)


def blocked_by(nodes: dict, node_id: str) -> list[str]:
    """列出阻塞该节点的前置（含悬空引用）。"""
    return unmet_requires(nodes[node_id], nodes)


def ready_tasks(nodes: dict) -> list[str]:
    """全部就绪的任务节点（id 升序，确定性）。"""
    return [nid for nid in sorted(nodes) if is_ready(nodes, nid)]


def _touch_pair_overlap(left: str, right: str) -> bool:
    """单对写面冲突：同一路径，或一方是另一方的目录前缀。"""
    a, b = left.rstrip("/"), right.rstrip("/")
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _touches_overlap(a: list[str], b: list[str]) -> bool:
    """写面冲突：任一对路径重合或互为目录前缀。"""
    return any(_touch_pair_overlap(left, right) for left in a for right in b)


def pack_waves(nodes: dict, node_ids: list[str]) -> list[list[str]]:
    """贪心把就绪节点装进波次：同波 `touch_plan` 不重叠（写面互斥）。"""
    waves: list[list[str]] = []
    for nid in node_ids:
        touches = nodes[nid].get("touch_plan") or []
        for wave in waves:
            if not any(_touches_overlap(touches, nodes[other].get("touch_plan") or [])
                       for other in wave):
                wave.append(nid)
                break
        else:
            waves.append([nid])
    return waves


def running_tasks(nodes: dict) -> list[str]:
    return sorted(nid for nid, node in nodes.items()
                  if node.get("kind") == "task" and node.get("status") == RUNNING)


def live_tasks(nodes: dict) -> list[str]:
    """非终态任务节点。"""
    return sorted(nid for nid, node in nodes.items()
                  if node.get("kind") == "task" and node.get("status") not in TERMINAL)


def is_stuck(nodes: dict) -> bool:
    """卡死：还有 **pending** 任务、但既无就绪也无运行（被阻塞 / 成环 / 引用悬空）。

    `failed` 不算卡死 —— 它可见、可 retry；卡死指的是「没有任何东西能推进也不在跑」。
    """
    pending = [nid for nid, node in nodes.items()
               if node.get("kind") == "task" and node.get("status") == "pending"]
    return bool(pending) and not ready_tasks(nodes) and not running_tasks(nodes)


def progress(nodes: dict) -> dict:
    tasks = [node for node in nodes.values() if node.get("kind") == "task"]
    done = sum(1 for node in tasks if node.get("status") in ("done", "skipped"))
    return {"tasks": len(tasks), "done": done,
            "pct": round(100.0 * done / len(tasks)) if tasks else 100}
