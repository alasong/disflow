#!/usr/bin/env python3
"""disflow · 存储层（L0）—— **vendored from fat3 scripts/flowcore/store.py**。

来源：fat3 `scripts/flowcore/store.py`（语言中立、零第三方依赖）。按 R1 决策只 vendor 本文件，
其余模块在 disflow 内重写。保持语言中立（不 import 业务模块）。

--- 原文说明 ---

通用工作流引擎 lib · **存储面**（L0：路径/锁/原子写/JSONL/事件追加/投影读写/重建）。

事件流是唯一真相，投影是派生缓存。本模块不认识任何业务名词 —— 只按 storage（机制数据）
取路径、落一条事件、刷新投影；投影形状（事件流 → 状态）由调用方注入的 `project` 提供。

**两层分工**：
  - 无 IO 纯原语：`now_iso` / `parse_ts` / `pctl` / `compare_projection`（逐键纯 diff）
  - 带 IO 与注入的落盘面：`state_dir` / `events_path` / `projection_path` / `lock` /
    `load_events` / `append_event` / `checkpoint` / `write_projection` / `rebuild` / `projection_matches`
`projection_matches` 内部调用 `compare_projection` 做纯 diff —— 高阶层带 IO 与注入，原语层无 IO。

**disflow 侧的偏离（与本仓保持一致所需，两处）**：
1. `checkpoint` / `read_projection_offset` 等随库带过来，但**本仓零调用方**（保留是为了
   vendored API 对等，方便日后与上游重新同步；不是功能缺口）。审查（python-expert 第 1 类）
   把这种“带过来但不用”的标注出来，以免读者以为有第二个真相源。
2. `file_lock` 里的 `fcntl` 改成了**惰性导入**：顶层导入会让非 POSIX 平台
   连 `import disflow` 都失败（只读动词本来是可用的）。

**为什么投影由调用方注入**：引擎是通用的，投影形状是每实例机制（不同实例的状态不是同一个
 dict）。把 reducer 当参数传入，避免 lib 把某个实例的投影 schema 硬编码进框架 —— 这正是
「机制零存储细节」的要求。`append_event` 的 `project` **必填**：不给默认值，避免造出
「忘传就静默不刷新」的第二条安静路径。

**零第三方依赖**：只用标准库。`read_jsonl` 遇坏行**停下**（不静默跳过），
原因见函数内注释：跳过会让历史与指标悄悄变少。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone

# 投影的**控制键**：自述「本投影由事件流的哪个前缀建成」。它是元数据，不是状态 ——
# `compare_projection`（无 IO 纯原语）不认识它，剔除由带 IO 的调用方做。
PROJECTION_OFFSET = "projection_offset"

__all__ = [
    "now_iso", "parse_ts", "pctl",
    "write_json_atomic", "read_json",
    "append_jsonl", "read_jsonl",
    "file_lock", "persist_event", "compare_projection",
    "state_dir", "events_path", "projection_path", "lock",
    "load_events", "append_event", "checkpoint",
    "write_projection", "rebuild", "projection_matches",
    "PROJECTION_OFFSET", "stamp_projection", "read_projection_offset", "batch_append",
    "build_event",
]


def now_iso() -> str:
    """UTC ISO8601，微秒精度（事件时间戳：全 lib 唯一一份）。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def parse_ts(text: str) -> float:
    """ISO 时间戳 → epoch 秒（全 lib 唯一副本，bus/jobs 类调用点都走这里）。"""
    return datetime.fromisoformat(text).timestamp()


def pctl(values: Sequence[float], q: float) -> float | None:
    """分位数：空输入 → None（不返回 0，避免「没有样本」被读成「样本为 0」）。"""
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[index], 3)


def write_json_atomic(path: str, payload, **dump) -> str:
    """**原子**写 JSON：同目录临时文件 + `os.replace` —— 读者要么看旧版本、要么看新版本，
    绝不半写（并发会话不崩）。序列化/替换失败时清掉临时件，不留残骸。"""
    directory = os.path.dirname(path)
    os.makedirs(directory or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory or ".", prefix=".tmp-", suffix=".json")
    try:
        with open(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, **dump)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):              # 只有 os.replace 成功才消失；失败时清掉临时件
            os.unlink(tmp)
    return path


