"""disflow · 就绪判据（唯一函数）。

`ready` 是**派生量**，不落库。分发器与 worker 共用同一份判定，两面不分叉。

本模块属**核心层**（NFR-2）：只用机制词汇（状态/分型/边/租约），不含业务词；
写面打包（依赖业务声明的写面字段）不在这里，见 `dispatch.pack_waves`。
"""
from __future__ import annotations

from datetime import datetime

from .core import TERMINAL
from .graph import unmet_requires

RUNNING = "running"


def lease_expired(lease: dict | None, now_ts: float) -> bool:
    """租约是否已过期（仅 `reap` 调用；`ready()` 不看时钟，保持纯函数）。"""
    return lease_overdue_seconds(lease, now_ts) is not None


def lease_overdue_seconds(lease: dict | None, now_ts: float) -> float | None:
    """租约已过期**多久**（秒）；未过期 / 无租约 / 解析不了 → `None`。

    为何要“多久”而不只是“过期了”：这是人判断「该 `reap` 还是该继续干」的**唯一线索**
    ——刚过几分钟多半是人还在干，过了几天多半是 worker 崩了。
    只有布尔值的报错会把这两种完全不同的情况说成同一件事。
    """
    if not lease:
        return None
    raw = lease.get("expires_at")
    if not raw:
        return None
    try:
        overdue = now_ts - datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None
    return overdue if overdue > 0 else None


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
    done = sum(1 for node in tasks if node.get("status") in TERMINAL)
    return {"tasks": len(tasks), "done": done,
            "pct": round(100.0 * done / len(tasks)) if tasks else 100}
