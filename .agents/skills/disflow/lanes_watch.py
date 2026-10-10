#!/usr/bin/env python3
"""运行视图 v7 —— 以 **disflow 树**为骨架（阶段列 + 树层级），在**在跑节点**上就地显示实时进展。

v7 的正源（2026-10-11 用户要求改版）——原话：「我希望看板的运行视图，能按照阶段列出来。
并且能按树结构的类别分层」+「运行中的任务，要参考现在的运行视图，实时显示进展，重新渲染下」。

两个视图（**数据源分离、布局同源**）：
  · **看板** = `disflow board <tree>`：**纯 disflow 事实** —— 阶段列 × 列内树层级 × 状态分组。
    布局的唯一实现在 `disflow/views.py` 的 `board_layout` / `board_lines`。
  · **运行视图** = 本文件：disflow 事实（骨架 = **全部**节点，含 pending/skipped/failed）
    **join** 宿主 pi 运行目录（实时事实：哪条 lane、哪个批次、run 状态、已跑/静默、消息数、
    最后一条工具命令、worker 自报进展）。
    **布局同源**：本文件 import `disflow.views`，逐行**复用 `board_lines` 的成品行**（列 / 缩进 /
    分组一个字符都不自己写），只在其后追加 join 来的实时行——两套缩进就是本仓已知的「两面分叉」病。

join 键：lane 名（父 run 的 `workflowChildren.children[].childId`）== disflow 节点 id（宿主派活
时 key=node）。读不到就**明说**，绝不猜一个 goal 顶上。

── v8（2026-10-11 用户当场提的四条可用性修复）──

1. **默认刷新模式不再吃回滚**：v7 的默认路径用**备用屏** `\033[?1049h`——备用屏**没有回滚缓冲**，
   滞轮/scrollback 因此失效（v4 为治屏闪引入的取舍）。v8 默认改**追加式**：每轮打一整块
   （自带时间戳头 `══ 运行视图 @ HH:MM:SS ══`）、可滞轮、块间**互不依赖**；备用屏改为**显式**
   `--screen` 并在页脚写明代价（不闪、**不留回滚**）。**非 TTY（管道/重定向）零控制序列**：
   只打一轮快照就退。
2. **终态行不再打整段 goal**：`✓ 完成` / `— 跳过` 行只给 `标记 + id + 状态`，
   其余行 goal 走 `views.truncate_goal`（首行、≤80 字）；完整 goal 留给 `board --json`。
3. **默认只显示「运行中 + 待完成（可派/待前置/被在跑挡住）」**（用户 2026-10-11：显示太多、
   已完成的不必看）；`--full` 显示全部状态（含 `✓/—/✗` 与非规范兜底组）；
   `--active`／`--open` 保留为显式写法（现在等于默认）；`once|all|N|-v` 一个都没动。
4. **无在跑节点时明说**：`◈/⌘/↳` 只对在跑节点显示是设计；但没有在跑节点时屏幕上不能一片
   空白，否则读者以为功能没了——显式打一句「当前无在跑节点 ⇒ 无实时进展」。

⚠ 命名（三个词不许再撞）：`done` **不是列**（是状态）；一个 pi workflow run（= 宿主一次 subagent
调用，可含 1..N 路 lane）**不叫「看板」/「工作流」**——它是**运行视图**里的一个**派发批次**，
v7 起只作**次级注解区**（排障用），不再是主结构。

── 历史（v1..v6 的错，留着防止改回去）──

v6：外层循环是 pi 的 run 目录 ⇒ **只列已派发 lane，pending 一个不出现**；分组轴是派发批次
  ⇒ 看不到树的层级；且把 pi workflow run 叫「派发批次」，与看板（阶段列）撞名。
v5：分组 / 状态 / 命令 / 进展全部结构化（`status.json` + 子会话 `session.jsonl`），不猜。
v1 错：判据用文件 mtime（两个方向都骗）。
v2 错：平铺无分组；「进展」是 tail 原始日志。
v3 错：命令读日志被截断的 `bash:` 行；分组靠 idle，父 run 的 `lastActivityAt` 为 None 时塌掉。
v4 错：屏闪（clear）；「进展」从日志猜，抓到的是它读的文档内容。

用法（建议先 `cd` 到 disflow 仓根）:
  sh .agents/skills/disflow/lanes_watch.sh once     # 快照（无控制序列，可直接重定向）
  sh .agents/skills/disflow/lanes_watch.sh          # 持续 5s 追加式（保留回滚）
  sh .agents/skills/disflow/lanes_watch.sh 2
  sh .agents/skills/disflow/lanes_watch.sh --screen # 持续 5s 用备用屏（不闪、无回滚）
  sh .agents/skills/disflow/lanes_watch.sh once --full   # 全部状态（含 ✓/—/✗）；默认只看运行中 + 待完成
  sh .agents/skills/disflow/lanes_watch.sh all      # 含全部历史 lane
  sh .agents/skills/disflow/lanes_watch.sh once -v  # 每个 lane 多给几行
  DISFLOW_STATE_DIR=<树的状态目录> sh .agents/skills/disflow/lanes_watch.sh once
"""
import glob
import json
import os
import signal
import sys
import tempfile
import time

