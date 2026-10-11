"""disflow · 节点模型（树结构原语）。

只做纯函数：节点构造、校验、父子遍历、派生状态。不碰 IO、不碰存储。
树边是 `parent`（唯一父）；这里是树语义的唯一实现处。
"""
from __future__ import annotations

import math

from .vocab import KINDS, STORED_STATUS

# 节点在**投影里**的完整字段集
NODE_FIELDS: frozenset[str] = frozenset({
    "id", "parent", "kind", "stage", "role", "goal", "context", "entry",
    "requires", "relates", "produces", "consumes", "touch_plan", "acceptance",
    "awaits", "status", "lease", "evidence", "points",
})

# 运行时/派生字段：由事件流决定，**不允许**在 `expand` 的声明里给
#   - `parent`：由 expand 的父子关系决定（声明另一个父没有意义）
#   - `status` / `lease` / `evidence`：由 `node.start` / `collect` / `done` 等事件驱动
#
# 为何是**拒收**而不是忽略：忽略就是“声明被接受、然后静默丢弃”——同一个 bug 家族
# （`requires` 曾经也被静默丢弃，后果是调度乱序）。要么生效，要么报错。
RUNTIME_FIELDS: frozenset[str] = frozenset({"parent", "status", "lease", "evidence"})

# `expand` 声明可给的字段 = 完整形状 − 运行时字段
DECLARABLE_FIELDS: frozenset[str] = NODE_FIELDS - RUNTIME_FIELDS

_LIST_FIELDS = ("requires", "relates", "produces", "consumes", "touch_plan", "awaits")

# 具备路径分隔语义的字符：ASCII 的 `/`/`\`/NUL，以及同形的 Unicode 同类。全角斜杠 `／`
# 在多数文件系统上仍是普通字符，但一旦被下游按分隔符处理就会拆出第二段路径——故与 ASCII
# 分隔符一视同仁**拒收**，而不是默默规范化（规范化会把危险名悄悄改成另一个名字）。
_PATH_SEPARATOR_LIKE: frozenset[str] = frozenset({
    "/", "\\", "\x00",
    "\uff0f",  # ／ 全角 SOLIDUS
    "\uff3c",  # ＼ 全角 REVERSE SOLIDUS
    "\u2044",  # ⁄ FRACTION SLASH
    "\u2215",  # ∕ DIVISION SLASH
    "\u29f8",  # ⧸ BIG SOLIDUS
    "\u29f9",  # ⧹ BIG REVERSE SOLIDUS
    "\ufe68",  # ﹨ SMALL REVERSE SOLIDUS
})


def validate_path_name(name: object, *, what: str) -> str:
    """校验一个**会进路径**的名字（树名 / 节点 id）：不许是路径形。

    树名与节点 id 共同构成 worktree 路径 `<state>/worktrees/<tree>/<node>`，两者都是路径段。
    放行含分隔符或 `.`/`..` 的名字，等于允许把 worktree 建到根目录**之外**（`_reject_if_inside`
    只挡「落在 repo 内」的路径，逃到 `/tmp` 这种仓外路径正好绕过它）。这是**写时**的唯一闸门
    ——坏名字进不了事实源；`worktree.path_for` 另有结构性兜底，见其 docstring。

    只拒这几种明确的路径形；**不**做字符集白名单（现有 id 形态多样：`n0`/`root`/`a`/十六进制），
    也**不**规范化（trim/替换）——危险名要拒收，不是悄悄改掉。
    """
    if not isinstance(name, str):
        raise ValueError(f"{what} 必须是字符串，得到 {type(name).__name__}：{name!r}")
    if not name.strip():
        raise ValueError(f"{what} 不得为空或纯空白：{name!r}（无法构成路径段）")
    if name in (".", ".."):
        raise ValueError(
            f"{what} 不得为 {name!r}（会指回上一级，把 worktree 建到根目录之外）")
    bad = sorted(set(name) & _PATH_SEPARATOR_LIKE)
    if bad:
        raise ValueError(
            f"{what} 不得含路径分隔符或 NUL：{name!r}（含 {''.join(bad)!r}，"
            "会把 worktree 建到根目录之外）")
    return name


def new_node(node_id: str, *, parent: str | None = None, kind: str = "task",
             stage: str = "dev", role: str = "dev", goal: str = "",
             context: dict | None = None, entry: str = "",
             points: float | None = None,
             acceptance: dict | None = None,
             touch_plan: list[str] | None = None,
             awaits: list[str] | None = None,
             requires: list[str] | None = None,
             relates: list[str] | None = None,
             produces: list[str] | None = None,
             consumes: list[str] | None = None) -> dict:
    """构造一个 status=pending 的节点（不校验、不入库）。

    四个边字段（`requires`/`relates`/`produces`/`consumes`）**必须在这里收下**：
    它们在 `validate_node_spec` 里是合法声明，若本函数不接，就会"声明被接受、然后静默丢弃"
    —— 实测后果是 `requires` 丢了 ⇒ 调度器**乱序派发**（E1 在 B3/B4 未完工时就被推）。

    `awaits`：跨树完成信号（等待别的树 `closed`）—— 不是图上的 `requires`。
    """
    if kind not in KINDS:
        raise ValueError(f"kind 非法：{kind!r}（应为 {KINDS}）")
    return {
        "id": node_id, "parent": parent, "kind": kind, "stage": stage, "role": role,
        "goal": goal, "context": dict(context or {}), "entry": entry, "points": points,
        "requires": list(requires or []), "relates": list(relates or []),
        "produces": list(produces or []), "consumes": list(consumes or []),
        "touch_plan": list(touch_plan or []), "awaits": list(awaits or []),
        "acceptance": dict(acceptance) if acceptance else None,
        "status": "pending", "lease": None, "evidence": None,
    }


