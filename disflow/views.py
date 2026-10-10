"""disflow · 视图层（D80）。

同一投影的三种渲染 —— 看板（按 stage）/ 树（按 parent）/ 图（按边）。**同源**，不各存一份。
"""
from __future__ import annotations

from .model import children_of
from .spec import STAGES

_MARKS = {"done": "✓", "running": "▶", "failed": "✗", "skipped": "—", "pending": "·"}


def _mark(node: dict) -> str:
    return _MARKS.get(node.get("status", "pending"), "?")


def board_lines(tree: dict, stage: str | None = None) -> list[str]:
    """看板视图：按 stage 分列，只列任务节点（容器是结构，不进板）。"""
    groups: dict[str, list[tuple[str, dict]]] = {}
    for node_id, node in sorted(tree["nodes"].items()):
        if node.get("kind") != "task":
            continue
        if stage and node.get("stage") != stage:
            continue
        groups.setdefault(str(node.get("stage") or "?"), []).append((node_id, node))
    order = [s for s in STAGES if s in groups]
    order += sorted(s for s in groups if s not in STAGES)
    lines: list[str] = []
    for name in order:
        rows = groups[name]
        done = sum(1 for _nid, n in rows if n.get("status") in ("done", "skipped"))
        lines.append(f"[{name}] {done}/{len(rows)}")
        for node_id, node in rows:
            lines.append(f"  {_mark(node)} {node_id}  {node.get('goal', '')}")
    return lines


def tree_lines(tree: dict) -> list[str]:
    """树视图：按 parent 缩进（root → 子节点）。"""
    nodes = tree["nodes"]
    lines: list[str] = []

    def walk(node_id: str, depth: int) -> None:
        node = nodes[node_id]
        tag = "◦" if node.get("kind") == "container" else _mark(node)
        stage = node.get("stage") or "?"
        lines.append(f"{'  ' * depth}{tag} {node_id} [{stage}] {node.get('goal', '')}")
        for child in children_of(nodes, node_id):
            walk(child, depth + 1)

    root = tree.get("root")
    if root and root in nodes:
        walk(root, 0)
    return lines


def graph_edges(tree: dict) -> list[tuple[str, str, str]]:
    """图视图的边集：requires（调度）/ relates（引用）/ consumes（产出消费）。"""
    edges: list[tuple[str, str, str]] = []
    for node_id, node in sorted(tree["nodes"].items()):
        for dep in node.get("requires") or []:
            edges.append((node_id, "requires", dep))
        for other in node.get("relates") or []:
            edges.append((node_id, "relates", other))
        for name in node.get("consumes") or []:
            edges.append((node_id, "consumes", name))
    return edges


def graph_lines(tree: dict) -> list[str]:
    symbol = {"requires": "─▶", "relates": "┄▶", "consumes": "┈▶"}
    return [f"{src} {symbol.get(kind, '?')} {dst}" for src, kind, dst in graph_edges(tree)]


def to_dot(tree: dict, name: str = "disflow") -> str:
    """Graphviz DOT（requires 实线、relates 虚线、consumes 点线）。"""
    style = {"requires": "", "relates": " style=dashed",
             "consumes": " style=dotted"}
    nodes = tree["nodes"]
    lines = [f'digraph "{name}" {{', "  rankdir=LR;"]
    for node_id, node in sorted(nodes.items()):
        shape = "box" if node.get("kind") == "container" else "ellipse"
        label = f"{node_id}\\n{node.get('status', '')}"
        lines.append(f'  "{node_id}" [shape={shape}, label="{label}"];')
    for src, kind, dst in graph_edges(tree):
        if dst in nodes:
            lines.append(f'  "{src}" -> "{dst}" [{style.get(kind, "")}];')
    lines.append("}")
    return "\n".join(lines)
