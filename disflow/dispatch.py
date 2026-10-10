"""disflow · 分发面（**只推不跑**）。

`drive` 只计算：就绪波次 + 阻塞 + 卡死；`dispatch_plan` 生成可照抄的 brief 命令。
spawn 归宿主 —— 本模块不启动任何进程。
"""
from __future__ import annotations

import os

from . import worktree
from .config import DEFAULTS, Config
from .context import brief as build_brief
from .ready import (
    blocked_by,
    is_stuck,
    live_tasks,
    progress,
    ready_tasks,
    running_tasks,
)
from .spec import SPEC


def _touch_pair_overlap(left: str, right: str) -> bool:
    """单对写面冲突：同一路径，或一方是另一方的目录前缀。"""
    a, b = left.rstrip("/"), right.rstrip("/")
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _touches_overlap(a: list[str], b: list[str]) -> bool:
    """写面冲突：任一对路径重合或互为目录前缀。"""
    return any(_touch_pair_overlap(left, right) for left in a for right in b)


def _occupied_touches(nodes: dict) -> dict[str, list[str]]:
    """占用写面的节点：**非终态**且**租约在手** ⇒ `{node_id: touch_plan}`（id 升序）。

    这就是「正在运行」的判据。刻意**不看时钟**：租约是否过期归 `reap` 判，而 `drive`
    必须保持纯函数（design.md 对 drive 的承诺：两次同输入同输出，不引入时钟 / 计数依赖）。
    也**不看**该节点是不是候选 —— 只要在跑，它的写面就视为已占用（单写者收口）。
    """
    return {nid: nodes[nid].get("touch_plan") or []
            for nid in live_tasks(nodes) if nodes[nid].get("lease")}


