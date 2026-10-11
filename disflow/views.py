"""disflow · 视图层（D80）。

同一投影的三种渲染 —— 看板（按 stage）/ 树（按 parent）/ 图（按边）。**同源**，不各存一份。

看板有**两种分组**（2026-10-11 用户裁决「我要的是四个看板」）：
- `board_layout` / `board_lines` —— **一块板 × 阶段列**：四个阶段是同一块板里的四列。
- `boards_layout` / `boards_lines` —— **四块板 × 一个阶段**：阶段升格为板与板之间的划分，
  板头报相邻阶段的完成度（板间工作流）。两者**同一份布局**，后者直接消费前者的列。

布局**只有 `board_layout` 一处实现**：人读态（`board_lines` / `boards_lines`）
与机器态（`disflow board --json`）都从它渲染。宿主**运行视图**有自己的分列（v10 起它答的是另一个
问题：**现在谁在跑 / 谁还没做**，见 `.agents/skills/disflow/lanes_watch.py`），但**阶段列名与 goal
截断仍共用**（列读配置、截断走 `truncate_goal`）。

**人读态 vs 机器态（2026-10-11 用户当场提）**：`board_lines` 给的是**屏幕维度**——终态行
（`✓ 完成` / `— 跳过`）默认**不打印 goal**（一行几百字没法扫），其余行的 goal 统一走
`truncate_goal`（首行、≤80 字、`…` 结尾）；**完整 goal 只在 `board --json`**。
`active_only=True`（`board` 的默认）只留「运行中 + 待完成」那些组（`board --all` 展开）。
"""
from __future__ import annotations

from .dispatch import drive
from .model import children_of
from .spec import STAGE_LABELS, STAGES

_MARKS = {"done": "✓", "running": "▶", "failed": "✗", "skipped": "—", "pending": "·"}
_CONTAINER_MARK = "◦"

# 人读态里**不打印 goal** 的组：终态节点一行一个「几百字目标」时屏幕没法扫。
# 完整目标留给 `board --json`（`board_layout` 的 `goal` 字段照旧全文）。
_GOAL_OMITTED_GROUPS: frozenset[str] = frozenset({"done", "skipped"})
# `active_only`（`board` 默认）保留的组：「运行中 + 待完成（可派/待前置/被在跑挡住）」+ 结构行。
_ACTIVE_GROUPS: frozenset[str] = frozenset(
    {"structure", "running", "ready", "blocked", "deferred"})

# 列内**状态分组**：固定顺序 + 标签。某组无节点则整组不出现；有节点则**必须出现**
# （绝不静默丢：每个节点恰好落进一组，见 `_group_of`）。
# `structure`：容器是**结构行**（不是该列的任务行），放在最前——读者先看到骨架再看活。
# `other`：非规范 status（如 `import` 进来的外来取值）——原样显示，不假装它是六类之一。
_GROUPS: tuple[tuple[str, str], ...] = (
    ("structure", "◦ 结构行（容器）"),
    ("running", "▶ 运行中"),
    ("ready", "· 可派"),
    ("blocked", "· 待前置"),
    ("deferred", "· 被在跑挡住"),
    ("done", "✓ 完成"),
    ("skipped", "— 跳过"),
    ("failed", "✗ 失败"),
    ("other", "? 非规范状态（原样显示）"),
)
_GROUP_LABELS: dict[str, str] = dict(_GROUPS)


def truncate_goal(text: str | None, limit: int = 80) -> str:
    """目标文本的单行截断 —— **本仓唯一的截断实现**（看板与运行视图都走这里）。

    取**首个非空行**、折叠内部空白；超过 `limit` 字则以 `…` 结尾（总长 ≤ `limit`）。
    为什么要截断：节点 goal 常是几百字一段，原样打出来一行占满屏幕，人读态没法扫。
    完整文本不丢——`board --json`（`board_layout` 的 `goal`）仍是全文。
    """
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]
    one = " ".join(lines[0].split()) if lines else ""
    if len(one) <= limit:
        return one
    return one[: max(limit - 1, 1)] + "…"


def _mark(node: dict) -> str:
    return _MARKS.get(node.get("status", "pending"), "?")


def _stage_of(node: dict) -> str:
    """节点所属的列（缺失/未知 stage 归入 `?` 列——读时容忍，**不改写**其值）。"""
    return str(node.get("stage") or "?")


