"""disflow · 就绪判据（唯一函数）。

`ready` 是**派生量**，不落库。分发器与 worker 共用同一份判定，两面不分叉。
"""
from __future__ import annotations

from datetime import datetime

from .graph import unmet_requires
from .spec import TERMINAL

RUNNING = "running"


def lease_expired(lease: dict | None, now_ts: float) -> bool:
    """租约是否已过期（仅 `reap` 调用；`ready()` 不看时钟，保持纯函数）。"""
    if not lease:
        return False
    raw = lease.get("expires_at")
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw).timestamp() < now_ts
    except ValueError:
        return False


def awaits_of(node: dict) -> list[str]:
    """跨树完成信号：本节点等待哪些**别的树** `closed`（不是图上的 `requires`）。"""
    return list(node.get("awaits") or [])


def awaits_unmet(node: dict, closed: set[str] | frozenset[str]) -> list[str]:
    return [tree for tree in awaits_of(node) if tree not in closed]


def is_ready(nodes: dict, node_id: str,
             closed: set[str] | frozenset[str] = frozenset()) -> bool:
    """任务节点就绪 = pending ∧ 未被租约占用 ∧ requires 全 done ∧ awaits 全 closed。

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
    if awaits_unmet(node, closed):
        return False
    return not unmet_requires(node, nodes)


def blocked_by(nodes: dict, node_id: str,
               closed: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """列出阻塞该节点的前置（悬空 `requires` 与未满足的跨树 `awaits`，后者带 `@`）。"""
    node = nodes[node_id]
    return unmet_requires(node, nodes) + [f"@{tree}" for tree in awaits_unmet(node, closed)]


def ready_tasks(nodes: dict, closed: set[str] | frozenset[str] = frozenset()) -> list[str]:
    """全部就绪的任务节点（id 升序，确定性）。"""
    return [nid for nid in sorted(nodes) if is_ready(nodes, nid, closed)]


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


def is_stuck(nodes: dict, closed: set[str] | frozenset[str] = frozenset()) -> bool:
    """卡死：还有 **pending** 任务、但既无就绪也无运行（被阻塞 / 成环 / 引用悬空）。

    `failed` 不算卡死 —— 它可见、可 retry；卡死指的是「没有任何东西能推进也不在跑」。
    """
    pending = [nid for nid, node in nodes.items()
               if node.get("kind") == "task" and node.get("status") == "pending"]
    return bool(pending) and not ready_tasks(nodes, closed) and not running_tasks(nodes)


def progress(nodes: dict) -> dict:
    tasks = [node for node in nodes.values() if node.get("kind") == "task"]
    done = sum(1 for node in tasks if node.get("status") in ("done", "skipped"))
    return {"tasks": len(tasks), "done": done,
            "pct": round(100.0 * done / len(tasks)) if tasks else 100}