# 本文件在 <repo>/.agents/skills/disflow/ —— 三跳回仓根，让 `disflow.views`（布局唯一实现）可
# import。**布局同源是硬要求**：运行视图若自己写一套缩进/分列，就与看板分叉了。
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from disflow import config as dconfig  # noqa: E402
from disflow import spec as dspec  # noqa: E402
from disflow import views as dviews  # noqa: E402

# pi 的 async run 落点：$TMPDIR/pi-subagents-uid-*/async-subagent-runs/*
PAT = os.path.join(tempfile.gettempdir(), "pi-subagents-uid-*",
                   "async-subagent-runs", "*")
ICON = {"running": "●", "complete": "✓", "completed": "✓", "failed": "✗",
        "killed": "⊘", "started": "●", "stopped": "⊘"}
_DEFAULT_STATE_DIR = os.path.expanduser("~/.disflow/disflow-self")


def state_dir():
    """状态目录**每次调用都从环境解析**（与 `store.state_dir` 同源），不缓存。"""
    return os.environ.get("DISFLOW_STATE_DIR") or _DEFAULT_STATE_DIR


def _stages():
    """看板列 = **与 `disflow board` 同一来源**（`disflow.json` 的 `stages`，缺省 `spec.STAGES`）。

    配置坏了（ConfigError）不该把只读旁路打挂：退回出厂列并在表头不说「同看板」——
    调用方拿到的列仍是 `spec.STAGES`，与零配置下的 `board` 一致。
    """
    try:
        return dconfig.load(os.getcwd()).stages
    except dconfig.ConfigError:
        return dspec.STAGES