def _column_rows(nodes: dict, root: str | None, name: str) -> list[tuple[str, int]]:
    """本列里的节点 + **列内深度**（DFS 顺序，父在子前）。

    深度 = 该节点**在本列里**的祖先数。祖先不在本列时不计：子节点照旧出现、深度回到本列
    顶层（**不许丢、不许负缩进**）——例如容器在 `dev` 列而子节点在 `verify` 列时，子节点
    在 verify 列里就是顶层。

    root 够不着的节点（`parent` 缺失 / 数据损坏）**兜底追加**：与「非规范 stage 不许丢」
    同一条纪律——视图对坏数据容忍，但不容忍静默隐藏。
    """
    rows: list[tuple[str, int]] = []
    seen: set[str] = set()

    def walk(node_id: str, depth: int) -> None:
        if node_id in seen:            # 防环（数据损坏）：宁可少画，不许递归死循环
            return
        seen.add(node_id)
        child_depth = depth
        if _stage_of(nodes[node_id]) == name:
            rows.append((node_id, depth))
            child_depth = depth + 1
        for child in children_of(nodes, node_id):
            walk(child, child_depth)

    if root and root in nodes:
        walk(root, 0)
    for node_id in sorted(nodes):
        if node_id not in seen:
            walk(node_id, 0)
    return rows


def _group_of(node_id: str, node: dict, packable: set[str],
              deferred: dict[str, str], blocked: dict[str, list[str]]) -> str:
    """节点的列内状态分组 —— 判据**全部来自 `dispatch.drive`**（可派性与 drive 同源）。

    判定顺序：分型（容器=结构行）→ 终态/运行（存储态）→ 就绪被挡 / 就绪 / 待前置 → 兜底
    `other`。兜底不是装饰：`import` 能把外来 status 直接写进投影，没有兜底它们会自动消失
    （正是「静默丢」）。
    """
    if node.get("kind") != "task":
        return "structure"
    status = str(node.get("status") or "pending")
    if status in ("running", "done", "skipped", "failed"):
        return status
    if node_id in deferred:
        return "deferred"
    if node_id in packable:
        return "ready"
    if node_id in blocked:
        return "blocked"
    return "other"


def _detail(node_id: str, node: dict, group: str,
            deferred: dict[str, str], blocked: dict[str, list[str]]) -> str:
    """行尾的**可读理由**（谁在跑 / 缺哪个前置 / 与谁重叠哪些路径）——同源取数，不重算。"""
    if group == "running":
        holder = (node.get("lease") or {}).get("holder") or "?"
        return f"holder={holder}"
    if group == "deferred":
        return deferred.get(node_id, "")
    if group == "blocked":
        return "缺 " + "、".join(blocked.get(node_id) or [])
    if group == "other":
        return f"status={node.get('status')!r}（非规范；不静默隐藏）"
    return ""


def _status_word(group: str) -> str:
    """终态行的「状态」词 —— **从组标签派生**（不另写一份词表，避免第二种拼法）。"""
    return _GROUP_LABELS[group].split(" ", 1)[-1]


