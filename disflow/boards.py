"""看板定义：**专门文件 `boards.json`**（一块板 = 一个阶段 = 节点 `stage` 的取值）。

**为什么从代码里搬出来**：四个看板（需求/设计/开发/验证）原本写死在 `spec.STAGES` 里，
连人读标签都写死在 `spec.STAGE_LABELS` 里。要做**动态看板**（按用户需求按需建板），
板定义就必须先是**数据** —— 否则「新建一块板」等于「改源码」。

**为什么是独立文件**（而不是塞进 `disflow.json` 的 `boards` 键）：同 `disflow.json`
的那条理由 —— 配置是**策略**（人写、进版本库、要 review），状态是**派生**。再加一层：
板定义会**长大**（动态建板后可能是几十块），和引擎参数（超时/预算/租约）混在一个文件里，
两种变更节奏会被绑在一起，review 也看不清「这次改的是引擎还是板」。

**单一来源**：本文件的 `id` 列表就是 `stage` 取值的**唯一闭集**（`Config.stages` 由它派生）。
`disflow.json` 的 `stages` 键已**退役** —— 写了会**响亮报错**并给迁移指引。
同一件事两处定义 = 双源，正是本仓批判 mgt 的 P1 病根。

**零配置可用**：文件不存在 ⇒ 回落内建默认（`spec.STAGES` + `spec.STAGE_LABELS`），
与 `disflow.json` 缺失即全用默认同一条纪律。

文件形状（两种都收，简单场景不必写对象）：

    {"boards": [{"id": "req", "label": "需求"}, {"id": "dev", "label": "开发"}]}
    {"boards": ["req", "dev"]}                    # label 回落成 id

**顺序就是板的顺序**（数组序 = 板的先后）——不另设 `order` 字段：那会造出
「数组序」与「order 值」两份顺序，谁说了算就成了新问题。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .spec import STAGE_LABELS, STAGES

BOARDS_BASENAME = "boards.json"
ENV_BOARDS_PATH = "DISFLOW_BOARDS"

# 顶层允许的键（闭集）：多一个就报错 —— 与 `disflow.json` 的未知键拒收同一条纪律。
_ALLOWED_TOP = ("boards",)
# 板条目的允许子键（闭集）。
_ALLOWED_ENTRY = ("id", "label", "wip", "acceptance")

# `done` 是**节点状态**（`core.TERMINAL`）不是板：同名板会被读者当成「干完了」那一列，
# 而它按 stage 分组，大半节点状态是 done、板里却是 0 —— 一个永远令人困惑的空板。
RESERVED_IDS: tuple[str, ...] = ("done",)


@dataclass(frozen=True)
class Board:
    """一块板（= 一个阶段）。`id` 同时是节点的 `stage` 取值。

    **看板是一等实体**（ADR 0005）：除身份（`id`/`label`）外，板还挂两类策略——它们是
    「看板附属」的机制落地：

    - `wip`（FR-30，分发策略）：该列**并发在跑**任务的上限，`None` = 不限；
      `dispatch.drive` 消费它（列满则把其余就绪者移进 `deferred`）。
    - `acceptance`（FR-31，验收策略）：该列的**默认验收**，节点未自带 `acceptance` 时由它派生；
      形状与节点 `acceptance` 相同（`cmd`/`expect`/`timeout`/`waiver`/`require_declarations`）。
    """

    id: str
    label: str
    wip: int | None = None
    acceptance: dict | None = None


class BoardsError(ValueError):
    """板定义本身有问题（形状不对 / 重复 id / 用了保留名）——要**响亮**，不静默忽略。"""


def default_boards() -> tuple[Board, ...]:
    """内建默认：`spec.STAGES` + `spec.STAGE_LABELS`（零配置时的回落值）。"""
    return tuple(Board(id=sid, label=STAGE_LABELS.get(sid, sid)) for sid in STAGES)


DEFAULT_BOARDS: tuple[Board, ...] = default_boards()


def dump(boards: tuple[Board, ...]) -> list[dict]:
    """序列化进**事件流**（`tree.init`/`tree.boards` 携带 → 投影携带）。

    为何要把板定义写进事件而不是每次读文件：文件是**进程级**的（同一棵树在不同机器/不同
    时刻可能读到不同 `boards.json`）—— 那样重放旧事件流会得到不同结果，**事件流就不可重放了**。
    写进事件 ⇒ 板定义成为该树的**事实**，与配置解耦。

    策略字段（`wip`/`acceptance`）**仅在非空时写出**：零配置 / 旧板条目仍是最小的
    `{"id","label"}`，不凭空多出字段（向后兼容 + 重放逐字不变）。
    """
    out: list[dict] = []
    for board in boards:
        item: dict = {"id": board.id, "label": board.label}
        if board.wip is not None:
            item["wip"] = board.wip
        if board.acceptance:
            item["acceptance"] = board.acceptance
        out.append(item)
    return out


def from_projection(raw: object) -> tuple[Board, ...]:
    """从投影的 `tree[\"boards\"]` 还原板定义。

    **读时容忍**（与视图对未知 stage 容忍同一纪律）：只提取能认的形状，非法项跳过；
    空/缺失 ⇒ 空元组（调用方回落”——旧事件流不带 boards，得能读）。
    """
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    boards: list[Board] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            board_id = str(item.get("id") or "").strip()
            label = str(item.get("label") or "").strip()
            raw_wip = item.get("wip")
            wip = (raw_wip if isinstance(raw_wip, int) and not isinstance(raw_wip, bool)
                   and raw_wip >= 1 else None)
            raw_acc = item.get("acceptance")
            acceptance = dict(raw_acc) if isinstance(raw_acc, dict) else None
        elif isinstance(item, str):
            board_id, label, wip, acceptance = item.strip(), "", None, None
        else:
            continue
        if not board_id or board_id in seen:
            continue
        seen.add(board_id)
        boards.append(Board(id=board_id, label=label or board_id,
                            wip=wip, acceptance=acceptance))
    return tuple(boards)


def boards_path(cwd: str) -> str:
    """板定义文件位置：`$DISFLOW_BOARDS` 优先，否则 `<cwd>/boards.json`。"""
    override = (os.environ.get(ENV_BOARDS_PATH) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.abspath(cwd), BOARDS_BASENAME)


def _wip(raw: object, where: str) -> int | None:
    """逐列 WIP 上限（FR-30）：正整数的**并发在跑**任务上限；缺省/None = 不限。"""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise BoardsError(f"{where} 的 'wip' 应为 ≥1 的整数（并发上限），实际是 {raw!r}")
    return raw


def _acceptance(raw: object, where: str) -> dict | None:
    """逐列默认验收（FR-31）：节点未自带 `acceptance` 时由它派生。形状 = 节点 acceptance。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BoardsError(f"{where} 的 'acceptance' 应为对象，实际是 {type(raw).__name__}")
    return dict(raw)


