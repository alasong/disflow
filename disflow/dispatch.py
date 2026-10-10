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
    pack_waves,
    progress,
    ready_tasks,
    running_tasks,
)


def drive(tree_name: str, tree: dict) -> dict:
    """一次只读推进：列出活节点、就绪波次、阻塞、卡死。同输入必同输出。"""
    nodes = tree["nodes"]
    ready = ready_tasks(nodes)
    waves = pack_waves(nodes, ready)
    blocked = {nid: blocked_by(nodes, nid) for nid in live_tasks(nodes)
               if blocked_by(nodes, nid)}
    return {
        "tree": tree_name,
        "goal": tree.get("goal", ""),
        "root": tree.get("root"),
        "live": live_tasks(nodes),
        "running": running_tasks(nodes),
        "ready_wave": waves[0] if waves else [],
        "waves": waves,
        "blocked": blocked,
        "stuck": is_stuck(nodes),
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


def dispatch_plan(tree_name: str, tree: dict) -> list[dict]:
    """就绪波次里每个节点的可照抄派发计划（不含实际 spawn）。"""
    result = drive(tree_name, tree)
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
