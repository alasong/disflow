"""disflow · 量化指标（纯函数，**零时钟**）。

事件流 + 投影 → 节点数分母 + 点数汇总 / 速度 / 燃尽。本模块内部**绝不读时钟**：`now_ts`
一律由调用方传入——否则同一份事件在不同机器、不同时刻会算出不同的天数（不可复现、无法对比）。

**统一语义：as-of `now_ts`。** 完工一律「截至 `now_ts`」判定——`points_done` / `nodes_done`
只计完工事件 `ts <= now_ts` 的节点。为何选这个语义而不是「按投影里当前 status 全算」：
`burndown` 是时间序列，必须截断到 `now_ts`（否则序列末行在未来的完工也会把曲线压低）；
若 `summary` 另按当前 status 全算，同一份事实就会出现两个数（`summary.points_left`
对不上 `series[-1].remaining`）。两处共用 `_done_as_of`，不变量才成立。

**未估（`points is None`）的节点不进任何分母**：把它当 0 会把「没估」读成「不用干」，
velocity 被凭空抬高、eta 被凭空压低——这是量化最容易骗自己的地方，故单独计入
`points_unestimated` 并在所有求和中排除。

**两个维度并列，不许混成一个数**：`nodes_*`（节点数）**永远**有读数，与 `points` 是否
声明无关；`points_*`（点数）在没人声明 `points` 时如实报 `points_total == 0` +
`points_unestimated == 全部 task 数`。为何要节点数这一维：只声明 points 时，一棵 33 个
task、0 个 points 声明的树的燃尽只剩「0 → 0」，一行没有信息量，读的人会以为「没什么可干」
——而实际上有 33 个节点在。两个维度共用同一个 `as-of now_ts` 截点（`_done_as_of`），
`summary.nodes_left` 因此恒等于 `burndown.series[-1].remaining_nodes`（与点数维度同一条不变量）。

**读路径不抛异常，但绝不静默**（NFR-7）：起点未知、存储的 deadline 不可解析这类
「数据有洞」的情形，一律给显式 `*_reason` 字段说明原因，绝不假装读数是合法的。

`tree` 参数是**投影树 dict**（不含 slug——slug 恒等于外层 key，是可推导值，由调用方
显式传入 `slug`，见 `state`）；`events` 是全量事件流。完工时刻一律**从 `node.done` /
`node.skip` 事件推**（事件流是唯一真相），不从存储字段推。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .core import TERMINAL
from .store import parse_ts

DATE_FMT = "YYYY-MM-DD"

# 终态事件：节点在这些事件里首次/最终进入终态，其时戳就是完工日。
_COMPLETION_KINDS = ("node.done", "node.skip")

# 起点未知（slug 在流里出现过、但该树确实没有 tree.init）的统一措辞。
# 「未知 slug」（传错 slug）另给专属措辞——见 `_start_reason`。
NO_START_REASON = "无 tree.init 事件，无法确定起点"


def parse_deadline(value: str | None) -> str | None:
    """校验并归一 `deadline`：`None`/`""`/纯空白/`"-"` → `None`；其余必须是 `YYYY-MM-DD`。

    为何不静默丢弃 / 当成「没有截止」：写错日期却当成无 deadline，会让逾期**永远发现不了**——
    与其它写动词一致，非法输入一律响亮失败。

    与 docstring 早先写的「原样返回」不同：这里会 `strip()` 掉首尾空白再校验。取舍——
    容忍首尾空白（复制粘贴/模板常带）比因一个空格拒收更友好，代价是「原样返回」并不
    严格成立，故此处把文案改准。校验后**零填充、无时区的 `YYYY-MM-DD` 文本**。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "-":
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"deadline 应为 {DATE_FMT}，得到 {value!r}") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"deadline 应为 {DATE_FMT}（零填充的规范形），得到 {value!r}")
    return text


def _read_deadline(tree: dict) -> tuple[str | None, str | None]:
    """从投影树读 `deadline`，返回 `(归一值, reason)`。

    deadline 的唯一入口就是树自己的 `tree['deadline']`（由 `tree.deadline` 事件写、写时
    `parse_deadline` 归一）——不再另设调用参数，否则两个入口会漂移。

    存储值不可解析（reducer 不校验：校验放在写时，否则重放会炸）时**不静默忽略**：
    给显式 `reason` 字段而不是抛异常——这是读路径，NFR-7 要的是「不静默」而不是「一读就炸」。
    与 `start_reason` 同一种失败哲学（本模块只有一种）。
    """
    raw = tree.get("deadline")
    try:
        return parse_deadline(raw), None
    except ValueError as exc:
        return None, f"存储的 tree['deadline'] 非法，已按无 deadline 处理：{exc}"