def read_json(path: str) -> dict:
    """读 JSON 对象；文件不存在 → `{}`（分态：从未写过 ≠ 内容为空）。"""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def append_jsonl(path: str, record) -> None:
    """O_APPEND 原子追加一行 JSON（单行 < PIPE_BUF 时内核保证原子；多进程并发不撕裂）。
    落盘失败**上抛** OSError（不静默、不降级）—— 调用方据此判定「本条命令不能算成功」。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as exc:
        raise OSError(f"事件未落盘（{record.get('kind')}）：{exc} —— 本条命令不能算成功") from exc


def read_jsonl(path: str) -> list[dict]:
    """逐行读 JSONL；文件不存在 → `[]`；空行跳过（最后一行常带空行）。

    某行**不是合法 JSON 就 raise**（不是跳过）：静默跳过会让历史与指标悄悄变少 ——
    故在此停下而不是容错。
    """
    if not os.path.isfile(path):
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"事件文件损坏：{path} 第 {lineno} 行不是合法 JSON（{exc}）"
                    " —— 静默跳过会让历史与指标悄悄变少，故在此停下") from exc
    return rows


@contextlib.contextmanager
def file_lock(directory):
    """跨进程互斥（`flock`）：给「读判定 → 写事件」**整段**用，否则两个会话能各判一次都放行
    （check-then-act）。同进程重复取锁会自锁 ⇒ 调用方自己不要嵌套取锁；异常路径靠 fd 关闭释放。

    为何 `fcntl` 是**函数内惰性导入**：它在模块顶层导入会让整个包在 Windows 上
    `import disflow` 就失败——只读动词本来是可用的。惰性化后：能 import、能读、
    只在真要写时才报一句能看懂的错。**不静默降级为不加锁**：那会变成并发写没保护。
    （本文件是 vendored 代码，此为 disflow 侧的偏离，见文件头。）
    """
    try:
        import fcntl                     # 仅 POSIX；Windows 走到下面那句报错
    except ImportError as exc:           # pragma: no cover - 非 POSIX 平台
        raise OSError(
            "disflow 的跨进程加锁依赖 POSIX `fcntl.flock`，本平台不支持"
            "——写操作无法安全进行（读操作不受影响）。"
            "Windows 的 `msvcrt.locking` 实现尚未提供，已登记在 docs/v0.2-backlog.md") from exc
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "lock")
    with open(path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def persist_event(*, events_file, projection_file, record, validate=None, reduce_) -> dict:
    """唯一落盘 + 刷新点：先（可选）校验 → O_APPEND 落一行 → 随即用 `reduce_()` 重算投影并原子写，
    并给投影盖自述偏移 `projection_offset = len(events)`（与正文**同一次原子写**落盘）。

    事件是唯一真相；投影是**自述偏移的前缀派生缓存**。落盘不变式（hard）：磁盘投影正文必须
    恰好等于 `project(events[:projection_offset])`。刷新后 lag=0（存活性由本写点负责）。
    失败分态（混在一句里会把诊断说反）：
      - append 失败 → `事件未落盘（kind）… 本条命令不能算成功`（由 `append_jsonl` 抛出）
      - 投影失败   → `事件已落盘（kind）但投影未刷新… 可由 rebuild 重建`（事件已改，故不谎称全失败）
    `validate(record)` 的异常**原样上抛**（形状校验先于落盘，校验不过不写）。
    """
    if validate is not None:
        validate(record)
    append_jsonl(events_file, record)          # 失败已带「事件未落盘（<kind>）」措辞，直接上抛
    try:                                        # 投影失败 ≠ 事件失败
        write_json_atomic(projection_file, stamp_projection(reduce_(), _count_lines(events_file)))
    except Exception as exc:
        raise OSError(
            f"事件已落盘（{record.get('kind')}）但投影未刷新：{exc} —— 可由调用方的 rebuild 重建"
        ) from exc
    return record


def stamp_projection(state: dict, offset: int) -> dict:
    """给投影正文盖**控制键** `projection_offset`（自述偏移），返回新 dict（不改入参）。
    只由写点调用（`_persist` / `persist_event` / `batch_append`）—— 与正文
    同一次原子写落盘，故声明偏移与正文不可能不同步。"""
    return dict(state) | {PROJECTION_OFFSET: offset}


def _count_lines(path: str) -> int:
    """数事件行数（与 `read_jsonl` 同口径：跳过空行）—— 只为给投影盖自述偏移，不解析 JSON。"""
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def compare_projection(recomputed: dict, on_disk: dict) -> tuple[bool, list[str]]:
    """投影一致性：重算结果 vs 磁盘投影（逐顶层键）。

    **分态**：两者皆空 = 从未开始 → 一致；有事件（recomputed 非空）却无磁盘投影 = 真漂移
    （缓存被删）；其余逐顶层键比，只列不一致的键。
    """
    if not recomputed and not on_disk:
        return True, []
    if not on_disk:
        return False, ["磁盘上没有投影（state.json 缺失）"]
    if recomputed == on_disk:
        return True, []
    diffs: list[str] = []
    for key in sorted(set(recomputed) | set(on_disk)):
        if recomputed.get(key) != on_disk.get(key):
            diffs.append(f"{key}: 重算={str(recomputed.get(key))[:60]} 磁盘={str(on_disk.get(key))[:60]}")
    return False, diffs


# ── storage 感知面（带 IO 与注入）：路径 / 锁 / 事件追加 / 投影读写 / 重建 / 比对 ──────


def state_dir(storage: dict) -> str:
    """状态目录：`[state].env` 指名的环境变量优先（多会话/多仓库隔离），否则 `[state].dir`。
    两键**直取**（缺即 KeyError）：storage 是唯一来源，不留代码默认副本。"""
    cfg = storage["state"]
    return os.path.expanduser(os.environ.get(cfg["env"]) or cfg["dir"])


def events_path(storage: dict) -> str:
    """事件流落点：`<state dir>/[state].events`。"""
    return os.path.join(state_dir(storage), storage["state"]["events"])


def projection_path(storage: dict) -> str:
    """投影落点：`<state dir>/[state].projection`。"""
    return os.path.join(state_dir(storage), storage["state"]["projection"])


@contextlib.contextmanager
def lock(storage: dict):
    """跨进程互斥：给「读判定 → 写事件」**整段**用，否则两个会话能各判一次都放行
    （check-then-act）。锁本体是 `file_lock`（同一把 flock），落点 `<state dir>/lock`；
    同进程重复取锁会自锁 ⇒ 调用方自己不要嵌套取锁。"""
    with file_lock(state_dir(storage)):
        yield


def load_events(storage: dict) -> list[dict]:
    """读事件流：缺文件 → `[]`，坏行 raise（不静默跳过，文案由 `read_jsonl` 给）。"""
    return read_jsonl(events_path(storage))


def _validate_record(storage: dict, record: dict) -> None:
    """事件形状校验（**数据驱动**：模板取自 storage，不写死任何 kind）：kind 有模板即校验 ——
    信封面（`[state].event_base`）与 per-kind 增量模板合并后再比；缺必填 / 出现未声明字段
    → 报错（不是静默写）；无模板的 kind 交给调用方的闭集机检。"""
    state_cfg = storage.get("state") or {}
    tpl = (state_cfg.get("event_fields") or {}).get(record.get("kind", ""))
    if not isinstance(tpl, dict):
        return
    fields = set(record) - set(state_cfg.get("event_base") or [])
    required = set(tpl.get("required") or [])
    missing = required - fields
    unknown = fields - required - set(tpl.get("optional") or [])
    if missing or unknown:
        raise ValueError(f"事件 {record.get('kind')} 字段不符模板："
                         f"缺必填 {sorted(missing)}、未声明 {sorted(unknown)}")


def write_projection(storage: dict, state: dict) -> str:
    """**原子**写投影到 `<state dir>/[state].projection`，**恒返回路径**（归一化：调用方不需要 None）。"""
    return write_json_atomic(projection_path(storage), state, indent=2, sort_keys=False)


def rebuild(storage: dict, *, project, offset: int | None = None) -> dict:
    """从事件流重算投影（可选只重算到某偏移，用于 restore）。
    `project`（事件流 → 状态）由调用方注入 —— 投影 schema 归调用方，lib 不认识它。"""
    events = load_events(storage)
    if offset is not None:
        events = events[:offset]
    return project(events)


def _persist(storage: dict, record: dict, project) -> dict:
    """唯一落盘+刷新点：形状校验 → **先重投影试算（语义闸门）** → O_APPEND 落一行 → 原子写投影。

    **为何先试算（曾是真缺陷的修法）**：`project` 就是语义校验点（重复 id / 环 / 悬空引用
    都在这里抛）。若先落盘再重投影，**被拒的事件会留在事件流里**，使此后任何重投影
    （rebuild / 下一次写）都失败——即「一次非法写入 = 状态永久不可恢复」。
    先试算则非法输入**一条事件都不落**，状态零变化。

    失败分态（混在一句里会把诊断说反）：
    - 形状非法 / **语义非法**（project 抛）→ 未落盘，上抛（可安全重试）。
    - append 失败 → `append_jsonl` 抛「事件未落盘（kind）…」。
    - 投影写盘失败 → 「事件已落盘（kind）但投影未刷新… 可由 rebuild 重建」（事件合法，不谎称全失败）。
    """
    _validate_record(storage, record)
    events = load_events(storage)
    candidate = events + [record]
    # 语义闸门：非法即抛，此时**尚未落盘**（这是「事件流永远可重放」的保证）
    state = stamp_projection(project(candidate), len(candidate))
    append_jsonl(events_path(storage), record)     # 失败已带「事件未落盘（<kind>）」措辞，直接上抛
    try:
        write_projection(storage, state)
    except OSError as exc:
        raise OSError(
            f"事件已落盘（{record.get('kind')}）但投影未刷新：{exc} —— 可由 rebuild 重建"
        ) from exc
    return record


def build_event(kind: str, *, cmd: str = "", args: list | None = None, exit_code: int = 0,
                slug: str = "", node: str = "", layer: str = "root",
                extra: dict | None = None) -> dict:
    """构造一条**事件记录**（不落盘、不加投影）：信封面（`ts/kind/cmd/args/exit/slug/layer`）
    + `node` 非空才入键 + `extra` 是 per-kind 增量字段。

    即时写点 `append_event` 与相界批量写（`tree.batch_phase` 攒批后交 `batch_append`）**共用**
    这一个信封构造处 —— 两条路径产出的记录逐字段相同，不会各写一份而漂移。
    构造无 IO，故不接 `storage`（形状校验在各自写点里做）。"""
    event = {
        "ts": now_iso(), "kind": kind, "cmd": cmd, "args": list(args or []),
        "exit": exit_code, "slug": slug, "layer": layer,
    }
    if node:
        event["node"] = node
    if extra:
        event.update(extra)
    return event


def append_event(storage: dict, kind: str, *, project, cmd: str = "", args: list | None = None,
                 exit_code: int = 0, slug: str = "", node: str = "", layer: str = "root",
                 extra: dict | None = None) -> dict:
    """append 一条事件，**并立即刷新投影到 `len(events)`**（唯一写点：任何调用方都不可能忘）。
    刷新后投影自述 `projection_offset = len(events)`，落盘不变式 = 正文恰好等于该前缀的重算（lag=0）。

    信封面由 `build_event` 统一构造（与相界批量写共用）；`extra` 是 per-kind 增量字段。`project` **必填**：没有它就无法重算投影 —— 不给默认值（要只落事件请直接用
    `append_jsonl`，那是更底层的原语）。写失败**上抛** `OSError`（不静默、不降级）。"""
    return _persist(
        storage,
        build_event(kind, cmd=cmd, args=args, exit_code=exit_code, slug=slug, node=node,
                    layer=layer, extra=extra),
        project,
    )


def checkpoint(storage: dict, name: str = "") -> dict:
    """记当前事件偏移（行数）到 `<state dir>/[state].checkpoints/<name>.json`：只记偏移，
    不改投影、不写事件。缺省名用 UTC 时间戳。返回 `{"offset", "name", "ts"}`。"""
    events = load_events(storage)
    directory = os.path.join(state_dir(storage), storage["state"]["checkpoints"])
    label = name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {"name": label, "offset": len(events), "ts": now_iso()}
    write_json_atomic(os.path.join(directory, f"{label}.json"), record, indent=2)
    return record


def projection_matches(storage: dict, *, project, offset: int | None = None) -> tuple[bool, list[str]]:
    """投影一致性：注入的 `project` 重算结果 vs 磁盘投影（纯 diff 委派 `compare_projection`）。

    判据：**正文必须恰好等于 `project(events[:projection_offset])`**。`offset=None`（三处读路径的
    缺省）→ 解析磁盘投影**自述**的 `projection_offset`；**字段缺失（旧格式）回落 `len(events)`** ——
    旧写路径恒全量重算、产不出滞后，故「无字段」与「全量新鲜」在旧数据上等价（逐字节同判）。
    `offset=` 仍是**显式覆盖**（供 restore / 审计比对某前缀）。
    控制键 `projection_offset` 在**本调用方**从正文剔除，`compare_projection`（原语）不认识 schema。
    lag>0 但正文忠实于声明的旧前缀 = **合法滞后** → 判 `True`（lag 用 `read_projection_offset` 读出）；
    正文 ≠ 自己声明前缀处的重算 = **漂移** → 判 `False`。

    **分态**：无事件且无磁盘投影 = 从未开始 → 一致；有事件却无投影 = 真漂移（缓存被删）；
    自述偏移非法/超出事件流 = fail-closed 判漂移（宁可误杀滞后，绝不放过损坏）。
    该分支留在这里：空事件的重算结果通常非空，直接交 `compare_projection` 会把「全新环境」
    误报成漂移。两层分工：本函数带 IO 与注入，`compare_projection` 只做无 IO 的逐键纯 diff。"""
    events = load_events(storage)
    on_disk = read_json(projection_path(storage))
    if not events and not on_disk:
        return True, []
    if not on_disk:
        return False, ["磁盘上没有投影（state.json 缺失）"]
    if offset is None:
        claimed = on_disk.get(PROJECTION_OFFSET, len(events))
        if not isinstance(claimed, int) or isinstance(claimed, bool) or not 0 <= claimed <= len(events):
            return False, ["projection_offset 非法/超出事件流（投影自述偏移不可信）"]
    else:
        claimed = offset
    body = {key: value for key, value in on_disk.items() if key != PROJECTION_OFFSET}
    return compare_projection(project(events[:claimed]), body)


def read_projection_offset(storage: dict) -> int | None:
    """读投影自述的 `projection_offset`：缺失（旧格式）→ `None`。
    供调用方把 `lag = len(events) - offset` 作为**信息**报出；判定本身在 `projection_matches` 里。"""
    claimed = read_json(projection_path(storage)).get(PROJECTION_OFFSET)
    return claimed if isinstance(claimed, int) and not isinstance(claimed, bool) else None


def batch_append(storage: dict, records: Iterable[dict], *, project) -> dict:
    """**批次落事件**：批内逐条 O(1) `append_jsonl`，**批末恰好一次**全量重算 + 原子写投影，
    并给投影盖自述偏移 `projection_offset = 批末的 len(events)`（与正文同一次原子写落盘）。

    **为什么必须是具名函数，而不是「叫调用方自己循环 `append_jsonl` + 批末 `rebuild`」**：
    `_persist` 的 eager 写入点存在的全部意义就是消灭「调用方会忘」；若去抖要求调用方自己记住
    「批次内绕开 `_persist`、批末自己 rebuild」，就把刚消掉的失效模式请了回来。故去抖逻辑封成
    这一个具名写点：调用方只描述「这一批事件」，刷新责任仍留在唯一的落盘处。

    **失败分态**（与既有单条写点同款措辞，别混）：
      - 某条 append 失败 → `append_jsonl` 抛「事件未落盘（kind）…」，该条不能算成功；
      - 全批 append 成功但批末刷投影失败 → 「批次已落盘 N 条但投影未刷新… 可由 rebuild 重建」
        （事件已改，故不谎称全失败）。
    **部分失败**：批到第 k 条异常时，前 k-1 条事件**已在盘上**（事件是唯一真相，不回收），投影停在
    更早的偏移 —— 这是契约允许的**合法滞后**（`projection_matches` 判 `matched=True`，
    `lag = len(events) - projection_offset` 可读出），不是漂移；下一次写点或 `state rebuild` 会追平。

    锁：与单条写点一致，**由调用方**在「读判定 → 写事件」整段持 `store.lock`；本函数不自锁。
    `project` 必填（与 `append_event` 同规，不给默认值，以免造出静默不刷新的第二路径）。
    返回 `{"appended": 本批落盘条数, "projection_offset": 批末事件数}`。

    **注意**：disflow 目前不为任何调用方（`tree.batch_phase` 那套批处理未落地），
    但仍采用与 `_persist` 同款的**先试算后落盘**语义，以免成为哑雷。"""
    pending = list(records)
    events = load_events(storage)
    # 语义闸门（与 _persist 同款）：先整批试算，非法则**一条都不落**
    candidate = stamp_projection(project(events + pending), len(events) + len(pending))
    appended = 0
    for record in pending:
        _validate_record(storage, record)
        append_jsonl(events_path(storage), record)   # 失败已带「事件未落盘（<kind>）」措辞，直接上抛
        appended += 1
    try:                                          # 批末一次性写投影（写盘失败 ≠ 事件失败）
        write_projection(storage, candidate)
    except OSError as exc:
        raise OSError(
            f"批次已落盘（{appended} 条）但投影未刷新：{exc} —— 可由 rebuild 重建"
        ) from exc
    return {"appended": appended, "projection_offset": len(events) + appended}
