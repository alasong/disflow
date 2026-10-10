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

**`stages`/`roles` 为什么可以配**：它们是**策略**（看板列名、负责方怎么划分），
不是机制——换个项目本来就该不一样。但注意它们**只在写时校验**（`Engine.expand`），
**不在 reducer 里校验**：否则换台机器（或改了配置）重放旧事件会直接失败，
事件流就不可重放了。见 `disflow/engine.py` 的 `_validate_vocabulary`。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace

from .spec import ROLES, SIZE_BUDGET_DEFAULT, STAGES, LEASE_TTL_SECONDS

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

    stages: tuple[str, ...] = STAGES
    """允许的 `stage` 取值 = 看板列。**写时校验**，reducer 不校验（保事件流可重放）。"""

    roles: tuple[str, ...] = ROLES
    """允许的 `role` 取值。同上，只写时校验。"""


DEFAULTS = Config()


class ConfigError(ValueError):
    """配置本身有问题（未知键 / 类型不对）——要**响亮**，不能静默忽略。"""


def _as_str_tuple(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return tuple(value)
    raise ConfigError(f"disflow.json 的 {key!r} 应为字符串数组（或逗号分隔字符串），实际是 {value!r}")


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
    "stages": (lambda v: _as_str_tuple(v, "stages"), "stages"),
    "roles": (lambda v: _as_str_tuple(v, "roles"), "roles"),
}

CONFIG_KEYS: tuple[str, ...] = tuple(sorted(_VALIDATORS))


def config_path(cwd: str) -> str:
    """配置文件位置：`$DISFLOW_CONFIG` 优先，否则 `<cwd>/disflow.json`。"""
    override = (os.environ.get(ENV_CONFIG_PATH) or "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.abspath(cwd), CONFIG_BASENAME)


def load(cwd: str) -> Config:
    """读配置。文件不存在 = 全用内建默认（**不是错误**：零配置可用是前提）。

    未知键 / 类型不对 → `ConfigError`（附允许的键），不静默忽略。
    """
    path = config_path(cwd)
    if not os.path.isfile(path):
        return DEFAULTS
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} 不是合法 JSON：{exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path} 读不了：{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} 顶层应为对象（键值对），实际是 {type(raw).__name__}")
    unknown = sorted(set(raw) - set(_VALIDATORS))
    if unknown:
        raise ConfigError(f"{path} 含未知配置键：{unknown}（允许：{list(CONFIG_KEYS)}）")
    overrides = {}
    for key, value in raw.items():
        validate, field = _VALIDATORS[key]
        overrides[field] = validate(value)
    return replace(DEFAULTS, **overrides)


def describe(cfg: Config, cwd: str) -> list[tuple[str, str, str]]:
    """`(键, 值, 来源)` 三列，供 `doctor` 报出“这个值从哪来”。"""
    path = config_path(cwd)
    source = path if os.path.isfile(path) else "内建默认"
    rows: list[tuple[str, str, str]] = []
    for key in CONFIG_KEYS:
        value = getattr(cfg, key)
        shown = ",".join(value) if isinstance(value, tuple) else str(value)
        rows.append((key, shown, source))
    return rows