def board_layout(tree: dict, stage: str | None = None,
                 stages: tuple[str, ...] | None = None, *,
                 closed: set[str] | frozenset[str] = frozenset(),
                 tree_name: str = "", active_only: bool = False,
                 empty_columns: bool = True) -> dict:
    """看板布局的**唯一实现**：阶段列 + 列内树层级 + 列内状态分组。

    **列定义单一来源**：`stages`（省略取 `spec.STAGES` 内建默认；CLI 传解析后的 `cfg.stages`，
    而 `cfg.stages` 由 `boards.json` **派生** —— 板定义住 `disflow/boards.py`，
    视图层不读文件、也不抄一份字面量）。

    **固定列**：按配置顺序**逐列输出全部列**，空列显式显示 `0/0`。
    为何要空列（2026-10-11 用户裁决）：只打「出现过的列」时，`board` 在需求/设计为空的树上
    只显示 `[dev] 14/14` —— 读者既看不出这是「需求→设计→开发→验证」的流程，
    也看不出「某阶段为空」这个**事实**。

    **非规范 stage 不许丢**：不在闭集里的历史值（旧版本、别的配置写下的）**追加在末尾并标注**
    —— 与 `engine._validate_vocabulary` 的「读时容忍」同一条纪律（视图对未知值容忍、不崩），
    但容忍不等于静默隐藏数据。

    **第一列 = 树自身的目标**：root 是**容器**（`stage` = 流程入口板，即板序第一块；
    内建流程里是 `req`，其 `goal` 就是树的目标），
    它以**结构行**出现，于是需求看得见——改动前 board 明确排除容器（`kind != "task"` 跳过），
    `[req]` 恒 `0/0`，等于把需求藏了。但容器**不是该列的任务行**：表头计数只数任务，
    容器不计入 `done/total`（与 `ready.progress` / `metrics`「容器不量化」同一口径）。

    **列内状态分组**：`running / ready / blocked / deferred / done / skipped / failed`
    （外加 `structure` 结构行与 `other` 兜底）——某组只要有一个节点就必须出现；可派性与
    `drive` **同源**：直接消费 `dispatch.drive` 的读数（`waves` / `deferred` / `blocked`），
    视图层**不重算**写面冲突，也不另存一份判据。

    **`--stage` 过滤**：只输出被选中的那一列（含非规范值），不泄漏别的列——与改动前一致。
    （CLI 侧的 `--stage` 另按配置闭集**拒收非法列名**，见 `cli.cmd_board`：视图是纯渲染器，
    对未知值容忍；「用法错误」在 CLI 这层拦。）被选中的列**没有节点时**照旧打 `[{stage}] 0/0`
    ——改动前什么都不打（与「列名拼错」无法区分）。

    **`active_only`（`board` 默认）**：只留 `_ACTIVE_GROUPS`（运行中 / 可派 / 待前置 / 被在跑
    挡住 + 结构行），隐藏 `✓ 完成` / `— 跳过` / `✗ 失败` / 非规范兜底 —— 「只显示运行中的
    和待完成的」。**默认 False，行为不变**；列头计数照旧是全列的口径（隐藏 ≠ 改写事实）。

    **`empty_columns=False`**：**没有任何任务**的列（通常只有 root 容器）不进版面 —— 干净优先
    （用户 2026-10-11：「我要的是清晰干净的视图」）。**事实不丢**：CLI 的「列」摘要行照报
    `design 0/0`（与 b2 「空列这个事实要看得到」的裁决不冲突 —— 那条裁决要的是**别看不出**
    某阶段为空，不是非要铺一行）。`--stage X` 显式选列时不受影响（选了就一定显示）。
    默认 `True` = 全部列都铺（向后兼容）；`board --json` 恒为 `True`（机器看事实）。

    **纯读**：本函数只读投影 + 只做派生计算（`dispatch.drive` 同样不写状态），不落事件。
    """
    columns = tuple(STAGES if stages is None else stages)
    nodes = tree["nodes"]
    root = tree.get("root")
    # 可派性**与 drive 同源**：直接读 `drive` 的读数（`waves` = 可装波次的候选；`deferred`
    # = 被在跑节点的写面挡下、带点名理由；`blocked` = 缺哪个前置），视图层一个都不重算。
    facts = drive(tree_name, tree, closed)
    packable = {nid for wave in facts["waves"] for nid in wave}
    deferred = {entry["node"]: entry["why"] for entry in facts.get("deferred") or []}
    blocked = facts["blocked"]

    present = sorted({_stage_of(node) for node in nodes.values()})
    if stage:                                  # 过滤：只留被选中的那一列
        order = [name for name in columns if name == stage]
        if not order and stage in present:     # 读时容忍：未知列取值照样渲染（不崩）
            order = [stage]
    else:                                      # 全列：配置顺序在前（含空列），非规范在后
        order = list(columns) + [name for name in present if name not in columns]

    columns_out: list[dict] = []
    for name in order:
        rows = _column_rows(nodes, root, name)
        task_ids = [nid for nid, _depth in rows if nodes[nid].get("kind") == "task"]
        if not empty_columns and not task_ids and not stage:
            continue            # 干净优先：没有任务的列（通常只有 root 容器）不占版面
        done = sum(1 for nid in task_ids
                   if str(nodes[nid].get("status")) in ("done", "skipped"))
        note = "  ⚠ 非规范列（不在配置的 stages 内）" if name not in columns else ""
        grouped: dict[str, list[dict]] = {}
        for node_id, depth in rows:
            node = nodes[node_id]
            group = _group_of(node_id, node, packable, deferred, blocked)
            grouped.setdefault(group, []).append({
                "node": node_id,
                "parent": node.get("parent"),
                "kind": node.get("kind"),
                "stage": node.get("stage"),
                "status": node.get("status", "pending"),
                "depth": depth,
                "mark": _CONTAINER_MARK if node.get("kind") != "task" else _mark(node),
                "goal": node.get("goal", ""),
                "detail": _detail(node_id, node, group, deferred, blocked),
            })
        columns_out.append({
            "stage": name,
            "canonical": name in columns,
            "header": f"[{name}] {done}/{len(task_ids)}{note}",
            "tasks": len(task_ids),
            "done": done,
            "groups": [{"group": gid, "label": _GROUP_LABELS[gid], "rows": grouped[gid]}
                       for gid, _label in _GROUPS
                       if gid in grouped and (not active_only or gid in _ACTIVE_GROUPS)],
        })
    return {"tree": tree_name, "root": root, "goal": tree.get("goal", ""),
            "columns": columns_out}


