#!/usr/bin/env python3
"""看板运行视图 —— 按**阶段**（= 看板的列：需求/设计/开发/验证）分别列出
**运行中（running）+ 待完成（pending）**的任务；树只作**列内的父子缩进**与行尾的来源标注。

判据（用户 2026-10-11 原话）：「我就想看看板运行视图，按照几个看板，分别显示 running 和
pending 的任务」+「树只是看板中条目的组织关系」——**看板 = 阶段列**；树**不是**一块板。

两个数据源（都只读，不写状态、不 spawn）：
  · disflow 投影 `$DISFLOW_STATE_DIR/state.json`：条目（哪棵树 / 父子 / stage / status / goal）
  · 宿主 pi 的运行目录 `$TMPDIR/pi-subagents-uid-*/async-subagent-runs/*`：哪条 lane 在跑、
    已跑 / 静默、最后一条工具命令、worker 自报的进展（`status.json` + 子会话 `session.jsonl`）
join 键：lane 名（父 run 的 `workflowChildren.children[].childId`）== disflow 节点 id。

**与 `disflow board` 的分工**：`board` 答「这棵树长什么样」（全阶段 × 全状态，一次快照）；
本视图答「**现在谁在跑、谁还没做**」（只看 running + pending，持续刷新）。问题不同 ⇒ 视图不同，
所以本文件**不做** `board` 那套分组，也不假装同源（同源的是阶段列名与 goal 截断：读配置 / `views.truncate_goal`）。

用法（建议先 `cd` 到 disflow 仓根）:
  sh .agents/skills/disflow/lanes_watch.sh            # 默认：每 5s 追加一块（保留回滚）；Ctrl-C 退出
  sh .agents/skills/disflow/lanes_watch.sh once       # 打一轮就退（非 TTY / 管道自动走这条）
  sh .agents/skills/disflow/lanes_watch.sh once --all # 连 ✓ 完成 / — 跳过 也列（默认只列运行中 + 待完成）
  sh .agents/skills/disflow/lanes_watch.sh once -v    # 每个在跑 lane 多几行（最后命令 + 自报进展）
  sh .agents/skills/disflow/lanes_watch.sh 2          # 每 2s 刷新
  DISFLOW_STATE_DIR=<状态目录> sh .agents/skills/disflow/lanes_watch.sh once
"""
import glob
import json
import os
import sys
import tempfile
import time

# 本文件在 <repo>/.agents/skills/disflow/ —— 三跳回仓根，让 disflow 的配置/spec/截断助手可 import。
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from disflow import config as dconfig  # noqa: E402
from disflow import spec as dspec  # noqa: E402
from disflow import views as dviews  # noqa: E402

# pi 的 async run 落点
PAT = os.path.join(tempfile.gettempdir(), "pi-subagents-uid-*", "async-subagent-runs", "*")
_DEFAULT_STATE_DIR = os.path.expanduser("~/.disflow/disflow-self")
# 只看这两类（用户要求）：运行中 + 待完成
_ACTIVE = ("running", "pending")
_GROUP = {"running": "▶ 运行中", "pending": "· 待完成", "done": "✓ 完成",
          "skipped": "— 跳过", "failed": "✗ 失败"}
_STATUS = {"running": "运行中", "pending": "待完成", "done": "完成",
           "skipped": "跳过", "failed": "失败"}
_MARK = {"running": "▶", "pending": "·", "done": "✓", "skipped": "—", "failed": "✗"}
_WINDOW = 600          # 运行目录窗口：在跑，或近 10 分钟活动过


def state_dir():
    """状态目录**每次调用都从环境解析**（与 `store.state_dir` 同源），不缓存。"""
    return os.environ.get("DISFLOW_STATE_DIR") or _DEFAULT_STATE_DIR


def stages():
    """列 = **与 `disflow board` 同一来源**（`disflow.json` 的 `stages`；缺省 `spec.STAGES`）。

    配置坏了不该把只读旁路打挂：退回出厂列（与零配置下的 `board` 一致）。
    """
    try:
        return tuple(dconfig.load(os.getcwd()).stages)
    except dconfig.ConfigError:
        return tuple(dspec.STAGES)