def _day(ts: float) -> date:
    """epoch 秒 → UTC 日历日（与 `store.now_iso` 的 UTC 口径一致）。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def tree_start_ts(events: list[dict], *, slug: str) -> float | None:
    """该树**第一条** `tree.init` 事件的 epoch 秒；找不到 → `None`。

    ts 解析复用 `store.parse_ts`（全库唯一一份 ISO→epoch 转换，不另写一份）。
    `slug` 显式传入（不再从投影 `tree['slug']` 读——那是恒等于外层 key 的可推导值）。

    `slug` 是 `str` 契约：`None`/非字符串时一律返回 `None`（可判定性缺失），而不是把
    `event.get("slug") == None` 与「事件里根本没有 `slug` 键 / 存了 `None`」混为一谈——
    那是类型外的隐含匹配，会把无 slug 的事件误认成该树起点。
    """
    if not isinstance(slug, str):
        return None
    for event in events:
        if event.get("kind") == "tree.init" and event.get("slug") == slug:
            return parse_ts(event["ts"])
    return None


def _start_reason(events: list[dict], slug: str, start_ts: float | None) -> str | None:
    """起点缺失时的**专属** reason（起点已知 → `None`）。

    「传错 slug」与「树真没 init」是两种不同的数据洞，绝不能合并成同一句话把调用方引向
    错的排查方向（与 `_read_deadline` 每种降级都给专属 reason 的做法一致）：

    - slug 未在该事件流里出现过（且流里确实出现过别的 slug）→ 明说「未知 slug」并列出
      流里出现过的 slug：拼错时一眼可见；
    - slug 出现过但没有 `tree.init` → 保留原措辞 `NO_START_REASON`；
    - 事件流里压根没有任何**可用** slug（空流，或只有空串/纯空白 slug）→ 无法归咎于拼错，
      同样保留 `NO_START_REASON`（故「列出的 slug」恒非空，文案不会出现「：」后为空）；
    - 查询 `slug` 本身不是字符串（`None`/非 `str`）→ 可判定性缺失，无从判断是不是拼错，
      按不可判定处理走 `NO_START_REASON`。**取舍**：此处不抛异常——metrics 是读路径，
      NFR-7 要的是「不静默」而非「一读就炸」，且会让正常展示路径因一个坏参数而崩溃。
    """
    if start_ts is not None:
        return None
    if not isinstance(slug, str):
        return NO_START_REASON
    seen = {text for text in (str(event.get("slug")) for event in events
            if event.get("slug") is not None) if text.strip()}
    if seen and slug not in seen:
        return (f"未知 slug：{slug!r}；该事件流里出现过的 slug："
                f"{', '.join(sorted(seen))}")
    return NO_START_REASON


def _future_start_reason(start_day: date, today: date) -> str:
    """`tree.init` 晚于 `now_ts` 时的专属 reason（起点在未来）。

    此时 `burndown` 只含起点那一行（因为跨度被夹到 0），其日期在 `now_ts` 之后；数值仍
    正确，但语义怪，故显式报告而非静默（NFR-7）。`summary` 的 `days_elapsed` 也会为负，
    同报此因。
    """
    return (f"tree.init（{start_day.isoformat()}）晚于 now_ts（{today.isoformat()}），"
            f"序列只含起点行")


def _tasks(nodes: dict) -> list[dict]:
    """只算 `kind == "task"` 的节点——container 不量化（它是子节点的聚合，不是工作量）。"""
    return [node for node in nodes.values() if node.get("kind") == "task"]


def _estimated(tasks: list[dict]) -> dict[str, float]:
    """已估任务 → `{node_id: points}`；未估（None）的任务不在这里（不进任何分母）。"""
    return {node["id"]: float(node["points"])
            for node in tasks if node.get("points") is not None}


def _completion_ts(events: list[dict], terminal_ids: set[str]) -> dict[str, float]:
    """已终态任务 → 完工时刻：取该节点**最后一条** `node.done` / `node.skip` 事件的 epoch 秒。

    取「最后一条」而非「第一条」：重试后重新完工时，完工日应记在真正进入终态的那次。
    只认 `terminal_ids`（当前投影里终态的节点）——复位事件会把节点带回非终态，
    此时不该再被算作完工。
    """
    ts: dict[str, float] = {}
    for event in events:
        if event.get("kind") not in _COMPLETION_KINDS:
            continue
        node_id = event.get("node")
        if node_id in terminal_ids:
            ts[node_id] = parse_ts(event["ts"])
    return ts


def _done_as_of(completion: dict[str, float], node_id: str, terminal_ids: set[str],
                cutoff_ts: float, cutoff_day: date) -> bool:
    """节点截至截点是否已完工（summary 与 burndown 共用，保证两处同口径）。

    - 非终态节点：未完工；
    - 终态节点有完工事件：`ts <= cutoff_ts` **且** 完工日 `<= cutoff_day`；
    - 终态但无完工事件（如 `node.import` 直接导入 done）：视为期内已完工，从线下起点即计入。
    """
    if node_id not in terminal_ids:
        return False
    ts = completion.get(node_id)
    if ts is None:
        return True
    return ts <= cutoff_ts and _day(ts) <= cutoff_day


def summary(tree: dict, events: list[dict], *, slug: str, now_ts: float) -> dict:
    """节点数汇总 + 点数汇总 + 速度 + 预计完工 + deadline 余量。纯函数，时间只来自 `now_ts`。

    完工按 **as-of `now_ts`** 截断（见模块 docstring）：`points_done` / `nodes_done` 只计
    完工事件 `ts <= now_ts` 的节点，与 `burndown` 的 `series[-1]` 同口径。

    `nodes_*` 是节点数维度（container 不算，与点数维度同一份 `_tasks`）；`points_*` 是点数
    维度，未声明时 `points_total` 就是 0（**不是**把节点数冒充成点数）。
    """
    nodes = tree.get("nodes") or {}
    tasks = _tasks(nodes)
    estimated = _estimated(tasks)

    # 终态节点集合按**全部 task 节点**取（不再只取已估的）：两个维度必须共用同一个
    # `as-of now_ts` 截点，否则同一份事实会给出两个「已完成」。已估节点是 task 的子集，
    # 故点数求和的逐项判据与「只取已估 ∩ 终态」时**逐字相同**。
    terminal_ids = {task["id"] for task in tasks
                    if nodes[task["id"]].get("status") in TERMINAL}
    completion = _completion_ts(events, terminal_ids)
    today = _day(now_ts)
    done_ids = {node_id for node_id in terminal_ids
                if _done_as_of(completion, node_id, terminal_ids, now_ts, today)}

    points_total = sum(estimated.values())
    points_done = sum(points for node_id, points in estimated.items()
                      if node_id in done_ids)
    points_left = points_total - points_done
    points_unestimated = len(tasks) - len(estimated)

    # 节点数维度：「没给点数」不等于「没有节点」，故这一维不受 points 声明影响。
    nodes_total = len(tasks)
    nodes_done = len(done_ids)
    nodes_left = nodes_total - nodes_done

    start_ts = tree_start_ts(events, slug=slug)
    # 起点未知与「起点就是今天」都可能给出 days_elapsed == 0，故用 start_reason 显式区分：
    # 起点未知时不得让调用方把 0 读成合法读数。此处**不抛异常**——它是读路径，
    # NFR-7 要的是「不静默」而不是「一读就炸」；给理由字段即可。
    # `_start_reason` 还区分「传错 slug」与「树真没 init」（见其 docstring）。
    start_reason = _start_reason(events, slug, start_ts)
    # 天数按**日历日**计：同一天开工同一天看 = 0 天（不是「过了几小时」）。
    # 于是「同一天 velocity 为 0」是结构性的，不靠 points_done 恰好为 0 来兜。
    days_elapsed = (today - _day(start_ts)).days if start_ts is not None else 0
    if start_ts is not None and _day(start_ts) > today:
        # 起点在未来：days_elapsed 为负，velocity/eta 无意义——显式报告（不静默）。
        start_reason = _future_start_reason(_day(start_ts), today)

    velocity = points_done / days_elapsed if days_elapsed > 0 else 0.0
    eta_days = points_left / velocity if velocity > 0 else None

    normalized, deadline_reason = _read_deadline(tree)
    if normalized is None:
        deadline_remaining_days: int | None = None
        deadline_overdue_days: int | None = None
    else:
        # remaining 有符号：正=还剩几天、负=已逾期；overdue 非负：逾期几天，未逾期为 0。
        delta = (date.fromisoformat(normalized) - today).days
        deadline_remaining_days = delta
        deadline_overdue_days = max(0, -delta)

    return {
        "nodes_total": nodes_total,
        "nodes_done": nodes_done,
        "nodes_left": nodes_left,
        "points_total": points_total,
        "points_done": points_done,
        "points_left": points_left,
        "points_unestimated": points_unestimated,
        "start_ts": start_ts,
        "start_reason": start_reason,
        "days_elapsed": days_elapsed,
        "velocity": velocity,
        "eta_days": eta_days,
        "deadline": normalized,
        "deadline_reason": deadline_reason,
        "deadline_remaining_days": deadline_remaining_days,
        "deadline_overdue_days": deadline_overdue_days,
    }


def burndown(tree: dict, events: list[dict], *, slug: str, now_ts: float) -> dict:
    """逐日剩余（series：节点数 + 点数两列）+ 两维理想线（ideal / ideal_nodes）。

    - `series`：从树开始那天到今天，每天一行
      `{"day", "remaining", "remaining_nodes"}`——`remaining` 是**点数**维度、
      `remaining_nodes` 是**节点数**维度，两个数并列不合并（节点数维度不受 points 是否
      声明影响：没人声明 points 时点数那一列恒为 0，节点数那一列照样逐日下降）；
    - `ideal` / `ideal_nodes`：分别从 `points_total` / `nodes_total` 直线降到 **deadline
      那天**归零，点覆盖 **start_day → deadline 两端**——末点值恒为 `0`、其 `day` 恒等于
      `deadline`。故理想线与 `series` 的 x 轴**故意不等长**：理想线画到截止（计划的形状），
      `series` 只画到今天（实际读数不许把未来画成事实）。两维共用一条成因（`ideal_reason`）；
      **无 deadline 时为 `None`**（两维都是），并在 `ideal_reason` 里说明原因（不伪造一条线
      ——假线比没有线更误导）；
    - 无 `tree.init` 时 `series` 为 `[]`，但带显式 `start_reason`（与 `summary` 同一种报告方式）。
    """
    nodes = tree.get("nodes") or {}
    tasks = _tasks(nodes)
    estimated = _estimated(tasks)
    points_total = sum(estimated.values())

    normalized, deadline_reason = _read_deadline(tree)
    start_ts = tree_start_ts(events, slug=slug)
    if start_ts is None:
        # 没有起点就画不出序列——但绝不静默：给显式 reason（与 summary 同一措辞，且同样
        # 区分「未知 slug」与「树真没 init」）。
        reason = _start_reason(events, slug, start_ts)
        return {"series": [], "ideal": None, "ideal_nodes": None,
                "deadline": normalized,
                "ideal_reason": reason, "start_ts": None,
                "start_reason": reason, "deadline_reason": deadline_reason}

    start_day = _day(start_ts)
    today = _day(now_ts)
    # 起点晚于 now 时跨度被夹到 0，序列只剩起点那一行（未来日期）——数值正确但语义怪，
    # 显式报告（NFR-7），与 `summary` 同一措辞。
    start_reason = _future_start_reason(start_day, today) if start_day > today else None
    span = max(0, (today - start_day).days)
    days = [start_day + timedelta(days=offset) for offset in range(span + 1)]

    # 与 `summary` 同一样：终态集合按全部 task 节点取，两维共用同一个截点。
    terminal_ids = {task["id"] for task in tasks
                    if nodes[task["id"]].get("status") in TERMINAL}
    completion = _completion_ts(events, terminal_ids)
    nodes_total = len(tasks)

    series: list[dict] = []
    for day in days:
        # 逐日截点：`cutoff_day = day`，完工时刻统一受 `now_ts` 约束（未来完工不算）。
        done_today = {node_id for node_id in terminal_ids
                      if _done_as_of(completion, node_id, terminal_ids, now_ts, day)}
        series.append({"day": day.isoformat(),
                       "remaining": sum(points for node_id, points in estimated.items()
                                        if node_id not in done_today),
                       "remaining_nodes": nodes_total - len(done_today)})

    ideal: list[dict] | None = None
    ideal_nodes: list[dict] | None = None
    ideal_reason: str | None = None
    if normalized is None:
        ideal_reason = "无 deadline，不构造理想线（不伪造一条线）"
    else:
        horizon = (date.fromisoformat(normalized) - start_day).days
        if horizon <= 0:
            ideal_reason = (f"deadline（{normalized}）不晚于起点"
                            f"（{start_day.isoformat()}），无法构造理想线")
        else:
            # 理想线自己一条 x 轴：start_day → deadline（两端都含）。**不能**复用 `series` 的
            # `days`（那只到 today）：deadline 那端会根本不生成，末点落在今天且值 > 0 ——
            # 下游把「末点」读成「归零日」，就会在「今天」说一句假话。
            plan_days = [start_day + timedelta(days=offset) for offset in range(horizon + 1)]
            ideal = []
            ideal_nodes = []
            for day in plan_days:
                fraction = (day - start_day).days / horizon
                ideal.append({"day": day.isoformat(),
                              "ideal": max(0.0, points_total * (1.0 - fraction))})
                ideal_nodes.append({"day": day.isoformat(),
                                    "ideal_nodes": max(0.0, nodes_total * (1.0 - fraction))})

    return {"series": series, "ideal": ideal, "ideal_nodes": ideal_nodes,
            "deadline": normalized,
            "ideal_reason": ideal_reason, "start_ts": start_ts,
            "start_reason": start_reason, "deadline_reason": deadline_reason}