def _row_line(row: dict, group: dict) -> str:
    """单行的**渲染文本**（看板与宿主运行视图共用；缩进/标记/目标/理由都只在这一处）。"""
    head = f"{'  ' * (row['depth'] + 1)}{row['mark']} {row['node']}  "
    is_root_container = row["kind"] != "task" and not row["parent"]
    if group["group"] in _GOAL_OMITTED_GROUPS:
        line = head + f"（{_status_word(group['group'])}）"
    elif is_root_container:
        line = head.rstrip()
    else:
        line = head + truncate_goal(row["goal"])
    if row["detail"]:
        line += f"  （{row['detail']}）"
    return line


def board_rows(tree: dict, stage: str | None = None,
               stages: tuple[str, ...] | None = None, *,
               closed: set[str] | frozenset[str] = frozenset(),
               tree_name: str = "", active_only: bool = False,
               empty_columns: bool = True) -> list[dict]:
    """布局 → **带类型**的逐行渲染（`kind = column | group | row`）—— **唯一行序来源**。

    `board_lines` 是它的纯文本投影；宿主运行视图按 `kind` 消费（不再按位置 `next()`）。
    为何要类型（2026-10-11 实测踩到）：折叠空列/空体列后行数变了，按位置取行的消费方
    直接 `StopIteration` —— 渲染可以变，消费方不该因此错位。
    """
    layout = board_layout(tree, stage, stages, closed=closed, tree_name=tree_name,
                          active_only=active_only, empty_columns=empty_columns)
    rows: list[dict] = []
    for column in layout["columns"]:
        shown = [g for g in column["groups"] if g["rows"]]
        if active_only and not shown and not empty_columns:
            # 自动折叠（默认）：空列 / 只有终态行的列不铺（计数已在表头「列」行）；
            # `--all-columns`（`empty_columns=True`）是**显式要求铺开**，不受此折叠影响。
            continue
        rows.append({"kind": "column", "stage": column["stage"], "text": column["header"]})
        for group in shown:
            rows.append({"kind": "group", "group": group["group"], "text": group["label"]})
            for row in group["rows"]:
                rows.append({"kind": "row", "node": row["node"], "group": group["group"],
                             "row": row, "text": _row_line(row, group)})
    return rows


def board_lines(tree: dict, stage: str | None = None,
                stages: tuple[str, ...] | None = None, *,
                closed: set[str] | frozenset[str] = frozenset(),
                tree_name: str = "", active_only: bool = False,
                empty_columns: bool = True) -> list[str]:
    """人读态看板：把 `board_layout`（唯一布局实现）渲染成行 —— 与 `board --json` **同源**。

    行形状：列头 `[<stage>] done/total` → 组标签（如 `▶ 运行中`）→ 行
    `{'  ' * (depth + 1)}<mark> <id>  <goal 截断>  （<理由>）`。缩进 = **列内深度**，
    所以「父子」在同一列里看得见；理由只在有话说时出现（谁在跑 / 缺哪个前置 / 与谁冲突）。

    **终态行（`✓ 完成` / `— 跳过`）默认只给 `标记 + id + 状态`，不打印 goal** ——
    一行几百字会把屏幕冲垮；完整目标在 `--json`。其余行的 goal 走 `truncate_goal`
    （首行、≤80 字、`…` 结尾）。`active_only` / `empty_columns` 直接透传给布局。

    **root 容器行不打 goal**（2026-10-11）：树目标是**表头**的事，`[req]` 再抄一遍就是
    同一段长文本在屏幕上出现两次（`--json` 里两个字段都在，不丢）。
    """
    lines: list[str] = []
    for item in board_rows(tree, stage, stages, closed=closed, tree_name=tree_name,
                           active_only=active_only, empty_columns=empty_columns):
        lines.append(item["text"])
    return lines


def _stage_label(stage: str, labels: dict[str, str] | None = None) -> str:
    """阶段的人读标签 —— 缺项即回落成阶段名（自定义板不崩，也不另造第二份词表）。

    `labels` 由调用方从**配置**传入（`Config.labels` ← `boards.json`）；省略时用内建默认
    （`spec.STAGE_LABELS`）—— 视图层不读文件，配置归 `config`/`boards` 两个模块。
    """
    table = STAGE_LABELS if labels is None else labels
    return table.get(stage, stage)