def _overlap_with_running(nodes: dict, node_id: str,
                          occupied: dict[str, list[str]]) -> list[str]:
    """候选被**在跑**节点挡下的原因清单（空 = 写面不与任何在跑节点冲突）。

    每条都点名在跑节点与重叠路径 —— 这是「绝不静默丢」的载体：被挡下的节点不是被删掉，
    而是带着可读的理由出现在 `drive` 的 `deferred` 里（决定权留给宿主，引擎只提示）。
    """
    touches = nodes[node_id].get("touch_plan") or []
    reasons: list[str] = []
    for other, other_touches in occupied.items():
        shared: list[str] = []
        for path in touches:
            if path not in shared and any(
                    _touch_pair_overlap(path, right) for right in other_touches):
                shared.append(path)
        if shared:
            reasons.append(f"与正在运行的 {other} 写面重叠：{', '.join(shared)}")
    return reasons


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

    就绪波次里**不含**与「正在运行（非终态 + 租约在手）」节点写面重叠的候选 —— 它们被
    移进 `deferred` 并附原因（只在非空时出现）。如此宿主循环（⑥ 回到 ①）不会派出一个
    正和在跑节点抢同一文件的节点；引擎仍**只推不跑**：不改状态、不结束别人的租约。
    """
    nodes = tree["nodes"]
    ready = ready_tasks(nodes, closed)
    occupied = _occupied_touches(nodes)
    # 与在跑节点写面重叠的候选**不进** ready_wave/waves（否则宿主会派它与在跑者抢同一文件），
    # 但**不静默**：逐个记进 `deferred`，点名冲突的在跑节点与重叠路径（绝不悄悄丢）。
    deferred: list[dict] = []
    packable: list[str] = []
    for nid in ready:
        reasons = _overlap_with_running(nodes, nid, occupied)
        if reasons:
            deferred.append({"node": nid, "why": "；".join(reasons)})
        else:
            packable.append(nid)
    waves = pack_waves(nodes, packable)
    blocked = {nid: blocked_by(nodes, nid, closed) for nid in live_tasks(nodes)
               if blocked_by(nodes, nid, closed)}
    result = {
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
    # `deferred` **仅在非空时出现**：无节点被挡下时输出与改动前**逐字节一致**
    # （design.md 的「两次同输入同输出」承诺 + 本节点的回归判据）。无新闻 = 不报。
    if deferred:
        result["deferred"] = deferred
    return result


def resolve_question_policy(node: dict, cfg: Config = DEFAULTS) -> dict:
    """提问纪律：`context.question_policy` 优先，否则项目配置默认（再否则出厂默认）。

    与 `gates.resolve_budget` 同一形状（三层：节点声明 > 项目配置 > 内建默认），
    因为两者是同一类东西：**项目级默认 + 节点级覆盖的声明**。

    返回值总是完整的三个子键 —— 合并而不是整体替换：节点只声明 `how` 时不该把
    `ask_host` 弄丢（丢掉 = 纪律静默变窄，而 brief 看上去还是「有纪律」）。
    """
    base = dict(cfg.question_policy)
    raw = (node.get("context") or {}).get("question_policy")
    if isinstance(raw, dict):
        for key in ("ask_host", "decide_alone", "how"):
            if key in raw:
                base[key] = raw[key]
    return base


def _worktree_brief(tree_name: str, node_id: str, repo: str) -> dict | None:
    """已 apply（真 worktree）→ `{"path": <abs>, "branch": "disflow/<tree>/<node>"}`；否则 None。

    worktree 是派生资源，存在性只靠只读的现实对账（`status.real`）；repo 为空 / 探针失败 /
    该节点没有 worktree 一律返回 None——brief 多一行 null 无妨，报错则会把派发卡住。
    """
    if not repo:
        return None
    try:
        present = worktree.status(SPEC, tree_name, node_id, repo)
    except (KeyError, worktree.WorktreeError, ValueError):
        return None
    if not present.get("real"):
        return None
    path = str(present["path"])
    if not os.path.isabs(path):
        path = os.path.join(repo, path)
    return {"path": path, "branch": worktree.branch_for(tree_name, node_id)}


def handoff_brief(tree_name: str, tree: dict, node_id: str,
                  cfg: Config = DEFAULTS, *, repo: str = "") -> dict:
    """写全派发契约：worker 的完整输入（FR-15：必填声明 + **提问纪律** + 结果契约）。

    `repo` 给定时额外给出 `worktree` 字段（已 apply 则为绝对路径 + 分支，否则 None），
    并在 `context` 里写明「你的工作目录是 path，不是仓根」——worker 拿到的第一个可执行事实
    必须包括「到哪干活」，而不是默认仓根（门在 worktree 上跑，写错目录 = 白干）。
    """
    nodes = tree["nodes"]
    payload = build_brief(nodes, node_id)
    payload["tree"] = tree_name
    payload["question_policy"] = resolve_question_policy(nodes[node_id], cfg)
    payload["result_contract"] = result_contract(tree_name, tree, node_id)
    payload["brief_cmd"] = f"disflow brief {tree_name} {node_id}"
    payload["done_cmd"] = f"disflow done {tree_name} {node_id} --decl '{{\"evidence\": …}}'"
    workspace = _worktree_brief(tree_name, node_id, repo)
    payload["worktree"] = workspace
    if workspace is not None:
        payload["context"]["worktree"] = (
            f"你的工作目录是 {workspace['path']}，不是仓根——"
            "所有命令与改动都相对该目录，门也在该目录上跑。")
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
        "status_in": ["ok", "failed", "blocked"],
        "question_channel": (
            '有问题就把问题写进 summary 且 status 置为 "blocked"（然后停）；'
            '不要自己拍板改范围——宿主会读 blocked 并决定'
        ),
        "acceptance": node.get("acceptance"),
    }


def validate_result(contract: dict, result: dict) -> list[str]:
    """返回缺失/非法项清单（空 = 通过）。"""
    missing = [key for key in contract["required"] if key not in result]
    status = result.get("status")
    if status is not None and status not in contract["status_in"]:
        missing.append(f"status 非法：{status!r}（应为 {contract['status_in']}）")
    return missing