def _read_json(path):
    """读一个 JSON 文件；读不到 / 解析不了 → None。

    本视图是**只读旁路**：状态目录里半截的文件、旧格式的文件都不该把它打挂，
    但也不许因此报出一个假读数（调用方拿到 None 必须走显式的「不知道」分支）。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _loads(line):
    """解析一行 JSONL；坏行 → None（session 日志可能被截断写入）。"""
    try:
        return json.loads(line)
    except ValueError:
        return None


def load_projection():
    """读 disflow 投影（**骨架 + 布局输入**）。读不到 / 不是对象 → None。

    只读 `state.json`（不 spawn 子进程、不碰事件流）——运行视图不许改状态。
    调用方拿到 None 时必须**明说读不到**，绝不自己造一棵树。
    """
    path = os.path.join(state_dir(), "state.json")
    if not os.path.isfile(path):
        return None
    proj = _read_json(path)
    return proj if isinstance(proj, dict) else None


def alive(pid):
    if not isinstance(pid, int):
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def dur(sec):
    if sec is None:
        return "?"
    sec = int(sec)
    if sec < 0:
        sec = 0
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def jsonl(path, tail=80):
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def from_session(session_file):
    """→ (turns, 最后 toolCall 的 command, 最后 assistant 正文块)"""
    lines = jsonl(session_file)
    turns = sum(1 for x in lines if x.strip())
    cmd, text = None, None
    for line in reversed(lines[-120:]):
        if '"assistant"' not in line and '"toolCall"' not in line:
            continue
        ev = _loads(line)
        if ev is None:
            continue
        m = ev.get("message") or {}
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if isinstance(c, str):
            text = text or c
            continue
        for b in (c if isinstance(c, list) else []):
            if not isinstance(b, dict):
                continue
            if b.get("type") == "toolCall" and cmd is None:
                a = b.get("arguments") or {}
                v = a.get("command") or a.get("path") or a.get("pattern") or a.get("file")
                if v is None:
                    v = json.dumps(a, ensure_ascii=False)
                cmd = f"{b.get('name')}: {' '.join(str(v).split())}"
            if b.get("type") == "text" and text is None and (b.get("text") or "").strip():
                text = b["text"]
        if cmd and text:
            break
    return turns, cmd, text


def load(run_dir):
    """读一个 pi run 目录 → 结构化运行事实；读不到 `status.json` → None。"""
    sp = os.path.join(run_dir, "status.json")
    st = _read_json(sp) if os.path.isfile(sp) else None
    if not isinstance(st, dict):
        return None
    sess = None
    rd = os.path.join(run_dir, "recovery-descriptor.json")
    if os.path.isfile(rd):
        doc = _read_json(rd)
        sess = (doc or {}).get("sessionFile") if isinstance(doc, dict) else None
    root = None
    mp = os.path.join(run_dir, "mission.json")
    if os.path.isfile(mp):
        doc = _read_json(mp)
        root = (doc or {}).get("projectRoot") if isinstance(doc, dict) else None
    now = time.time()
    started = (st.get("startedAt") or 0) / 1000
    act = (st.get("lastActivityAt") or st.get("lastUpdate") or st.get("startedAt") or 0) / 1000
    turns, cmd, text = from_session(sess)
    return {
        "runId": st.get("runId") or os.path.basename(run_dir),
        "short": (st.get("runId") or os.path.basename(run_dir))[:8],
        "state": st.get("state") or "?",
        "mode": st.get("mode") or "?",
        "pid": st.get("pid"),
        "root": root,
        "elapsed": (now - started) if started else None,
        "idle": (now - act) if act else None,
        "turns": turns,
        "cmd": cmd,
        "text": text,
        "children": (st.get("workflowChildren") or {}).get("children") or [],
    }


def visible(runs, all_):
    """窗口内的 run：在跑，或近 10 分钟活动过（`all` 则全部）——与旧运行视图**同节拍**。"""
    return [r for r in runs if all_ or r["state"] == "running"
            or (r["idle"] is not None and r["idle"] < 600)]


def lane_index(runs):
    """把 lane 名（= disflow 节点 id）映到它的运行事实。

    正源：父 run（`mode=workflow`）的 `workflowChildren.children[].childId`。同一节点被派过
    多次时优先留**在跑**的那条，否则留后出现的一条。返回 `(by_node, parents)`；
    `parents` 供**次级注解区**（派发批次）用。
    """
    by_node = {}
    parents = [r for r in runs if r["mode"] == "workflow"]
    for p in parents:
        for c in p["children"]:
            cid = c.get("childId")
            if not cid:
                continue
            kid = next((r for r in runs if r["runId"] == c.get("runId")), None)
            prev = by_node.get(cid)
            if prev is None or (kid is not None and kid["state"] == "running"):
                by_node[cid] = {"run": kid, "child": c, "parent": p}
    return by_node, parents


def prog_lines(run, want):
    t = (run["text"] or "").strip()
    if not t:
        return ["（还没有助手正文）"]
    out = [x.strip() for x in t.splitlines() if x.strip()][:want]
    return [x[:140] for x in out]


def is_running(node, lane):
    """节点是否「在跑」：disflow 侧持租约（`status=running`）**或** pi 侧 run 在跑。

    **只对这一类显示实时进展**（`◈/⌘/↳`）——pending/done 不显示，避免刷屏。
    """
    if str(node.get("status") or "pending") == "running":
        return True
    run = (lane or {}).get("run")
    return bool(run and run["state"] == "running")


def node_annotation(lane):
    """一行**就地标注**运行事实：哪条 lane / run 状态 / 已跑 / 静默 / 消息数。

    **默认不显示批次号**（用户 2026-10-11：派发批次不用显示）——批次详情在 `-v` 的次级注解区。
    """
    c, _p, r = lane["child"], lane["parent"], lane["run"]
    facts = [f"lane {c.get('childId') or '?'}"]
    if r is None:
        facts.append(f"{c.get('state') or '?'}（run 目录已过窗口）")
        return "  ▸ " + " · ".join(facts)
    pa = {True: "活", False: "死", None: "?"}[alive(r["pid"])]
    facts += [f"{r['state']}（pid {r['pid']}({pa})）", f"已跑 {dur(r['elapsed'])}",
              f"静默 {dur(r['idle'])}", f"{r['turns']} 条消息"]
    return "  ▸ " + " · ".join(facts)


def progress_lines(node, lane, verbose, indent):
    """在跑节点的实时进展：`◈` 目标 · `⌘` 最后命令 · `↳` worker 自报进展；读不到就**明说**。

    goal 走 `views.truncate_goal`（与看板**同一份截断实现**：首行、≤80 字、`…`）。
    """
    run = (lane or {}).get("run")
    if run is None:
        return [f"{indent}↳ （在跑，但 pi 运行目录里读不到这条 lane —— 实时进展读不到）"]
    out = [f"{indent}◈ {dviews.truncate_goal(node.get('goal'))}"]
    out.append(f"{indent}⌘ {run['cmd'][:150]}" if run["cmd"]
               else f"{indent}⌘ （还没有工具调用）")
    for t in prog_lines(run, 6 if verbose else 2):
        out.append(f"{indent}↳ {t}")
    return out


def tree_block(slug, tree, lanes, closed, verbose, active_only=False):
    """一棵树：**逐行复用看板的渲染**（列 / 缩进 / 分组），再就地追加 join 来的实时行。

    `dviews.board_rows` 是**唯一行序来源**（`kind = column|group|row`）；这里按 `kind` 消费，
    **不再按位置 `next()`** —— 2026-10-11 实测：看板折叠空列后，按位置的消费方直接
    `StopIteration`（渲染变了，消费方不该错位）。运行视图因此**没有第二套缩进/分列**。
    """
    cols = _stages()
    out = []
    for item in dviews.board_rows(tree, stages=cols, closed=closed, tree_name=slug,
                                  active_only=active_only, empty_columns=verbose):
        out.append(item["text"])                  # 列头 / 组标签 / 行：全来自看板那一份
        if item["kind"] != "row":
            continue
        line, row = item["text"], item["row"]
        # 续行缩进 = 该行自身的行首空白（不另写一份缩进公式）
        indent = line[:len(line) - len(line.lstrip())]
        lane = lanes.get(row["node"])
        if lane is not None:
            out.append(indent + node_annotation(lane))
        if is_running(row, lane):
            out.extend(progress_lines(row, lane, verbose, indent + "  "))
    return out


def child_detail(label, run, info, verbose=False):
    """次级注解区的一路 lane 详情（命令行 / 尾行，供排障）。"""
    pa = {True: "活", False: "死", None: "?"}[alive(run["pid"])]
    head = (f"  {ICON.get(run['state'], '?')} {label or '(散跑)'}"
            f"  {run['state']}  已跑 {dur(run['elapsed'])} · 静默 {dur(run['idle'])}"
            f" · pid {run['pid']}({pa}) · {run['turns']} 条消息{info}")
    lines = [head]
    if run["cmd"]:
        lines.append(f"      命令  {run['cmd'][:150]}")
    for t in prog_lines(run, 6 if verbose else 2):
        lines.append(f"      尾行  {t}")
    return lines


def batch_section(runs, keep, parents, node_meta, verbose):
    """**次级注解区**：派发批次（+ 散跑）的排障详情。**不是主结构**。"""
    kept = {r["runId"] for r in keep}
    out = ["", "── 派发批次（次级注解 · 排障用 · 不是主结构）──"]
    groups, claimed = [], set()
    for p in parents:
        cids = {c.get("runId") for c in p["children"]}
        kids = [r for r in runs if r["runId"] in cids]
        if any(k["runId"] in kept for k in kids):
            groups.append((p, kids))
            claimed |= {k["runId"] for k in kids}
    rest = [r for r in keep if r["runId"] not in claimed and r["mode"] != "workflow"]
    if not groups and not rest:
        out.append("（没有窗口内的 lane：没有 running 的，也没有近 10 分钟活动过的）")
    for i, (p, kids) in enumerate(groups, 1):
        nrun = sum(1 for k in kids if k["state"] == "running")
        out.append(f"▌批次 {i}/{len(groups)} · {p['short']}"
                   f"（{nrun} 在跑 / {len(kids)} 路 · 父 {p['state']}）")
        for c in p["children"]:
            cid = c.get("childId") or ""
            kid = next((r for r in kids if r["runId"] == c.get("runId")), None)
            if kid is None:
                out.append(f"  · {cid}  {c.get('state')}（run 目录已过窗口）")
                continue
            meta = node_meta.get(cid)
            info = (f" · 节点 {cid} · 树 {meta['tree']} · 列 {meta['stage']}"
                    f" · 状态 {meta['status']}" if meta
                    else f" · （lane 名 {cid!r} 在 disflow 里没有对应节点 —— 不猜）")
            out.extend(child_detail(cid, kid, info, verbose))
    if rest:
        out.append(f"▌散跑 {len(rest)} 路（没有 lane 名，无法关联节点）")
        for r in rest:
            out.extend(child_detail("", r, " · （散跑：没有 lane 名，无法关联节点）", verbose))
    return out


def has_active_work(tree):
    """这棵树有「运行中 / 待办」的**任务**吗 —— 运行视图默认只显示有活的树（用户 2026-10-11）。

    容器不算（它恒 `pending`，会让「空树」永远看起来有活——与 `ready.progress`/`metrics`
    「容器不量化」同一口径）。
    """
    for node in (tree.get("nodes") or {}).values():
        if (isinstance(node, dict) and node.get("kind") != "container"
                and node.get("status") in ("pending", "running")):
            return True
    return False


def render(all_, verbose=False, only_active=False, tree=None, active_trees_only=False):
    """一轮的**完整块**（自带时间戳头，不依赖上一轮）——追加式刷新直接照打。

    `only_active`（**默认 True**；`--all`／`--full` 关掉它）：只列「运行中 + 待完成」的组
    （终态组整组隐藏，与看板同源）。次级「派发批次」区默认不显示，`-v` 才展开。

    `tree`（`--tree <slug>`）只看那一棵树；`active_trees_only` **只看有活的树**（默认；
    一棵都没有时每棵树只给一行摘要，不是空白屏）—— 树 : 看板 = 1:1，不分屏叠树。
    """
    proj = load_projection()
    runs = [r for r in (load(d) for d in glob.glob(PAT)) if r]
    keep = visible(runs, all_)
    kept = {r["runId"] for r in keep}
    by_node, parents = lane_index(runs)
    # 只有**窗口内**的 lane 才就地标注（否则历史 lane 会把每一行都涂满）
    lanes = {nid: v for nid, v in by_node.items()
             if v["run"] is not None and v["run"]["runId"] in kept}
    trees = (proj or {}).get("trees") or {}
    closed = {name for name, t in trees.items()
              if isinstance(t, dict) and t.get("closed")}
    node_meta = {}
    for slug, t in trees.items():
        if not isinstance(t, dict):
            continue
        for nid, node in (t.get("nodes") or {}).items():
            if isinstance(node, dict):
                node_meta[nid] = {"tree": slug, "stage": node.get("stage") or "?",
                                  "status": node.get("status") or "?"}
    # 「在跑节点」用 `is_running` 同一判据算（disflow 持租约或 pi run 在跑）——
    # 无在跑节点时 `◈/⌘/↳` 一个都不会出现（这是设计），但屏幕不能一片空白。
    running_nodes = sum(
        1 for t in trees.values() if isinstance(t, dict)
        for nid, node in (t.get("nodes") or {}).items()
        if isinstance(node, dict) and is_running(node, lanes.get(nid)))
    nrunning = sum(1 for r in keep if r["state"] == "running")
    cols = _stages()
    # 树 : 看板 = 1:1 ⇒ 默认只看「有活的那棵」，不把 3 棵树叠成一屏。
    slugs = sorted(trees)
    note, pre = "", []
    if tree is not None:
        if tree in trees:
            slugs = [tree]
            note = f" · --tree {tree}"
        else:
            slugs = []
            pre = ["", f"✗ 没有名为 {tree!r} 的树（现有：{', '.join(sorted(trees)) or '（无）'}）"]
    elif active_trees_only:
        live = [s for s in slugs if isinstance(trees[s], dict) and has_active_work(trees[s])]
        if live:
            slugs = live
            note = (f" · 只显示有活的 {len(live)} 棵"
                    "（--all-boards 看全部 · --tree <slug> 指定）")
        else:
            slugs = []
            pre = [""]
            for s in sorted(trees):
                t = trees[s]
                g = dviews.truncate_goal(str(t.get("goal") or "")) if isinstance(t, dict) else ""
                pre.append(f"▌树 {s}  {g}  （没有运行中 / 待办的任务）")
    out = [f"══ 运行视图 @ {time.strftime('%H:%M:%S')} ══",
           "数据源：disflow 投影（骨架 = 全部节点，布局同看板） + pi 运行目录（实时事实）",
           f"树 {len(trees)} 棵：{', '.join(sorted(trees)) if trees else '（读不到 state.json）'}"
           f" · 列（同看板）：{'|'.join(cols)}"
             + ((" · 默认：只列运行中 + 待完成（--all 看全部状态）" if only_active
                 else " · --all：显示全部状态") + note),
           f"在跑 lane {nrunning} 路 · 窗口内 lane {len(keep)} 路 · 静默阈值 10 分钟"
           f" · state dir {state_dir()}"]
    out.extend(pre)
    if running_nodes == 0:
        out.append("")
        out.append("当前无在跑节点 ⇒ 无实时进展（进展只对在跑节点显示）")
    if proj is None:
        out.append("")
        out.append(f"✗ 读不到 state.json（{os.path.join(state_dir(), 'state.json')}）"
                   " —— 没有树可当骨架；下面只有 pi 运行目录里的事实（不猜节点）")
    elif not trees:
        out.append("")
        out.append("（state.json 里没有树）")
    for slug in slugs:
        t = trees[slug]
        if not isinstance(t, dict) or not isinstance(t.get("nodes"), dict):
            out.append(f"▌树 {slug}  ⚠ 投影节点不是对象（读时容忍：跳过，不猜）")
            continue
        goal = dviews.truncate_goal(str(t.get("goal") or ""))
        out.append("")
        out.append(f"▌树 {slug}  {goal}")
        out.extend(tree_block(slug, t, lanes, closed, verbose, only_active))
    if verbose:            # 次级「派发批次」区：默认不显示（用户 2026-10-11），`-v` 才展开
        out.extend(batch_section(runs, keep, parents, node_meta, verbose))
    return out


def draw(lines, footer=""):
    """备用屏（`--screen`）的整屏重绘 —— **只有这条路径才发控制序列**。"""
    sys.stdout.write("\033[H" + "\n".join(x + "\033[K" for x in lines)
                     + "\n" + footer + "\033[K\033[J")
    sys.stdout.flush()


def append_footer(iv):
    return (f"（Ctrl-C 退出 · {iv:g}s 刷新 · 状态=status.json.state"
            " · 列/布局=disflow 看板同源"
            " · 追加模式：每轮一整块、保留回滚（可滞轮）"
            " · --screen = 备用屏（不闪、**无回滚**））")


def screen_footer(iv):
    return (f"（Ctrl-C 退出 · {iv:g}s 刷新 · 状态=status.json.state"
            f" · 列/布局=disflow 看板同源 · 备用屏：不闪、**无回滚**）")


def leave(*_):
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    sys.exit(0)


def append_loop(iv, verbose, only_active, tree=None, active_trees_only=False):
    """**默认刷新模式**：追加式（不用备用屏）——每轮一整个块（自带时间戳头），
    回滚缓冲保留 ⇒ 滞轮/scrollback 可用；块间互不依赖。Ctrl-C → 干净退出。
    """
    try:
        while True:
            block = render(False, verbose, only_active, tree, active_trees_only) + [append_footer(iv)]
            sys.stdout.write("\n".join(block) + "\n")
            sys.stdout.flush()
            time.sleep(iv)
    except KeyboardInterrupt:
        return 0


def screen_loop(iv, verbose, only_active, tree=None, active_trees_only=False):
    """显式 `--screen`：备用屏整屏重绘（不闪，代价是**不留回滚**）。"""
    signal.signal(signal.SIGINT, leave)
    signal.signal(signal.SIGTERM, leave)
    sys.stdout.write("\033[?1049h\033[?25l")
    try:
        while True:
            draw(render(False, verbose, only_active), screen_footer(iv))
            time.sleep(iv)
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
    return 0


_FLAGS = ("-v", "--screen", "--active", "--open", "--full", "--all", "--all-boards")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = "-v" in argv
    screen = "--screen" in argv
    # `--all`（别名 `--full`）：展开终态（旧默认）。
    show_all = "--all" in argv or "--full" in argv
    only_active = not show_all
    # `--tree <slug>`：只看这一棵树（树 : 看板 = 1:1；不指定就自动选「有活的那棵」）。
    tree = None
    if "--tree" in argv:
        i = argv.index("--tree")
        tree = argv[i + 1] if i + 1 < len(argv) else None
        argv = [a for j, a in enumerate(argv) if j not in (i, i + 1)]
    # 默认**只看有活的树**（用户 2026-10-11：「怎么分 3 个树展示？树和看板是 1:1 关系啊」）。
    active_trees_only = tree is None and not show_all and "--all-boards" not in argv
    rest = [a for a in argv if a not in _FLAGS]
    mode = rest[0] if rest else "5"
    if mode in ("once", "all"):
        print("\n".join(render(mode == "all", verbose, only_active, tree, active_trees_only)))
        return 0
    try:
        iv = float(mode)
    except ValueError:
        iv = 5.0
    if not sys.stdout.isatty():
        # 非 TTY（管道 / 重定向）：**零控制序列** —— 打一轮独立块就退（没人会看第二次刷新）。
        print("\n".join(render(False, verbose, only_active, tree, active_trees_only)))
        return 0
    if screen:
        return screen_loop(iv, verbose, only_active, tree, active_trees_only)
    return append_loop(iv, verbose, only_active, tree, active_trees_only)


if __name__ == "__main__":
    sys.exit(main())
