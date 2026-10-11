"""项目级配置：一层薄配置。

**为什么需要它**（python-expert 审查的“该配置化却硬编码”那一组）：这些值随机器/项目而变，
却写死在代码里 ⇒ 用户唯一的出路是改源码或绕过门。最尖锐的一条：
验收超时 `120s` 是**模块常量**，节点声明不了 ⇒ 跑 >2 分钟的验收**这道门永远过不去**，
只能写 `waiver`——那等于**把门关掉**。

**优先级**（后者覆盖前者，从低到高）：

    内建默认（本文件的 dataclass 默认值，取自 spec.py）
      < `disflow.json`（`$DISFLOW_CONFIG` 可指别处；缺失 = 全用内建默认）
        < 显式参数 / 节点声明（如 `acceptance.timeout`、`context.size_budget`、`--ttl`）

**只有两层来源是文件级+显式参数**，没有再叠一层“每个键一个环境变量”：那是几十个
互不相同的测试面，收益低。环境变量只用于**指配置在哪**（`DISFLOW_CONFIG`）。

**为什么机制词汇不进这里**：`EVENT_KINDS` 与 `state._HANDLERS` 必须逐个相等，
靠的是**编译/测试期**的保证（`tests/test_declarations.py`）；挪进配置就退化成
**运行期**才发现——配置错一个字母，坏的是一个事件流的可重放性。同理 `KINDS`/
`STORED_STATUS`（投影响应表）留在代码里。

**`roles` 为什么可以配**：它是**策略**（负责方怎么划分），不是机制——换个项目本来就该
不一样。但注意它**只在写时校验**（`Engine.expand`），**不在 reducer 里校验**：否则换台
机器（或改了配置）重放旧事件会直接失败，事件流就不可重放了。见 `disflow/engine.py` 的
`_validate_vocabulary`。

**`stages` 为什么搬走了**：看板定义（板 id + 人读标签）住在**独立文件 `boards.json`**
（见 `disflow/boards.py`）。理由：板定义会**长大**（要做动态看板），且它的变更节奏与
引擎参数（超时/预算/租约）不同。`Config.stages` 仍在，但已是**从 `boards` 派生的属性**
（不是字段）—— 于是所有既有消费者（写时校验、视图、CLI）一行都不用改，
而"唯一来源"变成了 `boards.json`。`disflow.json` 的 `stages` 键**已退役**：写了响亮报错。

**顺带长出来的两个派生属性**：`entry_stage`（root 容器落的板 = 板序第一块）与
`default_stage`（任务没声明 `stage` 时的默认板）。板定义一旦可配，这两个值就不能再是
写死的 `"req"` / `"dev"` —— 否则自定义板集下新节点会落在**不存在的板**上（视图只能把它
当「非规范列」追加，静默错位）。它们是**策略的派生**，权威仍是 `boards.json`。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace

from .boards import DEFAULT_BOARDS, Board, load_boards, boards_path
from .spec import (DEFAULT_STAGE, LEASE_TTL_SECONDS, QUESTION_POLICY_DEFAULT, ROLES,
                   SIZE_BUDGET_DEFAULT)

CONFIG_BASENAME = "disflow.json"
ENV_CONFIG_PATH = "DISFLOW_CONFIG"

# 默认忽略集：`fingerprint()` 递归一个目录时不该把版本库/依赖/缓存算进去
# （否则 touch_plan 写了一个含 node_modules 的目录 ⇒ 哈希整个依赖树）。
WALK_IGNORE_DEFAULT: tuple[str, ...] = (
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", "dist", "build",
)


@dataclass(frozen=True)
class Config:
    """解析后的配置。**不可变**——避免“配置在运行中被谁改了”这类难查的 bug。"""

    acceptance_timeout: float = 120.0
    """验收命令默认超时（秒）。节点可用 `acceptance.timeout` 覆盖。"""

    git_timeout: float = 20.0
    """git 探测命令超时（秒）。仓很大/很慢时调大，否则门会因“读不到改动集”而空洞通过。"""

    output_tail: int = 2000
    """证据里保留的命令输出尾部字符数。"""

    walk_ignore: tuple[str, ...] = WALK_IGNORE_DEFAULT
    """`fingerprint()` 递归目录时跳过的名字（任一层的目录名匹配即跳过）。"""

    size_budget: int = SIZE_BUDGET_DEFAULT
    """单节点改动行数预算的项目级默认（节点可用 `context.size_budget` 覆盖）。"""

    lease_ttl: int = LEASE_TTL_SECONDS
    """租约默认存活秒数（`start --ttl` 可覆盖）。"""

    boards: tuple[Board, ...] = DEFAULT_BOARDS
    """看板定义（板 = 阶段）。来源：`boards.json`（独立文件），缺失 = 内建默认。"""

    @property
    def stages(self) -> tuple[str, ...]:
        """允许的 `stage` 取值 = **板 id**（从 `boards` 派生，不另存一份）。

        保留这个名字是因为它已是既有消费者的公共面（写时校验 `engine._validate_vocabulary`、
        视图列、CLI `--stage` 校验）。派生而不是字段 ⇒ 不可能与 `boards` 分叉。
        """
        return tuple(board.id for board in self.boards)

    @property
    def labels(self) -> dict[str, str]:
        """板 id → 人读标签（**渲染**用；机器态不带标签）。"""
        return {board.id: board.label for board in self.boards}

    @property
    def entry_stage(self) -> str:
        """**流程入口板**（板序第一块）：root 容器落的板。

        root 是**树的自身目标**（不是一块待干的活），按流程语义该在入口 —— 视图里它就是
        第一列的结构行。写成派生属性而不是常量：板序换了，入口跟着换，不会分叉。
        """
        return self.boards[0].id

    @property
    def default_stage(self) -> str:
        """任务**没声明 `stage`** 时落的板。

        规则：内建默认板（`spec.DEFAULT_STAGE` = 「开发」，大多数节点是干活的）**是当前
        板集中的一块**就用它；否则回落 `entry_stage`（板序第一块）。

        为何要回落而不是报错：`expand` 不给 `stage` 是常见写法，板集自定义后一律报错会把
        整条路堵死。为何不静默用常量：那正是本次修掉的 bug —— 自定义板下写死 `"dev"`
        会让节点落在**一块不存在的板**上（视图只能把它当「非规范列」追加到末尾），
        而 `stage` 的闭集本该是板定义。回落到**入口板**是可推导的：新活从流程头开始。
        要精确控制就显式声明 `stage`（`--stage` / 节点字段），这条只兜「没说」的情况。
        """
        return DEFAULT_STAGE if DEFAULT_STAGE in self.stages else self.entry_stage

    roles: tuple[str, ...] = ROLES
    """允许的 `role` 取值。同上，只写时校验。"""

    question_policy: dict = field(default_factory=lambda: dict(QUESTION_POLICY_DEFAULT))
    """提问纪律（FR-15）：brief 写进 worker 契约。节点可用 `context.question_policy` 覆盖。"""


DEFAULTS = Config()


class ConfigError(ValueError):
    """配置本身有问题（未知键 / 类型不对）——要**响亮**，不能静默忽略。"""


def _as_str_tuple(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"disflow.json 的 {key!r} 应为字符串数组（或逗号分隔字符串），实际是 {value!r}")


def _as_policy(value: object, key: str) -> dict:
    """提问纪律：固定形状的对象。未知子键**响亮拒绝**（拼错了却静默丢掉 = 纪律没生效）。"""
    if not isinstance(value, dict):
        raise ConfigError(f"disflow.json 的 {key!r} 应为对象，实际是 {value!r}")
    allowed = {"ask_host", "decide_alone", "how"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"disflow.json 的 {key!r} 含未知子键：{unknown}（允许：{sorted(allowed)}）")
    out: dict = {}
    for sub in ("ask_host", "decide_alone"):
        if sub in value:
            raw = value[sub]
            if isinstance(raw, str):
                out[sub] = [p.strip() for p in raw.split(",") if p.strip()]
            elif isinstance(raw, list) and all(isinstance(v, str) for v in raw):
                out[sub] = list(raw)
            else:
                raise ConfigError(f"disflow.json 的 {key}.{sub!r} 应为字符串数组，实际是 {raw!r}")
    if "how" in value:
        if not isinstance(value["how"], str):
            raise ConfigError(f"disflow.json 的 {key}.how 应为字符串，实际是 {value['how']!r}")
        out["how"] = value["how"]
    return out


def _as_number(value: object, key: str, *, allow_float: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"disflow.json 的 {key!r} 应为数字，实际是 {value!r}")
    if value <= 0:
        raise ConfigError(f"disflow.json 的 {key!r} 应为正数，实际是 {value!r}")
    return float(value) if allow_float else float(int(value))


def _as_int(value: object, key: str) -> int:
    return int(_as_number(value, key, allow_float=False))


# `键 -> (校验器, 字段名)`：闭集。多一个键就报错（跟节点字段闭集同一个纪律：
# 写错了要报，不能“接受后静默忽略”）。
_VALIDATORS = {
    "acceptance_timeout": (lambda v: _as_number(v, "acceptance_timeout", allow_float=True), "acceptance_timeout"),
    "git_timeout": (lambda v: _as_number(v, "git_timeout", allow_float=True), "git_timeout"),
    "output_tail": (lambda v: _as_int(v, "output_tail"), "output_tail"),
    "walk_ignore": (lambda v: _as_str_tuple(v, "walk_ignore"), "walk_ignore"),
    "size_budget": (lambda v: _as_int(v, "size_budget"), "size_budget"),
    "lease_ttl": (lambda v: _as_int(v, "lease_ttl"), "lease_ttl"),
    "roles": (lambda v: _as_str_tuple(v, "roles"), "roles"),
    "question_policy": (lambda v: _as_policy(v, "question_policy"), "question_policy"),
}

# **已退役的键**：写了**响亮报错**并给迁移指引，**不静默回落**。
# 为何不回落：回落就是「同一件事两处定义」——用户在 disflow.json 里改了 `stages`，
# 而板实际由 boards.json 决定，他会以为配置生效了。静默失效比报错贵得多。
_RETIRED = {
    "stages": ('已迁到独立文件 boards.json —— 把 "stages": [...] 写成 boards.json 里的 '
               '{"boards": [{"id": "req", "label": "需求"}, ...]}（也可写纯字符串数组）；'
               "要用内建默认就删掉这一键"),
}

CONFIG_KEYS: tuple[str, ...] = tuple(sorted(_VALIDATORS))


def config_path(cwd: str) -> str:
    """配置文件位置：`$DISFLOW_CONFIG` 优先，否则 `<cwd>/disflow.json`。"""
    override = (os.environ.get(ENV_CONFIG_PATH) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.abspath(cwd), CONFIG_BASENAME)


def load(cwd: str) -> Config:
    """读配置。**两个文件都不存在 = 全用内建默认**（零配置可用是前提，不是错误）。

    未知键 / 类型不对 / 已退役的键 → `ConfigError`（附允许的键与迁移指引），不静默忽略。
    板定义另在 `boards.json`（`disflow/boards.py`）—— 本函数负责把两者**汇成一个** `Config`。
    """
    path = config_path(cwd)
    # 板定义先读：即使 disflow.json 缺失也要生效（两者独立，互不为前提）。
    boards = load_boards(cwd)
    if not os.path.isfile(path):
        return replace(DEFAULTS, boards=boards)
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path} 读不了：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} 顶层应为对象（键值对），实际是 {type(raw).__name__}")
    # 退役键先查：否则它们会先撞上「未知配置键」，读者拿到的是「拼错了」而不是迁移指引。
    retired = sorted(set(raw) & set(_RETIRED))
    if retired:
        hints = "；".join(f"{key!r} {_RETIRED[key]}" for key in retired)
        raise ConfigError(f"{path} 含**已退役**的配置键：{hints}")
    unknown = sorted(set(raw) - set(_VALIDATORS))
    if unknown:
        raise ConfigError(f"{path} 含未知配置键：{unknown}（允许：{list(CONFIG_KEYS)}）")
    overrides = {}
    for key, value in raw.items():
        validate, field = _VALIDATORS[key]
        overrides[field] = validate(value)
    return replace(DEFAULTS, boards=boards, **overrides)


def describe(cfg: Config, cwd: str) -> list[tuple[str, str, str]]:
    """`(键, 值, 来源)` 三列，供 `doctor` 报出“这个值从哪来”。

    **逐键报来源**：现在有两个配置文件（`boards.json` / `disflow.json`），来源不再唯一 ——
    报一个笼统的路径会让读者以为板定义也在 `disflow.json` 里。
    """
    path = config_path(cwd)
    source = path if os.path.isfile(path) else "内建默认"
    b_path = boards_path(cwd)
    b_source = b_path if os.path.isfile(b_path) else "内建默认"
    rows: list[tuple[str, str, str]] = [
        ("boards", " · ".join(f"{b.id}={b.label}" for b in cfg.boards), b_source),
    ]
    for key in CONFIG_KEYS:
        value = getattr(cfg, key)
        shown = ",".join(value) if isinstance(value, tuple) else str(value)
        rows.append((key, shown, source))
    return rows