def _entry(raw: object, path: str, index: int) -> Board:
    """单个板条目：字符串 = 只有 id（label 回落成 id）；对象 = id + 可选 label/wip/acceptance。"""
    where = f"{path} 的 boards[{index}]"
    wip: int | None = None
    acceptance: dict | None = None
    if isinstance(raw, str):
        board_id, label = raw.strip(), ""
    elif isinstance(raw, dict):
        unknown = sorted(set(raw) - set(_ALLOWED_ENTRY))
        if unknown:
            raise BoardsError(f"{where} 含未知子键：{unknown}（允许：{list(_ALLOWED_ENTRY)}）")
        if "id" not in raw:
            raise BoardsError(f"{where} 缺 'id'（板的 id 同时是节点的 stage 取值）")
        raw_id = raw["id"]
        if not isinstance(raw_id, str):
            raise BoardsError(f"{where} 的 'id' 应为字符串，实际是 {raw_id!r}")
        raw_label = raw.get("label", "")
        if not isinstance(raw_label, str):
            raise BoardsError(f"{where} 的 'label' 应为字符串，实际是 {raw_label!r}")
        board_id, label = raw_id.strip(), raw_label.strip()
        wip = _wip(raw.get("wip"), where)
        acceptance = _acceptance(raw.get("acceptance"), where)
    else:
        raise BoardsError(f"{where} 应为字符串或对象，实际是 {type(raw).__name__}")
    if not board_id:
        raise BoardsError(f"{where} 的 id 不能为空（空 id 会造出一块没有名字的板）")
    if board_id in RESERVED_IDS:
        raise BoardsError(
            f"{where} 的 id {board_id!r} 是**节点状态**不是板（`done` 表达「干完了」，"
            f"用 status 表达；见 spec.py 里 STAGES 的注释）")
    return Board(id=board_id, label=label or board_id, wip=wip, acceptance=acceptance)


def parse(raw: object, path: str = BOARDS_BASENAME) -> tuple[Board, ...]:
    """把已解析的 JSON 值变成板列表。**形状错误一律拒收**，不猜、不补、不忽略。"""
    if not isinstance(raw, dict):
        raise BoardsError(
            f"{path} 顶层应为对象（形如 {{\"boards\": [...]}}），实际是 {type(raw).__name__}"
            + ("（顶层直接写数组请包一层：{\"boards\": [...]}）" if isinstance(raw, list) else ""))
    unknown = sorted(set(raw) - set(_ALLOWED_TOP))
    if unknown:
        raise BoardsError(f"{path} 含未知键：{unknown}（允许：{list(_ALLOWED_TOP)}）")
    entries = raw.get("boards")
    if not isinstance(entries, list):
        raise BoardsError(f"{path} 的 'boards' 应为数组，实际是 {type(entries).__name__}")
    if not entries:
        raise BoardsError(f"{path} 的 'boards' 是空数组 —— 没有板就没有看板；"
                          f"要内建默认就删掉这个文件")
    boards: list[Board] = []
    seen: set[str] = set()
    for index, item in enumerate(entries):
        board = _entry(item, path, index)
        if board.id in seen:
            raise BoardsError(
                f"{path} 的 boards[{index}] id 重复：{board.id!r}"
                "（板 id 是 stage 取值的闭集，重复会让同一个 stage 落在两块板上）")
        seen.add(board.id)
        boards.append(board)
    return tuple(boards)


def load_boards(cwd: str) -> tuple[Board, ...]:
    """读板定义。**文件不存在 = 内建默认**（零配置可用是前提，不是错误）。"""
    path = boards_path(cwd)
    if not os.path.isfile(path):
        return DEFAULT_BOARDS
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise BoardsError(f"{path} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise BoardsError(f"{path} 读不了：{exc}") from exc
    return parse(raw, path)