def validate_points(value: object) -> float | None:
    """量化估点：None 放行（未估），正数归一为 float，其余**响亮拒绝**。

    为何 `bool`/`str` 也要拒：`True` 是 `int` 的子类，`points=True` 会被静默当成 1 点；
    字符串 `"3"` 不是数值却看着像。两者都会让求和悄悄出错，故与 0/负数一样拒。
    为何不静默丢弃非法值：写错点数却当成「没估」会让 velocity / eta 悄悄失真。

    为何用 `math.isfinite` 而非 `value <= 0` 判非法：`nan <= 0` 与 `inf <= 0` 都是 `False`，
    只判负会让 `NaN`/`+inf` 漏网、污染之后**全部**求和（`json.loads` 默认收 `NaN`，可端到端落盘）。
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"points 应为正数或 None（未估），得到 {value!r}")
    if not math.isfinite(value):
        raise ValueError(
            f"points 应为有限正数，得到 {value!r}（NaN/±inf 会污染求和，一律拒）")
    if value <= 0:
        raise ValueError(
            f"points 应为正数，得到 {value!r}（0 与负数无意义；不估请留 None）")
    return float(value)


def validate_node_spec(spec: dict) -> None:
    """校验 expand 传入的子节点声明：字段闭集 + 拒运行时字段 + 取值合法。"""
    unknown = set(spec) - NODE_FIELDS
    if unknown:
        raise ValueError(f"节点声明含未定义字段：{sorted(unknown)}"
                         f"（可声明：{sorted(DECLARABLE_FIELDS)}）")
    runtime = set(spec) & RUNTIME_FIELDS
    if runtime:
        raise ValueError(
            f"这些字段由运行时事件决定，不能在 expand 里声明：{sorted(runtime)}"
            "（声明了也不会生效——`parent` 由 expand 的父子关系决定；"
            "`status`/`lease` 走 start/fail/retry/skip/reap 动词；`evidence` 由 done 自算）")
    if "id" in spec:
        # 键在即校验：空串 `/`/`..` 一律拒（`id` 缺省时由 `next_id` 生成安全 id，不校验）。
        validate_path_name(spec["id"], what="节点 id")
    if spec.get("kind", "task") not in KINDS:
        raise ValueError(f"kind 非法：{spec.get('kind')!r}（应为 {KINDS}）")
    validate_points(spec.get("points"))
    for key in _LIST_FIELDS:
        value = spec.get(key)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"节点字段 {key} 应为 list，得到 {type(value).__name__}")


def next_id(nodes: dict) -> str:
    """生成下一个空闲节点 id（n0, n1, …）。确定性：只看现有 id，无随机。"""
    used = set(nodes)
    index = 0
    while f"n{index}" in used:
        index += 1
    return f"n{index}"


def children_of(nodes: dict, node_id: str) -> list[str]:
    return sorted(nid for nid, node in nodes.items() if node.get("parent") == node_id)


def ancestor_chain(nodes: dict, node_id: str) -> list[dict]:
    """root → … → parent 的节点列表（不含自身）。用于上下文继承。

    走 `parent` 单链；若检测到环（数据损坏）则报错而不是死循环。
    """
    chain: list[dict] = []
    seen: set[str] = set()
    current = nodes.get(node_id, {}).get("parent")
    while current:
        if current in seen:
            raise ValueError(f"parent 链成环：{node_id} → … → {current}")
        seen.add(current)
        node = nodes.get(current)
        if node is None:
            raise ValueError(f"{node_id} 的祖先 {current} 不存在")
        chain.append(node)
        current = node.get("parent")
    chain.reverse()
    return chain


def descendants(nodes: dict, node_id: str) -> list[str]:
    out: list[str] = []
    stack = children_of(nodes, node_id)
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(children_of(nodes, current))
    return out


def is_container_done(node: dict, nodes: dict) -> bool:
    """容器节点：全部子节点 done（或 skipped）即视为完成（派生量，不落库）。"""
    children = children_of(nodes, node["id"])
    if not children:
        return node.get("status") == "done"
    return all(nodes[c].get("status") in ("done", "skipped") for c in children)


def effective_status(node: dict, nodes: dict) -> str:
    """派生状态：容器由子节点推导，任务用存储态。"""
    if node.get("kind") == "container":
        if is_container_done(node, nodes):
            return "done"
        if any(nodes[c].get("status") == "running" for c in descendants(nodes, node["id"])):
            return "running"
        return "pending"
    return node.get("status", "pending")


def check_status(status: str) -> None:
    if status not in STORED_STATUS:
        raise ValueError(f"status 非法：{status!r}（应为 {STORED_STATUS}）")