def _read_json(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _loads(line):
    try:
        return json.loads(line)
    except ValueError:
        return None


def load_projection():
    """读 disflow 投影（条目来源）。读不到 / 不是对象 → None（调用方必须明说，不许造树）。"""
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
    sec = max(int(sec), 0)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def from_session(session_file):
    """→ (消息条数, 最后一条 toolCall 的命令, 最后一段 assistant 正文)。"""
    if not session_file or not os.path.isfile(session_file):
        return 0, None, None
    try:
        with open(session_file, errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return 0, None, None
    turns = sum(1 for x in lines if x.strip())
    cmd = text = None
    for line in reversed(lines[-120:]):
        if '"assistant"' not in line and '"toolCall"' not in line:
            continue
        ev = _loads(line)
        if ev is None:
            continue
        msg = ev.get("message") or {}
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            text = text or content
            continue
        for block in (content if isinstance(content, list) else []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "toolCall" and cmd is None:
                args = block.get("arguments") or {}
                val = (args.get("command") or args.get("path") or args.get("pattern")
                       or args.get("file"))
                if val is None:
                    val = json.dumps(args, ensure_ascii=False)
                cmd = f"{block.get('name')}: {' '.join(str(val).split())}"
            if block.get("type") == "text" and text is None and (block.get("text") or "").strip():
                text = block["text"]
        if cmd and text:
            break
    return turns, cmd, text


def load(run_dir):
    """读一个 pi run 目录 → 结构化运行事实；读不到 `status.json` → None（明说，不猜）。"""
    path = os.path.join(run_dir, "status.json")
    st = _read_json(path) if os.path.isfile(path) else None
    if not isinstance(st, dict):
        return None
    session_file = None
    rd = os.path.join(run_dir, "recovery-descriptor.json")
    if os.path.isfile(rd):
        doc = _read_json(rd)
        session_file = (doc or {}).get("sessionFile") if isinstance(doc, dict) else None
    now = time.time()
    started = (st.get("startedAt") or 0) / 1000
    act = (st.get("lastActivityAt") or st.get("lastUpdate") or st.get("startedAt") or 0) / 1000
    turns, cmd, text = from_session(session_file)
    return {
        "runId": st.get("runId") or os.path.basename(run_dir),
        "state": st.get("state") or "?",
        "pid": st.get("pid"),
        "elapsed": (now - started) if started else None,
        "idle": (now - act) if act else None,
        "turns": turns,
        "cmd": cmd,
        "text": text,
        "children": (st.get("workflowChildren") or {}).get("children") or [],
    }


def visible(runs):
    """窗口内的 run：在跑，或近 10 分钟活动过（不然历史 run 会把每一行都涂满）。"""
    return [r for r in runs if r["state"] == "running"
            or (r["idle"] is not None and r["idle"] < _WINDOW)]


def lane_index(runs):
    """lane 名（= 节点 id）→ 运行事实。同一节点被派过多次 ⇒ 优先留**在跑**的那条。"""
    by_node = {}
    for parent in runs:
        for child in parent["children"]:
            node = child.get("childId")
            if not node:
                continue
            run = next((r for r in runs if r["runId"] == child.get("runId")), None)
            if run is None and str(child.get("state") or "") != "running":
                continue
            entry = {"parent": parent, "child": child, "run": run}
            old = by_node.get(node)
            if old is None or (run is not None and run["state"] == "running"
                               and (old["run"] is None or old["run"]["state"] != "running")):
                by_node[node] = entry
    return by_node


def items(proj):
    """投影 → 条目列表：每棵树一个容器行（= 需求）+ 它的任务行，带 tree/parent/stage/status。"""
    out = []
    trees = (proj or {}).get("trees") or {}
    for slug in sorted(trees):
        tree = trees[slug]
        if not isinstance(tree, dict) or not isinstance(tree.get("nodes"), dict):
            continue
        nodes = tree["nodes"]
        for node_id in sorted(nodes):
            node = nodes[node_id]
            if not isinstance(node, dict):
                continue
            out.append({
                "node": node_id,
                "tree": slug,
                "parent": node.get("parent"),
                "kind": node.get("kind") or "task",
                "stage": str(node.get("stage") or "?"),
                "status": str(node.get("status") or "pending"),
                "goal": node.get("goal") or "",
                "closed_tree": bool(tree.get("closed")),
            })
    return out


def indent_of(row, by_id):
    """列内缩进深度 = **同列祖先链**长度（父不在同列就不缩进）。"""
    depth, seen = 0, {row["node"]}
    parent = row["parent"]
    while parent and parent in by_id and parent not in seen:
        seen.add(parent)
        if by_id[parent]["stage"] == row["stage"]:
            depth += 1
        parent = by_id[parent]["parent"]
    return depth


def lane_line(lane, verbose):
    """在跑任务的实时事实：`▸ lane … · 状态 · 已跑 · 静默 · 消息数`（+ `-v` 的命令/进展）。"""
    run = lane.get("run")
    name = lane["child"].get("childId") or "?"
    if run is None:
        return f"▸ lane {name} · 运行目录已过窗口（窗口 = 在跑或 10 分钟内活动）"
    pid_state = {True: "活", False: "死", None: "?"}[alive(run["pid"])]
    facts = (f"▸ lane {name} · {run['state']}（pid {run['pid']}({pid_state})）"
             f" · 已跑 {dur(run['elapsed'])} · 静默 {dur(run['idle'])} · {run['turns']} 条消息")
    lines = [facts]
    if verbose and run["cmd"]:
        lines.append(f"    ⌘ {run['cmd'][:150]}")
    if verbose and run["text"]:
        lines.append(f"    ↳ {' '.join(run['text'].split())[:150]}")
    return "\n".join(lines)


def effective_status(row, lane):
    """**运行事实优先**：投影里 `status=pending`，但只要有一条在跑的 lane，它就是「运行中」——
    这就是 join 的意义（disflow 只在 collect/done 时改 status，不能拿它当实时状态）。
    """
    run = (lane or {}).get("run")
    if run is not None and run.get("state") == "running":
        return "running"
    return row["status"]


def render(show_all=False, verbose=False):
    """一轮的完整块（自带时间戳头，块间互不依赖）—— 追加式刷新直接照打。"""
    proj = load_projection()
    rows = items(proj)
    runs = [r for r in (load(d) for d in glob.glob(PAT)) if r]
    keep = visible(runs)
    lanes = lane_index(keep)
    for row in rows:
        row["view"] = effective_status(row, lanes.get(row["node"]))
    cols = stages()
    containers = [r for r in rows if r["kind"] != "task"]      # 需求行（树目标），不是待办工作
    tasks = [r for r in rows if r["kind"] == "task"]
    wanted = tasks if show_all else [r for r in tasks if r["view"] in _ACTIVE]
    active_tasks = [r for r in tasks if r["view"] in _ACTIVE]
    per_col = {c: ([r for r in wanted if r["stage"] == c]
                   + [r for r in containers if r["stage"] == c]) for c in cols}
    counts = {c: len(per_col[c]) for c in cols}
    running_lanes = sum(1 for lane in lanes.values()
                        if (lane.get("run") or {}).get("state") == "running")
    out = [f"══ 看板运行视图 @ {time.strftime('%H:%M:%S')} ══",
           "列 = 看板阶段；只列**运行中 + 待完成**" + ("（--all：连终态也列）" if show_all else ""),
           "列 " + " · ".join(f"{c} {counts[c]}" for c in cols)
           + f"\u3000·\u3000在跑 lane {running_lanes} 路"
           + f" · state dir {state_dir()}"]
    if proj is None:
        out += ["", f"✗ 读不到 {os.path.join(state_dir(), 'state.json')}"
                     " —— 没有条目可列；下面只有 pi 运行目录里的事实"]
    elif not rows:
        out += ["", "（投影里没有条目）"]
    if not active_tasks:
        out += ["", "（没有运行中 / 待完成的任务"
                    + ("" if show_all else "；加 --all 连完成/跳过也列") + "）"]
    by_id = {r["node"]: r for r in rows}
    groups = ("running", "pending", "done", "skipped", "failed") if show_all else _ACTIVE
    multi = len({r["tree"] for r in rows}) > 1
    for col in cols:
        items_col = per_col[col]
        mine = [r for r in items_col if r["kind"] == "task"]
        mine_containers = [r for r in items_col if r["kind"] != "task"]
        out += ["", f"[{col}]  {counts[col]}" if items_col else f"[{col}]  0\u3000（空）"]
        if mine_containers:
            out.append("  ◦ 需求（树目标）")
            for row in mine_containers:
                where = f"  ← {row['tree']}" if multi else ""
                out.append(f"    ◦ {row['node']}  {dviews.truncate_goal(row['goal'])}{where}")
        for status in groups:
            group = [r for r in mine if r["view"] == status]
            if not group:
                continue
            out.append(f"  {_GROUP[status]}")
            for row in sorted(group, key=lambda r: (r["tree"], r["node"])):
                goal = "" if status in ("done", "skipped", "failed") else \
                    f"  {dviews.truncate_goal(row['goal'])}"
                where = f"  ← {row['tree']}" if multi else ""
                out.append(f"    {'  ' * indent_of(row, by_id)}{_MARK[status]} {row['node']}"
                           f"{goal}{where}")
                lane = lanes.get(row["node"])
                if lane is not None:
                    out.append("      " + lane_line(lane, verbose))
    others = [r for r in rows if r["stage"] not in cols]
    if others:
        out += ["", f"⚠ 非规范列（不在配置的 stages 内，读时容忍但不静默隐藏）："
                    f"{', '.join(sorted({r['stage'] for r in others}))}"]
    # 读不到就**明说**：lane 名在投影里找不到对应节点时点名（别默不作声地丢掉）
    known = {r["node"] for r in rows}
    orphans = sorted(n for n in lanes if n not in known)
    if orphans:
        out += ["", f"⚠ {len(orphans)} 路 lane 在投影里找不到对应节点：{', '.join(orphans)}"
                    "（不猜一个 goal 顶上）"]
    return out


def footer(iv):
    return (f"（Ctrl-C 退出 · {iv:g}s 刷新 · 状态=status.json.state"
            " · 列=配置的 stages · 追加式：每轮一整块、保留回滚）")


def loop(iv, verbose, show_all):
    try:
        while True:
            sys.stdout.write("\n".join(render(show_all, verbose) + [footer(iv)]) + "\n")
            sys.stdout.flush()
            time.sleep(iv)
    except KeyboardInterrupt:
        return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    verbose = "-v" in argv
    show_all = "--all" in argv
    rest = [a for a in argv if a not in ("-v", "--all")]
    mode = rest[0] if rest else "5"
    if mode == "once" or not sys.stdout.isatty():
        # 非 TTY（管道 / 重定向）：打一轮就退，**零控制序列**
        print("\n".join(render(show_all, verbose)))
        return 0
    try:
        iv = float(mode)
    except ValueError:
        iv = 5.0
    return loop(iv, verbose, show_all)


if __name__ == "__main__":
    sys.exit(main())