def boards_layout(tree: dict, stages: tuple[str, ...] | None = None, *,
                  labels: dict[str, str] | None = None,
                  closed: set[str] | frozenset[str] = frozenset(),
                  tree_name: str = "", active_only: bool = False,
                  empty_boards: bool = True) -> dict:
    """**四块板**：一个阶段一块板（板内 = 树层级 + 状态分组）。

    与 `board_layout` 的关系是**同一份布局换一种分组**：阶段在 `board` 里是「板内的列」，
    在这里是「板与板之间的划分」。数据一个字都不多 —— 本函数**直接消费 `board_layout` 的列**，
    不重算写面冲突、不另存判据（`board_layout` 仍是布局的**唯一实现**）。

    **板间工作流**：每块板带 `upstream` / `downstream`（相邻阶段的 `done/tasks`）。
    为何只给**相邻**阶段而不是整条链：整条链会让板头变成长表格，而读者只关心
    「我上一关过了没」——上游没过，本板的活就还没轮到（可派性由 `dispatch.drive` 判定，
    这里只**显示**，不参与判定）。

    **板名从配置来**：`labels`（`Config.labels` ← `boards.json`）—— 视图不读文件、不硬编码
    中文名；自定义板缺标签时回落成 id。`empty_boards=False` 只影响**渲染层**，机器态恒为全量。
    """
    layout = board_layout(tree, None, stages, closed=closed, tree_name=tree_name,
                          active_only=active_only, empty_columns=True)
    columns = layout["columns"]
    total = len(columns)

    def _peer(column: dict) -> dict:
        return {"stage": column["stage"], "label": _stage_label(column["stage"], labels),
                "done": column["done"], "tasks": column["tasks"]}

    boards: list[dict] = []
    for index, column in enumerate(columns):
        boards.append({
            "stage": column["stage"],
            "label": _stage_label(column["stage"], labels),
            "index": index + 1,
            "total_boards": total,
            "canonical": column["canonical"],
            "tasks": column["tasks"],
            "done": column["done"],
            "upstream": _peer(columns[index - 1]) if index > 0 else None,
            "downstream": _peer(columns[index + 1]) if index + 1 < total else None,
            "groups": column["groups"],
        })
    if not empty_boards:
        boards = [b for b in boards if b["tasks"]]
    return {"tree": layout["tree"], "root": layout["root"], "goal": layout["goal"],
            "boards": boards}


def board_head(board: dict) -> str:
    """板头（**唯一实现**）：序号 + 标签 + 阶段名 + 完成度 + 上游完成度。"""
    flow = ""
    if board["upstream"]:
        flow = f"  ← {board['upstream']['label']} {board['upstream']['done']}/{board['upstream']['tasks']}"
    return (f"══ 板 {board['index']}/{board['total_boards']} · {board['label']} "
            f"[{board['stage']}] {board['done']}/{board['tasks']} ══{flow}")


def boards_lines(tree: dict, stages: tuple[str, ...] | None = None, *,
                 labels: dict[str, str] | None = None,
                 closed: set[str] | frozenset[str] = frozenset(),
                 tree_name: str = "", active_only: bool = False,
                 empty_boards: bool = True) -> list[str]:
    """人读态：**四块板**（一块板一个阶段）—— 与 `board --by board --json` 同源。

    行形状：板头 `══ 板 N/M · <标签> [<stage>] done/total ══  ← <上游> d/t`
    → 组标签 → 行。行本身**复用 `_row_line`**（与 `board` 同一个渲染，不另写一份缩进/截断）。

    板间用空行隔开：四块板是一个**流程**，不是四个挤在一起的段落。
    """
    layout = boards_layout(tree, stages, labels=labels, closed=closed, tree_name=tree_name,
                           active_only=active_only, empty_boards=empty_boards)
    lines: list[str] = []
    for board in layout["boards"]:
        if lines:
            lines.append("")
        lines.append(board_head(board))
        shown = [group for group in board["groups"] if group["rows"]]
        if not shown:
            # 空板要说清是**真没有任务**还是**被终态折叠**——两者长得一样但意思相反。
            lines.append("  （空 —— 本阶段还没有任务）" if not board["tasks"]
                         else "  （终态折叠：--all 展开）")
            continue
        for group in shown:
            lines.append(group["label"])
            for row in group["rows"]:
                lines.append(_row_line(row, group))
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
