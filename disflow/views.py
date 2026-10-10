"""disflow · 视图层（D80）。

同一投影的三种渲染 —— 看板（按 stage）/ 树（按 parent）/ 图（按边）。**同源**，不各存一份。
"""
from __future__ import annotations

from .model import children_of
from .spec import STAGES

_MARKS = {"done": "✓", "running": "▶", "failed": "✗", "skipped": "—", "pending": "·"}


def _mark(node: dict) -> str:
    return _MARKS.get(node.get("status", "pending"), "?")


def board_lines(tree: dict, stage: str | None = None,
                stages: tuple[str, ...] | None = None) -> list[str]:
    """看板视图：按 `stage` 分列，只列任务节点（容器是结构，不进板）。

    **列定义单一来源**：`stages`（省略取 `spec.STAGES`；CLI 传解析后的 `cfg.stages`，
    于是 `disflow.json` 的 `stages` 就是列的唯一来源 —— 视图层不再抄一份字面量）。

    **固定列**：按配置顺序**逐列输出全部列**，空列显式显示 `0/0`。
    为何要空列（2026-10-11 用户裁决）：只打「出现过的列」时，`board` 在需求/设计为空的树上
    只显示 `[dev] 14/14` —— 读者既看不出这是「需求→设计→开发→验证」的流程，
    也看不出「某阶段为空」这个**事实**。

    **非规范 stage 不许丢**：不在闭集里的历史值（旧版本、别的配置写下的）**追加在末尾并标注**
    —— 与 `engine._validate_vocabulary` 的「读时容忍」同一条纪律（视图对未知值容忍、不崩），
    但容忍不等于静默隐藏数据。

    **`--stage` 过滤**：只输出被选中的那一列（含非规范值），不泄漏别的列——与改动前一致。
    （CLI 侧的 `--stage` 另按配置闭集**拒收非法列名**，见 `cli.cmd_board`：视图是纯渲染器，
    对未知值容忍；「用法错误」在 CLI 这层拦。）
    唯一的差异：被选中的列**没有节点时**，改动前**什么都不打**（与「列名拼错」无法区分），
    现在打 `[{stage}] 0/0`——与上面的「空列显式显示 0」同一条纪律。
    """
    columns = tuple(STAGES if stages is None else stages)
    groups: dict[str, list[tuple[str, dict]]] = {}
    for node_id, node in sorted(tree["nodes"].items()):
        if node.get("kind") != "task":
            continue
        if stage and node.get("stage") != stage:
            continue
        groups.setdefault(str(node.get("stage") or "?"), []).append((node_id, node))
    if stage:                                  # 过滤：只留被选中的那一列
        order = [s for s in columns if s == stage]
    else:                                      # 全列：配置顺序在前（含空列），非规范在后
        order = list(columns)
    order += sorted(s for s in groups if s not in columns)
    lines: list[str] = []
    for name in order:
        rows = groups.get(name, [])
        done = sum(1 for _nid, n in rows if n.get("status") in ("done", "skipped"))
        mark = "  ⚠ 非规范列（不在配置的 stages 内）" if name not in columns else ""
        lines.append(f"[{name}] {done}/{len(rows)}{mark}")
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
        stage = node.get("stage") or "?"  # 读时容忍：未知/缺失 stage 照原样显示，不改写
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
