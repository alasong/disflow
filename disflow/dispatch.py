"""disflow · 分发面（**只推不跑**）。

`drive` 只计算：就绪波次 + 阻塞 + 卡死；`dispatch_plan` 生成可照抄的 brief 命令。
spawn 归宿主 —— 本模块不启动任何进程。
"""
from __future__ import annotations

from .context import brief as build_brief
from .ready import (
    blocked_by,
    is_stuck,
    live_tasks,
    progress,
    ready_tasks,
    running_tasks,
)


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
    """贪心把就绪节点装进波次：同波写面（`touch_plan`）不重叠。

    写面是业务声明（不入核心层），故本函数住在**分发面**而非 `ready.py`。
    """
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


def drive(tree_name: str, tree: dict,
          closed: set[str] | frozenset[str] = frozenset()) -> dict:
    """一次只读推进：列出活节点、就绪波次、阻塞、卡死。同输入必同输出。

    `closed`：已关闭的树集合（跨树完成信号 `awaits` 的判据）。
    """
    nodes = tree["nodes"]
    ready = ready_tasks(nodes, closed)
    waves = pack_waves(nodes, ready)
    blocked = {nid: blocked_by(nodes, nid, closed) for nid in live_tasks(nodes)
               if blocked_by(nodes, nid, closed)}
    return {
        "tree": tree_name,
        "goal": tree.get("goal", ""),
        "root": tree.get("root"),
        "live": live_tasks(nodes),
        "running": running_tasks(nodes),
        "ready_wave": waves[0] if waves else [],
        "waves": waves,
        "blocked": blocked,
        "stuck": is_stuck(nodes, closed),
        "progress": progress(nodes),
    }


def handoff_brief(tree_name: str, tree: dict, node_id: str) -> dict:
    """写全派发契约：worker 的完整输入。"""
    nodes = tree["nodes"]
    payload = build_brief(nodes, node_id)
    payload["tree"] = tree_name
    payload["brief_cmd"] = f"disflow brief {tree_name} {node_id}"
    payload["done_cmd"] = f"disflow done {tree_name} {node_id} --decl '{{\"evidence\": …}}'"
    return payload


def dispatch_plan(tree_name: str, tree: dict,
                  closed: set[str] | frozenset[str] = frozenset()) -> list[dict]:
    """就绪波次里每个节点的可照抄派发计划（不含实际 spawn）。"""
    result = drive(tree_name, tree, closed)
    plan = []
    for index, node_id in enumerate(result["ready_wave"], 1):
        node = tree["nodes"][node_id]
        plan.append({
            "node": node_id,
            "stage": node.get("stage"),
            "role": node.get("role"),
            "goal": node.get("goal", ""),
            "brief_cmd": f"disflow brief {tree_name} {node_id}",
            "wave": index,
        })
    return plan


def result_contract(tree_name: str, tree: dict, node_id: str) -> dict:
    """collect 校验的结果契约。"""
    node = tree["nodes"].get(node_id)
    if node is None:
        raise KeyError(f"节点不存在：{node_id}")
    return {
        "node": node_id,
        "required": ["status", "summary", "changed_files"],
        "status_in": ["ok", "failed"],
        "acceptance": node.get("acceptance"),
    }


def validate_result(contract: dict, result: dict) -> list[str]:
    """返回缺失/非法项清单（空 = 通过）。"""
    missing = [key for key in contract["required"] if key not in result]
    status = result.get("status")
    if status is not None and status not in contract["status_in"]:
        missing.append(f"status 非法：{status!r}（应为 {contract['status_in']}）")
    return missing
