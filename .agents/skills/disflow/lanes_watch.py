#!/usr/bin/env python3
"""运行视图 v6 —— 「这是什么活」+「跑到哪了」，一屏看完。

v6 的正源（新增，接上 disflow 的树）：
  · goal/stage/status  ← `$DISFLOW_STATE_DIR/state.json` 的投影（键 = 节点 id）
  · lane 名 == disflow 节点 id（宿主派活时 key=node；找不到就明说，不猜）
  为什么要接：以前只读 pi 的 run 目录 ⇒ 只知道「跑到哪了」，不知道「这是什么活」。
  两个存储各答一半，人在两个窗口之间对不上——这才是「看不出做什么的」的病根。

命名（重要，三个词不许再撞）：
  · **看板** = `disflow board <tree>`：**一棵树的 `stage` 列投影**（阶段列，不是第二份存储）。
    它的**列**是 `stage`，默认 `req|design|dev|verify`（可配）。
    设计原文（docs/design-dispatch-tree-graph.md:123）：「工作流模板 = 阶段图（需求 → 设计 → 开发 → 验证）」；
    §4：看板 = 按 `stage` 分组/过滤**同一棵树**的投影。
    ⚠ `done` **不是列**：它是节点**状态**（`status`）——「流程走到哪」与「收尾没有」是两个轴，
    混成一个会造出一个永远为空、且与状态同名的列。
  · **运行视图** = 本文件（宿主侧，读 pi 的 run 目录）：并行 lane 的**实时进度**。
    一个 pi workflow run（= 宿主一次 subagent 调用，可含 1..N 路 lane）就是运行视图里的一个块。
    ⚠ **不许**把它叫「看板」/「工作流」/「派发批次」——它与看板（阶段列）不是一回事。
  · 本文件的 lane 名 = disflow 节点 id；后面的「看板 <树> · 列 <stage>」是**读投影**得来的，不是猜的。

── 历史（v1..v5 的错，留着防止改回去）──

v6 修（2026-10-11）：把 pi workflow run 叫「派发批次」——与看板（阶段列）撞名，改叫**运行视图**；
  看板列取值去掉了 `done`（`done` 是节点**状态**，列 = 需求/设计/开发/验证）。

v5（定稿）。

v1 错：判据用文件 mtime（两个方向都骗）。
v2 错：平铺无分组；「进展」是 tail 原始日志。
v3 错：命令读日志被截断的 bash: 行；分组靠 idle，父 run 的 lastActivityAt 为 None 时塌掉。
v4 错：屏闪（clear）；「进展」从日志猜，抓到的是它读的文档内容。

v5 的正源（全部结构化，不猜）：
  · 分组   status.json.workflowChildren.children[].childId（= 派活时给的 lane 名）
  · 状态   status.json.state + pid 存活直接探测
  · 命令   子会话 session.jsonl 里最后一条 toolCall 的完整 arguments.command
  · 进展   同一份 session.jsonl 里最后一条 assistant text 块的首行（= ⟳ 进展块）
  · 静默   lastActivityAt → lastUpdate → startedAt 逐级回退（父 run 常常没有 lastActivityAt）

用法（建议先 `cd` 到 disflow 仓根）:
  sh .agents/skills/disflow/lanes_watch.sh once     # 快照
  sh .agents/skills/disflow/lanes_watch.sh          # 持续 5s（备用屏，无闪、不留回滚）
  sh .agents/skills/disflow/lanes_watch.sh 2
  sh .agents/skills/disflow/lanes_watch.sh all      # 含全部历史
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

# pi 的 async run 落点：$TMPDIR/pi-subagents-uid-*/async-subagent-runs/*
PAT = os.path.join(tempfile.gettempdir(), "pi-subagents-uid-*",
                   "async-subagent-runs", "*")
ICON = {"running": "●", "complete": "✓", "completed": "✓", "failed": "✗",
        "killed": "⊘", "started": "●"}
DISFLOW_STATE_DIR = (os.environ.get("DISFLOW_STATE_DIR")
                     or os.path.expanduser("~/.disflow/disflow-self"))


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


def trees_index():
    """读 disflow 投影，把 lane 名（= 节点 id）映回它的 goal/stage/status。

    只读 state.json（不 spawn 子进程、不碰事件流）——运行视图不许改状态。
    找不到就返回 None，由调用方**明说找不到**，绝不猜一个 goal 顶上。
    """
    path = os.path.join(DISFLOW_STATE_DIR, "state.json")
    if not os.path.isfile(path):
        return {}
    proj = _read_json(path)
    if proj is None:
        return {}
    idx = {}
    for slug, t in (proj.get("trees") or {}).items():
        for nid, nd in ((t or {}).get("nodes") or {}).items():
            idx[nid] = {"tree": slug,
                        "goal": " ".join(str(nd.get("goal") or "").split()),
                        "status": nd.get("status") or "?",
                        "stage": nd.get("stage") or "?",
                        "points": nd.get("points")}
    return idx


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


def prog_lines(r, want):
    t = (r["text"] or "").strip()
    if not t:
        return ["（还没有助手正文）"]
    out = [x.strip() for x in t.splitlines() if x.strip()][:want]
    return [x[:140] for x in out]


def block(r, label, verbose=False, info=None, looked_up=False):
    icon = ICON.get(r["state"], "?")
    pa = {True: "活", False: "死", None: "?"}[alive(r["pid"])]
    warn = "   ⚠ state=running 但进程没了" if (r["state"] == "running" and pa == "死") else ""
    pad = " " * max(1, 24 - len(label))
    tag = (f" · 看板 {info['tree']} · 列 {info['stage']} · {info['status']}"
           + (f" · {info['points']}pt" if info.get("points") else "")) if info else ""
    b = [f"  {icon} {label}{pad}{r['state']:<9} 已跑 {dur(r['elapsed'])} · 静默 {dur(r['idle'])}"
         f" · pid {r['pid']}({pa}) · {r['turns']} 条消息{tag}{warn}"]
    if info:
        goal = info["goal"]
        b.append(f"       ◈ [{info['tree']}] {goal[:96]}{'…' if len(goal) > 96 else ''}")
    elif looked_up:
        b.append("       ◈ （disflow 里没有这个节点名 —— lane 名与节点 id 不一致？不许猜）")
    if r["cmd"]:
        b.append(f"       ⌘ {r['cmd'][:150]}")
    for t in prog_lines(r, 6 if verbose else 2):
        b.append(f"       ↳ {t}")
    return b


def render(all_, verbose=False):
    idx = trees_index()
    runs = [r for r in (load(d) for d in glob.glob(PAT)) if r]
    keep = [r for r in runs if all_ or r["state"] == "running"
            or (r["idle"] is not None and r["idle"] < 600)]
    kept = {r["runId"] for r in keep}

    # 分组；父 run 只要**任一子可见**就保留（父常缺 lastActivityAt），
    # 且保留后该运行视图下的**全部** lane 都显示（不再被 10 分钟窗口逐个筛掉）
    groups = []
    for p in [r for r in runs if r["mode"] == "workflow"]:
        cids = {c.get("runId") for c in p["children"]}
        kids_all = [r for r in runs if r["runId"] in cids]
        if any(k["runId"] in kept for k in kids_all):
            groups.append((p, kids_all))
    claimed = {k["runId"] for _, kids in groups for k in kids}
    rest = [r for r in keep if r["runId"] not in claimed and r["mode"] != "workflow"]

    nboards = len(groups) + (1 if rest else 0)
    nrunning = sum(1 for r in keep if r["state"] == "running")
    trees = sorted({v["tree"] for v in idx.values()})
    boards = len(trees)
    out = [f"══ 运行视图 @ {time.strftime('%H:%M:%S')} ══",
           f"看板（= disflow 树，列 stage）{boards} 棵："
           f"{', '.join(trees) if trees else '（读不到 state.json）'}",
           f"运行视图（= pi workflow run）{nboards} 个"
           f"（workflow {len(groups)} · 散跑 {1 if rest else 0}）"
           f" · lane {len(keep)} 路 · 在跑 {nrunning} · 静默阈值 10 分钟",
           f"state dir {DISFLOW_STATE_DIR}"]
    for i, (p, kids) in enumerate(groups, 1):
        nrun = sum(1 for k in kids if k["state"] == "running")
        out.append("")
        out.append(f"▌运行视图 {i}/{nboards} · {p['short']}"
                   f"（{nrun} 在跑 / {len(kids)} 路 · 父 {p['state']}）")
        if p["root"]:
            out.append(f"  root {p['root']}")
        for c in p["children"]:
            cid = c.get("childId") or ""
            kid = next((r for r in kids if r["runId"] == c.get("runId")), None)
            out.extend(block(kid, cid, verbose, idx.get(cid), bool(cid)) if kid
                       else [f"  · {cid:<22}{c.get('state')}（已过窗口）"])
    if rest:
        out.append("")
        out.append(f"▌散跑 {nboards}/{nboards}（不属于任何 pi workflow）")
        for r in rest:
            out.extend(block(r, "", verbose, None, False))
    if not keep:
        out.append("")
        out.append("（没有 lane：没有 running 的，也没有近 10 分钟活动过的）")
    return out


def draw(lines, footer=""):
    sys.stdout.write("\033[H" + "\n".join(x + "\033[K" for x in lines)
                     + "\n" + footer + "\033[K\033[J")
    sys.stdout.flush()


def leave(*_):
    sys.stdout.write("\033[?25h\033[?1049l")
    sys.stdout.flush()
    sys.exit(0)


mode = sys.argv[1] if len(sys.argv) > 1 else "5"
verbose = "-v" in sys.argv
if mode in ("once", "all"):
    print("\n".join(render(mode == "all", verbose)))
else:
    try:
        iv = float(mode)
    except ValueError:
        iv = 5.0
    signal.signal(signal.SIGINT, leave)
    signal.signal(signal.SIGTERM, leave)
    sys.stdout.write("\033[?1049h\033[?25l")
    try:
        while True:
            draw(render(False, verbose),
                 f"（Ctrl-C 退出 · {iv:g}s 刷新 · 状态=status.json.state"
                 f" · 看板/列=disflow 投影 · 备用屏：不闪不留回滚）")
            time.sleep(iv)
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
