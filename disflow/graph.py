"""disflow · 图原语（调度依赖 + 引用）。

调度边 `requires` 必须构成 DAG：写入时即拒环（fail-fast）。
引用边 `relates` 不参与就绪，可任意。
产出登记 `produces`/`consumes` 是图上的资产关系。
"""
from __future__ import annotations


def requires_of(node: dict) -> list[str]:
    return list(node.get("requires") or [])


def unmet_requires(node: dict, nodes: dict) -> list[str]:
    """返回尚未 done 的前置（含不存在的前置 —— 悬空即视为未满足并报告）。"""
    out: list[str] = []
    for dep in requires_of(node):
        target = nodes.get(dep)
        if target is None or target.get("status") not in ("done", "skipped"):
            out.append(dep)
    return out


def dangling_requires(node: dict, nodes: dict) -> list[str]:
    return [dep for dep in requires_of(node) if dep not in nodes]


def would_cycle(nodes: dict, src: str, dst: str) -> bool:
    """若加边 `src requires dst`，是否成环？= dst 能否沿 requires 到达 src。"""
    stack = [dst]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current == src:
            return True
        if current in seen:
            continue
        seen.add(current)
        node = nodes.get(current)
        if node is not None:
            stack.extend(requires_of(node))
    return False


def find_cycles(nodes: dict) -> list[list[str]]:
    """返回 requires 子图中的强连通/环（用于 doctor 与 status 自检）。"""
    WHITE, GREY, BLACK = 0, 1, 2
    color = dict.fromkeys(nodes, WHITE)
    cycles: list[list[str]] = []
    path: list[str] = []

    def visit(nid: str) -> None:
        color[nid] = GREY
        path.append(nid)
        for dep in requires_of(nodes[nid]):
            if dep not in nodes:
                continue
            if color[dep] == GREY:
                cycles.append(path[path.index(dep):] + [dep])
            elif color[dep] == WHITE:
                visit(dep)
        path.pop()
        color[nid] = BLACK

    for nid in sorted(nodes):
        if color[nid] == WHITE:
            visit(nid)
    return cycles


def topo_waves(node_ids: list[str], nodes: dict) -> tuple[list[list[str]], list[str]]:
    """按 requires 拓扑分层（Kahn）。返回 (waves, 环中残留节点)。"""
    pending = set(node_ids)
    in_degree = dict.fromkeys(node_ids, 0)
    dependents: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for nid in node_ids:
        for dep in requires_of(nodes[nid]):
            if dep in pending:
                in_degree[nid] += 1
                dependents[dep].append(nid)

    queue = sorted(nid for nid in node_ids if in_degree[nid] == 0)
    waves: list[list[str]] = []
    processed: set[str] = set()
    while queue:
        waves.append(list(queue))
        processed.update(queue)
        next_queue: list[str] = []
        for nid in queue:
            for dependent in dependents.get(nid, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    next_queue.append(dependent)
        queue = sorted(next_queue)
    residual = sorted(nid for nid in node_ids if nid not in processed)
    return waves, residual
