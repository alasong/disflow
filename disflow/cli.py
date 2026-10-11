"""disflow · 命令行（只装配，不含业务逻辑）。

所有业务在 `engine` / `dispatch` / `gates`；本模块只做参数解析与打印。

**两条装配约定**（历史上在这两处各遗漏过一次，合计三种“静默错”）：
1. **旗标必须真接上引擎**：`--dry-run` / `--ttl` 漏传时命令看着成功、却真写了事件/用错租约。
2. **报文通道分明**：成功 → stdout；**失败与门拒收 → stderr**。否则脚本 `2>/dev/null`
   会把失败看成成功。

另：只做形状/存在性校验的动词（`require`/`unrequire`/`relate`/`unrelate`/`produce`/`consume`）
**不配** `--dry-run`——只有跑门的写动词（`start`/`done`/`close`/`drive`）才配（见 `design.md` §7.3）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import boards as boards_mod
from . import config, graph, metrics, store, views, worktree
from .boards import Board
from .engine import Engine, EngineError, GateReject
from .import_mgt import apply_import, build_import, load_mgt
from .metrics import parse_deadline
from .ready import lease_overdue_seconds
from .state import expands_after_close, project


def _dump(payload, file=None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=file)


def _loads(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise EngineError(f"JSON 解析失败：{exc}") from exc


# ── 命令实现 ──────────────────────────────────────────────────────────────

def _boards_from_arg(spec: str) -> tuple[Board, ...]:
    """把 `--boards` 参数变成板定义：逗号分隔的 id，或一个 JSON 文件路径。

    两种都收（简单场景不必写文件）：
      --boards '需求,设计,开发,验证'        # 纯 id 列表
      --boards path/to/boards.json         # 与独立文件同格式（{"boards": [...]})
    形状/保留名/重复都走 `boards.parse`——**响亮拒收**，不猜。
    """
    text = spec.strip()
    if os.path.isfile(text):
        with open(text, encoding="utf-8") as handle:
            raw = json.load(handle)
        return boards_mod.parse(raw, text)
    ids = [piece.strip() for piece in text.split(",") if piece.strip()]
    return boards_mod.parse({"boards": ids}, "--boards")


def cmd_init(engine: Engine, args) -> int:
    chosen: tuple[Board, ...] = ()
    if getattr(args, "boards", None):
        chosen = _boards_from_arg(args.boards)
    engine.init_tree(args.tree, args.goal, boards=chosen)
    extra = f"（{len(chosen)} 块板）" if chosen else ""
    print(f"✓ 建树 {args.tree}：{args.goal}{extra}")
    return 0


def cmd_goal(engine: Engine, args) -> int:
    engine.set_goal(args.tree, args.goal)
    print(f"✓ {args.tree} 目标更新")
    return 0


def cmd_expand(engine: Engine, args) -> int:
    engine.expand(args.tree, args.parent, _loads(args.children),
                  holder=getattr(args, "holder", "") or "")
    print(f"✓ {args.parent} 展开子节点")
    return 0


def cmd_require(engine: Engine, args) -> int:
    engine.require(args.tree, args.node, args.dep)
    print(f"✓ {args.node} requires {args.dep}")
    return 0


def cmd_unrequire(engine: Engine, args) -> int:
    engine.unrequire(args.tree, args.node, args.dep)
    print(f"✓ {args.node} 不再 requires {args.dep}")
    return 0


def cmd_relate(engine: Engine, args) -> int:
    engine.relate(args.tree, args.node, args.other)
    print(f"✓ {args.node} relates {args.other}")
    return 0


def cmd_unrelate(engine: Engine, args) -> int:
    engine.unrelate(args.tree, args.node, args.other)
    print(f"✓ {args.node} 不再 relates {args.other}")
    return 0


def cmd_produce(engine: Engine, args) -> int:
    engine.produce(args.tree, args.node, args.name)
    print(f"✓ {args.node} produces {args.name}")
    return 0


def cmd_consume(engine: Engine, args) -> int:
    engine.consume(args.tree, args.node, args.name)
    print(f"✓ {args.node} consumes {args.name}")
    return 0


def cmd_start(engine: Engine, args) -> int:
    if args.dry_run:
        engine.start(args.tree, args.node, args.holder or "", ttl=args.ttl, dry_run=True)
        print(f"✓ 预演通过：{args.node} 可就绪开工（**未落事件**）")
        return 0
    engine.start(args.tree, args.node, args.holder or "", ttl=args.ttl)
    print(f"✓ {args.node} 开工（holder={args.holder or 'unknown'}）")
    return 0


def cmd_collect(engine: Engine, args) -> int:
    engine.collect(args.tree, args.node, _loads(args.result))
    print(f"✓ {args.node} 结果已校验落盘（未写 done）")
    return 0


def _vacuous_line(label: str, vacuous: dict) -> str:
    """空洞通过的门：一行报出（门没东西可验 ⊊ 门拒收）。"""
    detail = "；".join(f"{gate}（{reason}）" for gate, reason in sorted(vacuous.items()))
    return f"⚠️ {label}空洞通过（无东西可验）：{detail}"


def _has_collected(engine: Engine, tree: str, node: str) -> bool | None:
    """本节点是否已有已落盘的 collect 结果；树/节点不存在 → None（交给门去报）。"""
    try:
        nodes = engine.tree(tree)["nodes"]
    except EngineError:
        return None
    if node not in nodes:
        return None
    return nodes[node].get("collected") is not None


def cmd_done(engine: Engine, args) -> int:
    declarations = _loads(args.decl) if args.decl else None
    dry = bool(getattr(args, "dry_run", False))
    no_result = str(getattr(args, "no_result", "") or "").strip()
    # **宿主循环契约**（CLI 层，不是门）：worker 的结果必须先经 `collect` 落盘再 `done`。
    # 为何在 CLI 而不在引擎：无 collect 的 `done` 是引擎 API 的合法组合（直干型节点不该被逼
    # 伪造一份自报），而事故发生在**走 CLI 的宿主**——它的 collect 失败时 done 静默落盘，
    # 于是 collect（worker 上报 block 的唯一通道，FR-15）形同虚设、树上查不到谁干了什么。
    collected = _has_collected(engine, args.tree, args.node)
    # `_has_collected` 返回 `bool | None`：None = 树/节点不存在（交给门报）。
    # 写成 `is not None and not collected` 而不是 `is False`：语义相同（只有 False 才拦），
    # 但不触发 linter 的「字面量恒等」规则。
    if collected is not None and not collected and not no_result:
        print(f"✗ 拒收 {args.node}：本节点没有已落盘的 collect 结果——", file=sys.stderr)
        print("  这是宿主循环契约（不是门）：worker 的结果要先经 collect 落盘，"
              "否则树的读者看不出「谁干了什么」、blocked 提问也无处留痕。", file=sys.stderr)
        print(f"  先跑：disflow collect {args.tree} {args.node} --result "
              f"'{{\"status\": \"ok\", \"summary\": \"<一句话>\", "
              f"\"changed_files\": [\"<路径>\"]}}'", file=sys.stderr)
        print("  （确实没有 worker 结果时显式破例："
              f"disflow done {args.tree} {args.node} --no-result \"<具体理由>\"）",
              file=sys.stderr)
        return 1
    try:
        outcome = engine.done(args.tree, args.node, declarations, dry_run=dry,
                              no_result=no_result)
    except GateReject as reject:
        print(f"✗ {'预演会被拒' if dry else '门拒收'} {args.node}：", file=sys.stderr)
        for item in reject.missing:
            print(f"  - {item}", file=sys.stderr)
        print("契约片段：", file=sys.stderr)
        _dump(reject.contract, file=sys.stderr)
        return 2
    vacuous = _done_vacuous(outcome, dry, args)
    if dry:
        print(f"✓ 预演通过：{args.node} 的验收/作用域/规模门均会过（**未落事件**）")
    else:
        print(f"✓ {args.node} done（证据已自算落盘）")
    if collected is not None and not collected:
        # 破例已落进事件（evidence.no_result）；stdout 再明说一句：绝不静默。
        print(f"⚠️ {args.node} 无 worker 结果（--no-result 破例：{no_result}）")
    if vacuous:
        print(_vacuous_line("门", vacuous))
    return 0


def _done_vacuous(outcome: dict, dry: bool, args) -> dict:
    """取本节点的空洞门清单（预演与实写两条路径都能取到）。"""
    if dry:
        return ((outcome.get("extra") or {}).get("evidence") or {}).get("vacuous") or {}
    node = (((outcome.get("trees") or {}).get(args.tree) or {}).get("nodes") or {}).get(args.node)
    return ((node or {}).get("evidence") or {}).get("vacuous") or {}


def cmd_fail(engine: Engine, args) -> int:
    engine.fail(args.tree, args.node, args.reason)
    print(f"✓ {args.node} 标记 failed：{args.reason}")
    return 0


def cmd_retry(engine: Engine, args) -> int:
    engine.retry(args.tree, args.node)
    print(f"✓ {args.node} 重置为 pending")
    return 0


def cmd_skip(engine: Engine, args) -> int:
    engine.skip(args.tree, args.node, args.reason or "")
    print(f"✓ {args.node} 已 skip")
    return 0


def cmd_drive(engine: Engine, args) -> int:
    result = engine.drive(args.tree)
    if getattr(args, "dry_run", False):
        result["dry_run"] = True
    if args.json:
        _dump(result)
        return 0
    progress = result["progress"]
    print(f"树 {args.tree}：{result['goal']}")
    print(f"进度 {progress['done']}/{progress['tasks']}（{progress['pct']}%）"
          f" · 运行中 {len(result['running'])} · 卡死 {'是' if result['stuck'] else '否'}")
    plan = engine.plan(args.tree)
    deferred = result.get("deferred") or []
    if not plan:
        # 有就绪节点却无可派者：别谎报「无就绪」，点名是写面被在跑节点挡下。
        print("无就绪节点可派：以下节点就绪但被在跑节点的写面挡下。" if deferred
              else "无就绪节点。")
    else:
        print(f"就绪首波 {len(result['ready_wave'])} 个 / 共 {len(result['waves'])} 波"
              f"（**只推不跑**，spawn 归宿主）：")
        for item in plan:
            print(f"  {item['node']}  {item['stage']}/{item['role']}  {item['goal']}")
            print(f"    → {item['brief_cmd']}")
    if deferred:
        # **绝不静默丢**：被挡下的节点连同原因（在跑的谁 + 重叠哪些路径）逐条报出。
        print("写面被在跑节点占用（本波不派，防并写冲突；等它们收尾或改 touch_plan）：")
        for item in deferred:
            print(f"  {item['node']}  {item['why']}")
    if result["blocked"]:
        print("被阻塞：")
        for node_id, deps in sorted(result["blocked"].items()):
            print(f"  {node_id} ← {', '.join(deps)}")
    return 0


def cmd_host_next(engine: Engine, args) -> int:
    """宿主「下一步」（FR-16 的**派活面**）：一次只读调用给出本轮该派的全部。

    默认人类可读；`--json` 给机器可读（含每个就绪节点的完整 brief 与命令）。
    """
    payload = engine.host_next(args.tree)
    if args.json:
        _dump(payload)
        return 0
    progress = payload["progress"]
    print(f"树 {args.tree}：{payload['goal']}")
    print(f"进度 {progress['done']}/{progress['tasks']}（{progress['pct']}%）"
          f" · 就绪 {len(payload['ready_wave'])} · 运行中 {len(payload['running'])}"
          f" · 卡死 {'是' if payload['stuck'] else '否'}")
    if not payload["plan"]:
        # 无就绪节点：**显式**说清是全部完成还是卡住，不静默空。
        # 全被在跑节点挡下那一格（`state=waiting`）的 deferred 已在 `state_message` 里说清 ⇒
        # 此处不重复打印（同一屏不出现两遍同样的话）。
        print(f"[{payload['state']}] {payload['state_message']}")
        for node_id, deps in sorted(payload["blocked"].items()):
            print(f"  被阻塞 {node_id} ← {', '.join(deps)}")
        return 0
    print(f"就绪波次：{len(payload['waves'])} 波（首波 {len(payload['ready_wave'])} 个，"
          "同波写面互不重叠）")
    for wave_index, wave in enumerate(payload["waves"], 1):
        print(f"  第 {wave_index} 波：{', '.join(wave)}")
    print("下面每个节点的 brief 即 worker 的 task（**原样给**），命令照抄：")
    for entry in payload["plan"]:
        print(f"▶ {entry['node']}  [{entry['stage']}/{entry['role']}]  第 {entry['wave']} 波")
        print(f"    goal:    {entry['goal']}")
        print(f"    start:   {entry['start_cmd']}")
        print(f"    collect: {entry['collect_cmd']}")
        print(f"    done:    {entry['done_cmd']}")
        print(f"    提问纪律：{entry['brief']['question_protocol']}")
    deferred = payload.get("deferred") or []
    if deferred:
        # **绝不静默丢**：就绪却与在跑节点写面重叠的候选不进 plan，但不许在屏幕上无声消失
        # （JSON 里有、屏幕上没有 = 只对机器人诚实）——逐条报出节点 + 在跑的谁 + 重叠哪些路径。
        # 措辞与 `drive` 的人读态一致（同一批读者）；全被挡那格由 `state_message` 说（见上分叉），
        # 此处不重复打印。
        print("写面被在跑节点占用（本轮不派，防并写冲突；等它们收尾或改 touch_plan）：")
        for item in deferred:
            print(f"  {item['node']}  {item['why']}")
    print("（完整 brief 见 --json：宿主可直接把 plan[].brief 作为 worker 的 task）")
    return 0


def cmd_brief(engine: Engine, args) -> int:
    _dump(engine.brief(args.tree, args.node))
    return 0


def cmd_status(engine: Engine, args) -> int:
    if args.tree:
        status = engine.status(args.tree)
        if args.json:
            _dump(status)
            return 0
        progress = status["progress"]
        print(f"树 {args.tree}：{status['goal']}")
        print(f"进度 {progress['done']}/{progress['tasks']}（{progress['pct']}%）"
              f" · 就绪 {len(status['ready'])} · 运行中 {len(status['running'])}"
              f" · 卡死 {'是' if status['stuck'] else '否'}"
              f" · 已关闭 {'是' if status['closed'] else '否'}")
        return 0
    trees = engine.trees()
    if not trees:
        print("（无树）")
        return 0
    for name in trees:
        status = engine.status(name)
        progress = status["progress"]
        print(f"{name}: {progress['done']}/{progress['tasks']}（{progress['pct']}%）"
              f" · 就绪 {len(status['ready'])} · 卡死 {'是' if status['stuck'] else '否'}"
              f" · {status['goal']}")
    return 0


def cmd_plan(engine: Engine, args) -> int:
    _dump(engine.plan(args.tree))
    return 0


def cmd_rebuild(engine: Engine, args) -> int:
    if args.check:
        matched, diffs = store.projection_matches(engine.spec, project=project)
        if matched:
            print("✓ 投影一致")
            return 0
        print("✗ 投影不一致：")
        for item in diffs:
            print(f"  - {item}")
        return 1
    state = store.rebuild(engine.spec, project=project)
    store.write_projection(engine.spec, store.stamp_projection(
        state, len(store.load_events(engine.spec))))
    print("✓ 投影已从事件重建")
    return 0


def _human_seconds(seconds: float) -> str:
    """把秒数说成人能读的样子（用于“租约过期多久”）。"""
    total = int(seconds)
    if total < 60:
        return f"{total} 秒"
    if total < 3600:
        return f"{total // 60} 分 {total % 60} 秒"
    if total < 86400:
        return f"{total // 3600} 小时 {total % 3600 // 60} 分"
    return f"{total // 86400} 天 {total % 86400 // 3600} 小时"


# `doctor` 的“需人判断”条目——**不计入退出码**（错在把两种病因当成一种报）
_DOCTOR_ADVISORY_FOOTNOTE = (
    "两种读法：① worker 已崩 → `disflow reap <tree>` 收回（节点退回 pending，\n"
    "             已落盘的事件不丢）；\n"
    "           ② 人还在干 → 直接 `disflow done <tree> <node> --decl …`\n"
    "             （**done 不要求租约**——租约防的是并发，不是“收工晚了”）。\n"
    "想避开这条：`start --ttl <秒>` 或配置 `lease_ttl`。"
)


def _state_dir_inside_repo(engine: Engine) -> tuple[str, str] | None:
    """state dir 解析后（realpath，逐层绕过符号链接）是否落在 repo 内部。

    落在内部时返回 `(state_dir 真实路径, repo 真实路径)`，否则返回 `None`。
    判据与 `worktree._reject_if_inside` **同源**：worktree 落点 `<state_dir>/worktrees`
    派生自 state dir，故「state dir 在仓内」⇔「worktree 根在仓内」⇔ `worktree apply` 必被拒收。
    这里**主动**算这条（而非等 `worktree` 动词报错），doctor 才能在本环节提前说出
    「已交付的 worktree 功能在当前 state dir 配置下不可用」。
    相对 state dir 一律以 `repo`（= `engine.cwd`，与 worktree 模块的 `repo` 同一来源）为基准解析，
    两个 cwd 下判定必须一致。
    """
    raw = store.state_dir(engine.spec)
    repo = os.path.realpath(engine.cwd)
    path = raw if os.path.isabs(raw) else os.path.join(engine.cwd, raw)
    resolved = os.path.realpath(path)
    if resolved == repo or resolved.startswith(repo + os.sep):
        return resolved, repo
    return None


def cmd_doctor(engine: Engine, args) -> int:
    problems: list[str] = []
    advisories: list[str] = []
    matched, diffs = store.projection_matches(engine.spec, project=project)
    if not matched:
        problems.extend(f"投影漂移：{d}" for d in diffs)
        # 报告要带修法：漂移是投影函数变了/被手改，事件才是真相
        problems.append("修法：`disflow rebuild` 从事件重算投影（投影是派生的，事件是真相）")
    # 「已交付的 worktree 功能在当前 state dir 配置下不可用」——**主动发现**，不等 worktree 动词报错。
    # 这一条是**需人判断**（不是故障，不计入退出码）：仓内 state dir 是**出厂默认**（`spec.py` 的
    # `state.dir = ".disflow"`），不是误配；它只限制一项**可选功能**（worktree）的可用性，其余功能正常。
    # 判成故障会因默认布局把所有人假红（假红比不报更坏）——但也不能静默：单独一条、单独一行报出来，
    # 才兑现「提前可见」这个收益。
    worktree_advisory: str | None = None
    inside_repo = _state_dir_inside_repo(engine)
    if inside_repo is not None:
        state_real, repo_real = inside_repo
        worktree_advisory = (
            f"worktree 功能不可用：state dir 落在 repo 内部（{state_real}，repo={repo_real}）"
            "——已交付功能（worktree）在当前 state dir 配置下的可用性限制，其余功能正常；"
            "修法：把 [state].dir 或 DISFLOW_STATE_DIR 配到仓外")
    state = engine.load()
    now_ts = time.time()
    for name, tree in sorted(state.get("trees", {}).items()):
        cycles = graph.find_cycles(tree["nodes"])
        if cycles:
            problems.append(f"{name}: requires 成环 {cycles}")
        for node_id, node in tree["nodes"].items():
            if node.get("status") == "running":
                overdue = lease_overdue_seconds(node.get("lease"), now_ts)
                if overdue is not None:
                    holder = (node.get("lease") or {}).get("holder") or "?"
                    advisories.append(
                        f"{name}/{node_id}: 租约已过期 {_human_seconds(overdue)}"
                        f"（holder={holder}）")
            for dep in node.get("requires") or []:
                if dep not in tree["nodes"]:
                    problems.append(f"{name}/{node_id}: requires 悬空 {dep}")
            for consumed in node.get("consumes") or []:
                if consumed not in tree["assets"]:
                    problems.append(f"{name}/{node_id}: consumes 未登记 {consumed}")
    # 「关树后又挂任务」：**只能从事件顺序判**（终态里它和「合法地带着未完成任务关树」
    # 长得一模一样）。这条不是新守卫，而是**事后探测**：`expand` 的拒收只管从今往后。
    for item in expands_after_close(store.load_events(engine.spec)):
        who = f"（holder={item['holder']}）" if item["holder"] else ""
        problems.append(
            f"{item['tree']}: {item['closed_at']} 已关闭，但之后又挂了任务"
            f"（{item['expanded_at']} · node={item['node']}）{who}"
            f"—— awaits 的完成信号已失真；若确实要继续，应显式 `disflow reopen {item['tree']}`")
    if problems:
        # 失败报文统一走 stderr（与 GateReject 一致）：脚本 2>/dev/null 时不会把失败看成成功。
        # stdout 必须为空——“失败时 stdout 干净”是既有契约（tests/test_cli_readonly.py）。
        # 配置信息不丢：路径并进 stderr 报告（一行）。
        print(f"✗ doctor 发现（配置：{config.config_path(engine.cwd)}）：", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        if advisories or worktree_advisory is not None:
            print("  （另有需人判断项，因本轮已有故障而不单独报；见 exit 0 时的输出）",
                  file=sys.stderr)
        return 1
    # 全绿时把“这些值从哪来”报出来：配置是独立文件，改了它行为会变，所以得看得见。
    # **逐键报来源**（两个配置文件了）：板定义在 `boards.json`，引擎参数在 `disflow.json`
    # —— 报一个笼统路径会让读者以为板也住在 disflow.json 里。按来源**分组**打印，
    # 免得同一行长路径重复 9 遍。
    groups: dict[str, list[tuple[str, str]]] = {}
    for key, value, source in config.describe(engine.cfg, engine.cwd):
        groups.setdefault(source, []).append((key, value))
    print("配置（逐键报来源）：")
    for source, items in groups.items():
        print(f"  {source}")
        for key, value in items:
            print(f"    {key} = {value}")
    if worktree_advisory is not None or advisories:
        # **不计入退出码**，也不叫“故障”：——租约条目是「worker 崩了」还是「人在正常干活」
        # doctor 分不出；worktree 条目是出厂默认布局下的功能可用性限制。两条都不假装知道，
        # 只给事实 + 修法/两种读法。worktree 条目置顶且自成一行（不与别的检查拼在一行）。
        print("⚠ 需人判断（不是故障，不计入退出码）：")
        if worktree_advisory is not None:
            print(f"  - {worktree_advisory}")
        for item in advisories:
            print(f"  - {item}")
        if advisories:                 # 脚注只讲租约过期：没有租约项就不印
            print(_DOCTOR_ADVISORY_FOOTNOTE)
    print("✓ doctor 全绿")
    return 0


def cmd_board(engine: Engine, args) -> int:
    # 列定义的**单一来源 = 该树的板集**（`tree.init`/`tree.boards` 事件携带 → 投影携带）。
    # 逐树覆盖后，同一进程里不同树可以有不同的板——用进程级 `cfg.stages` 会张冠李戴。
    # 旧事件流（不带 boards）由 `_tree_boards` 回落 cfg ⇒ 兼容。
    tree = engine.tree(args.tree)
    tree_boards = engine._tree_boards(tree)
    stages = tuple(board.id for board in tree_boards)
    labels = {board.id: board.label for board in tree_boards}
    if args.stage is not None and args.stage not in stages:
        # 非法列名**响亮拒收**（退出码 2 = 用法错误，与 argparse 同款）：放行会打出
        # `[bogus] 0/0` —— 读者会当成「这一列是空的」，实际是**拼错了**（宽错）。
        # 为何不用 argparse 的 `choices=`：合法列来自**运行期解析的配置**（`boards.json`
        # 的板 id），parser 在解析配置之前就建好了；写死一份字面量就是**第二份列定义**
        # （两面分叉），自定义板也会被误拒。
        print(f"✗ --stage 取值非法：{args.stage!r}（允许：{list(stages)}）", file=sys.stderr)
        return 2
    if args.by == "board" and args.stage is not None:
        # 四块板**已经**按阶段分板，再选一列是用法错误：放行会打出「只有一块板」的版面，
        # 读者会以为其余三块板丢了（宽错）。响亮拒收，退出码 2 与 argparse 同款。
        print("✗ --by board 与 --stage 互斥（四块板已按阶段分板）", file=sys.stderr)
        return 2
    # 默认只列「运行中 + 待完成」（用户 2026-10-11：要清晰干净的视图）；`--all` 展开终态。
    # 空列默认不铺版面（`--all-columns` 铺；`--json` 恒铺 = 机器看事实）；但**事实不丢**：
    # 表头的「列」摘要把每一列（含折叠掉的空列）都报出来。
    active_only = not args.all
    empty_columns = bool(args.all_columns or args.json or args.stage)
    kwargs = {"stages": stages, "closed": engine.closed_trees(), "tree_name": args.tree,
              "active_only": active_only, "empty_columns": empty_columns}
    if args.json:
        # 机器视图 = **事实全量**：全部列 + 全部状态（不受 --all/--all-columns 影响；要过滤由消费者做）。
        # 人读态是同一份布局的**渲染**（默认折叠终态/空列）——“同源”指同一实现，不是同一子集。
        if args.by == "board":
            _dump(views.boards_layout(tree, stages, labels=labels,
                                      **{**kwargs, "active_only": False, "empty_boards": True}))
            return 0
        _dump(views.board_layout(tree, args.stage,
                                 **{**kwargs, "active_only": False, "empty_columns": True}))
        return 0
    # 摘要要**全部列 × 全部状态**（与默认折叠无关）——同一布局实现，只换两个开关，不另算一套。
    full = views.board_layout(tree, args.stage,
                              **{**kwargs, "active_only": False, "empty_columns": True})
    print(f"══ 看板 {args.tree} ══ {views.truncate_goal(tree.get('goal'), 86)}")
    print("列 " + " · ".join(f"{c['stage']} {c['done']}/{c['tasks']}" for c in full["columns"]))
    tally: dict[str, int] = {}
    for column in full["columns"]:
        for group in column["groups"]:
            if group["group"] != "structure":
                tally[group["label"]] = tally.get(group["label"], 0) + len(group["rows"])
    folded = "" if (args.all or not tally) else "    （终态折叠：--all 展开）"
    print("状态 " + (" · ".join(f"{label} {n}" for label, n in tally.items()) or "（无）") + folded)
    print()
    if args.by == "board":
        # 四块板：同一份布局换一种分组（`boards_layout` 直接消费 `board_layout` 的列）。
        # **空板恒铺**（与列形态的「干净优先」相反）：四块板的「四」是**结构事实**——
        # 折叠掉空板，读者就看不见「需求→设计→开发→验证」这条流程本身，
        # 而「需求板是空的」正是他此刻最该看到的那条事实。故不受 `--all-columns` 影响。
        body = views.boards_lines(tree, stages, labels=labels,
                                  closed=engine.closed_trees(),
                                  tree_name=args.tree, active_only=active_only,
                                  empty_boards=True)
    else:
        body = views.board_lines(tree, args.stage, **kwargs)
    for line in body:
        print(line)
    if not body and not args.all:
        print("（这棵树没有运行中 / 待办的任务 —— 终态与空列请看 --all / --all-columns）")
    return 0


def cmd_tree(engine: Engine, args) -> int:
    for line in views.tree_lines(engine.tree(args.tree)):
        print(line)
    return 0


def cmd_graph(engine: Engine, args) -> int:
    target = engine.tree(args.tree)
    print(views.to_dot(target, args.tree) if args.dot else "\n".join(views.graph_lines(target)))
    return 0


def cmd_import(engine: Engine, args) -> int:
    if args.what != "mgt":
        print(f"✗ 不支持导入源：{args.what}（当前仅 mgt）", file=sys.stderr)
        return 1
    mgt = load_mgt(args.path)
    if not mgt["todos"]:
        print(f"✗ 未找到 mgt 状态：{args.path}/.fat/**/state.json", file=sys.stderr)
        return 1
    stats = apply_import(engine, args.tree, build_import(mgt))
    _dump(stats)
    return 0


def cmd_context(engine: Engine, args) -> int:
    engine.annotate(args.tree, args.node, _loads(args.set))
    print(f"✓ {args.node} context 已合并")
    return 0


def cmd_stage(engine: Engine, args) -> int:
    engine.set_stage(args.tree, args.node, args.stage,
                     reason=args.reason, holder=getattr(args, "holder", "") or "")
    print(f"✓ {args.node} stage → {args.stage}")
    return 0


def cmd_rescope(engine: Engine, args) -> int:
    touch = _loads(args.touch)
    dry = bool(getattr(args, "dry_run", False))
    engine.rescope(args.tree, args.node, touch, args.reason, dry_run=dry)
    if dry:
        print(f"✓ 预演通过：{args.node} 的声明可改为 {touch}（**未落事件**）")
        return 0
    print(f"✓ {args.node} 写面声明已更新：{touch}")
    return 0


def cmd_reopen(engine: Engine, args) -> int:
    reason = getattr(args, "reason", "") or ""
    engine.reopen(args.tree, reason=reason)
    print(f"✓ {args.tree} 已重开（tree.reopen）")
    if reason:
        print(f"  理由已留痕：{reason}")
    else:
        print("  （未给 --reason——事件已落，但事后无法知道为何重开）")
    return 0


def cmd_close(engine: Engine, args) -> int:
    dry = bool(getattr(args, "dry_run", False))
    try:
        engine.close(args.tree, dry_run=dry)
    except GateReject as reject:
        print(f"✗ {'预演会被拒' if dry else '树级门拒收'} {args.tree}：", file=sys.stderr)
        for item in reject.missing:
            print(f"  - {item}", file=sys.stderr)
        print("契约片段：", file=sys.stderr)
        _dump(reject.contract, file=sys.stderr)
        return 2
    if dry:
        print(f"✓ 预演通过：{args.tree} 的 tree_closure + tree_coverage 门均会过（**未落事件**）")
        return 0
    print(f"✓ {args.tree} 已关闭（tree_closure + tree_coverage 门通过）")
    _report_tree_vacuity(engine.tree(args.tree))
    return 0


def _report_tree_vacuity(tree: dict) -> None:
    """关树时汇总“门空洞通过”的节点——否则“门全过”会被读成“门都验了”。

    为何放在 `close`：这是新读者看到的第一屏（看板×N/ N 已关闭）。
    """
    total = 0
    per_gate: dict[str, int] = {}
    for node in tree.get("nodes", {}).values():
        if node.get("status") != "done":
            continue
        total += 1
        for gate in ((node.get("evidence") or {}).get("vacuous") or {}):
            per_gate[gate] = per_gate.get(gate, 0) + 1
    if not per_gate or not total:
        return
    detail = "、".join(f"{gate} {count}" for gate, count in sorted(per_gate.items()))
    print(f"⚠️ 空洞通过（门无东西可验）：{total} 个 done 节点中的 {detail}")


def cmd_reap(engine: Engine, args) -> int:
    reclaimed = engine.reap(args.tree)
    if not reclaimed:
        print("✓ 无过期租约")
        return 0
    print(f"✓ 回收 {len(reclaimed)} 个过期租约：{' '.join(reclaimed)}")
    return 0


def _pts(value) -> str:
    """点数显示：整数不拖 `.0`，小数看得见（`0.5` 点与 `0` 点是两回事）。"""
    return f"{value:g}" if isinstance(value, (int, float)) else str(value)


def _days(text: str, *, remaining: int | None, overdue: int | None) -> str:
    """把 `剩余/逾期` 读数说成人话；`remaining is None` 时返回 `text`（通常是「未声明」）。"""
    if remaining is None:
        return text
    if overdue:
        return f"{text} · **逾期 {overdue} 天**"
    return f"{text} · 剩 {remaining} 天"


# 量化里「尚未声明」这一类事实的措辞：与其它地方同源，避免两处文案漂
_NO_DEADLINE = "未声明"


def _ideal_tail(ideal: list[dict], *, ideal_field: str) -> str:
    """理想线读数的尾巴：`（理想线 → <末点日> 归零）`。

    **「归零」只允许用在末点值确实为 0 的那天。** 末点日就是 deadline、值恒为 0
    （`metrics.burndown` 这么构造），但措辞不得寄生在这个不变量上：末值 > 0（线被截断）时
    如实报还剩多少——本模块曾把「被截断在今天的末点」当成归零日，在值是 22 的那天说「归零」。

    `ideal_field` 指明取哪一列（`ideal` 点数 / `ideal_nodes` 节点数），与 `_burn_line` 的
    `field` 一样：两个维度各取自己那一列，不靠猜。
    """
    last = ideal[-1]
    if last[ideal_field] != 0:
        return f"（理想线 → {last['day']} 剩 {_pts(last[ideal_field])}）"
    return f"（理想线 → {last['day']} 归零）"


def _burn_line(label: str, series: list[dict], *, field: str, ideal,
               ideal_field: str) -> str:
    """一行燃尽读数：`<维度> 首日 值 → 末日 值（理想线 → 日 归零）`。

    `field` 明确指定取哪一列（`remaining` 点数 / `remaining_nodes` 节点数）——两个维度
    各打印一行、各有标签，不把两个数混成一个。

    首末两点都取 `series`（实际读数只到今天）；理想线是**另一条 x 轴**（画到 deadline，
    与 `series` 故意不等长），故它的读数另由 `_ideal_tail` 取，不拿 `series` 的下标去凑。
    """
    first, last = series[0], series[-1]
    tail = _ideal_tail(ideal, ideal_field=ideal_field) if ideal else ""
    return (f"{label}{first['day']} {_pts(first[field])}"
            f" → {last['day']} {_pts(last[field])}{tail}")


def cmd_metrics(engine: Engine, args) -> int:
    """量化：节点数（永远有）+ `points`（声明）+ `velocity`/`burndown`（派生）。只读。

    节点数维度与点数维度并列打印、各带标签：只声明 points 时，未声明点数的树也能看到
    「几个节点、完几个、剩几个」——否则燃尽只剩「0 → 0」，一行没有信息量。
    """
    tree = engine.tree(args.tree)
    events = store.load_events(engine.spec)
    now_ts = time.time()
    summary = metrics.summary(tree, events, slug=args.tree, now_ts=now_ts)
    burn = metrics.burndown(tree, events, slug=args.tree, now_ts=now_ts)
    if args.json:
        _dump({"summary": summary, "burndown": burn})
        return 0
    print(f"树 {args.tree}：量化")
    points_declared = summary["points_total"] > 0
    # 节点数维度：不受 points 是否声明影响（标签写明是「节点」）。
    print(f"  节点    {summary['nodes_total']}"
          f"（已完成 {summary['nodes_done']}，剩 {summary['nodes_left']}）")
    if not points_declared:
        print(f"  点数    未声明——{summary['points_unestimated']} 个 task 节点都没给 `points`")
        print("          （估点是**人给的声明**，引擎不猜；所以这里不会自己编一个数出来）")
    else:
        print(f"  点数    {_pts(summary['points_total'])}"
              f"（已完成 {_pts(summary['points_done'])}，剩 {_pts(summary['points_left'])}）"
              f" · 未估 {summary['points_unestimated']} 个节点")
        if summary["eta_days"] is None:
            why = ("起点未知" if summary["start_reason"] else "不足一天（同日）·速度还是 0")
            print(f"  进度    {summary['days_elapsed']} 天 · 速度 {_pts(summary['velocity'])} 点/天"
                  f" → 预计完工：无法给出（{why}）")
        else:
            print(f"  进度    {summary['days_elapsed']} 天 · 速度 {_pts(summary['velocity'])} 点/天"
                  f" → 预计还需 {_pts(summary['eta_days'])} 天")
    if summary["start_reason"]:
        print(f"  起点    ⚠ {summary['start_reason']}")
    print(f"  截止    {_days(summary['deadline'] or _NO_DEADLINE,
                             remaining=summary['deadline_remaining_days'],
                             overdue=summary['deadline_overdue_days'])}")
    if summary["deadline_reason"]:
        print(f"          ⚠ {summary['deadline_reason']}")
    if burn["series"]:
        # 两行：节点数与点数各带标签（点数未声明时点数那一行不打印——留着只会是「0 → 0」）。
        print("  燃尽    " + _burn_line("节点  ", burn["series"],
                                              field="remaining_nodes",
                                              ideal=burn["ideal_nodes"],
                                              ideal_field="ideal_nodes"))
        if points_declared:
            print("          " + _burn_line("点数  ", burn["series"],
                                            field="remaining",
                                            ideal=burn["ideal"],
                                            ideal_field="ideal"))
    if burn["ideal_reason"]:
        print(f"  理想线  ⚠ {burn['ideal_reason']}")
    return 0


def cmd_deadline(engine: Engine, args) -> int:
    """设/清树级 `deadline`（迭代边界）。非法日期在写时响亮拒收（不落事件）。"""
    engine.deadline(args.tree, args.date)
    value = parse_deadline(args.date)
    if value is None:
        print("✓ deadline 已清空")
        return 0
    tree = engine.tree(args.tree)
    summary = metrics.summary(tree, store.load_events(engine.spec), slug=args.tree,
                              now_ts=time.time())
    print(f"✓ deadline 已设为 {value}"
          + (f" · **逾期 {summary['deadline_overdue_days']} 天**"
             if summary["deadline_overdue_days"]
             else f" · 剩 {summary['deadline_remaining_days']} 天"))
    return 0


def _parse_nodes(value: str) -> list[str]:
    """一次 `--nodes` 的值 → 节点名列表：`a,b` 逗号分隔（本次内），`--nodes` 可重复（跨次累加）。

    空串或含空项一律**响亮拒收**（argparse error，退出码 2）。为什么不能宽容：空串若被
    静默读成「没限定」，就会把「点名了却把名字写空」当成「全量建」——正是本参数要防的误建。
    本函数只做**形状**校验（非空项）；名字是否真存在由模块层 `plan` 校验并响亮上抛。
    """
    items = [item.strip() for item in value.split(",")]
    if any(not item for item in items):
        raise argparse.ArgumentTypeError(
            f"节点列表含空项：{value!r}（逗号分隔非空节点名，如 a,b）")
    return items


def _flatten_nodes(groups: list[list[str]] | None) -> list[str] | None:
    """把 `action="append"` 攒下的「每次一组」摊平；`None` 原样透传（= 全量，逐字不变）。"""
    if groups is None:
        return None
    return [nid for group in groups for nid in group]


def _worktree_call(args, handler):
    """组装层把底层异常归一到 `EngineError`：`worktree` 抛 `KeyError`（名字写错）
    与 `WorktreeError`（探针失败 / 真跑 git 失败），两者若直接漏出去就是抛栈而不是一行人话。

    `WorktreeProbeError` 是 `WorktreeError` 的子类、单独先捕，以便保留「读数不可信」的
    具体措辞；其余 `WorktreeError`（如 `apply` 的 `git worktree add` 失败）归为操作失败。"""
    try:
        return handler()
    except KeyError as exc:
        raise EngineError(str(exc).strip("'") or "树/节点不存在") from exc
    except worktree.WorktreeProbeError as exc:
        raise EngineError(f"worktree 对账读数不可信：{exc}") from exc
    except worktree.WorktreeError as exc:
        raise EngineError(f"worktree 操作失败：{exc}") from exc


def cmd_worktree(engine: Engine, args) -> int:
    """每节点 git worktree（**派生资源，不是状态**：路径是 (state_dir, tree, node) 的纯函数）。

    默认**只推不跑**：`plan`/`list`/`reap` 零副作用；只有 `apply`（**创建**，可逆且幂等）真跑 git。
    `reap` 是**纯计划**——删除交还宿主跑它打印出来的命令：删是唯一不可逆动作，引擎不替人删数据。
    """
    engine.tree(args.tree)                 # 名字写错在这里就用 EngineError 拦下
    spec, tree, cwd = engine.spec, args.tree, engine.cwd
    action = args.action
    nodes = _flatten_nodes(args.nodes)
    if nodes is not None and action != "apply":
        # 只有 apply 把「按节点子集建」落到 git 副作用上；其余动作不支持就响亮拒收，
        # 绝不静默忽略——静默忽略会让 `plan --nodes a` 读成一个看似成功的全量计划。
        raise EngineError(
            f"worktree {action} 不支持 --nodes（节点子集只对 apply 有意义）")
    force = bool(getattr(args, "force", False))
    if force and action != "reap":
        # 同 --nodes 的纪律：不支持的动词响亮拒收，绝不静默忽略——静默忽略会让
        # `apply --force` 读成一个看似生效的强删。
        raise EngineError(
            f"worktree {action} 不支持 --force（显式强删计划只对 reap 有意义）")
    if action == "plan":
        report = _worktree_call(args, lambda: worktree.plan(spec, tree, cwd))
        if args.json:
            _dump(report)
            return 0
        if not report["plan"]:
            print("（无可建 worktree：没有非终态的 task 节点）")
            return 0
        print(f"worktree 计划（根：{report['root']}）")
        for entry in report["plan"]:
            mark = "存在" if entry["status"]["exists"] else "待建"
            print(f"  {entry['node']}  [{mark}]  {entry['status']['path']}")
            print(f"        {' '.join(entry['add_cmd'])}")
        print("只推不跑：要真建请 `disflow worktree apply <tree>`")
        return 0
    if action == "list":
        report = _worktree_call(args, lambda: worktree.reconcile(spec, tree, cwd))
        if args.json:
            _dump(report)
            return 0
        print(f"worktree 对账（树 {tree}）")
        for leak in report["leaks"]:
            dirty = "（**有未提交改动**）" if leak.get("dirty") else ""
            ignored = f"（被忽略文件 {len(leak['ignored'])} 个）" if leak.get("ignored") else ""
            print(f"  leak      {leak['node']}: 节点已 {leak['status']} 但 worktree 还在{dirty}{ignored}")
        for nid in report["missing"]:
            print(f"  missing   {nid}: 节点在跑却没有 worktree")
        for path in report["orphans"]:
            print(f"  orphan    {path}: 对不上任何已知节点")
        for item in report["residual"]:
            print(f"  residual  {item['node']}: 分支 {item['branch']} 还在（节点已 {item['status']}）")
        if not any(report.get(k) for k in ("leaks", "missing", "orphans", "residual")):
            print("  ✓ 干净")
        return 0
    if action == "apply":
        report = _worktree_call(args, lambda: worktree.apply(spec, tree, cwd, nodes=nodes))
        if args.json:
            _dump(report)
            return 0
        if report.get("pruned"):
            print(f"（顺带清了 {len(report['pruned'])} 条自家命名空间下的陈旧登记）")
        print(f"✓ 建了 {len(report['created'])} 个：{' '.join(report['created']) or '（无）'}"
              f" · 重新附着 {len(report['reattached'])}"
              f" · 已存在跳过 {len(report['skipped'])}")
        for nid in report["reattached"]:
            print(f"  ↩ {nid}：接着上次的分支继续（未合并的提交还在）")
        for nid in report.get("not_applicable") or []:
            print(f"  · {nid}：非活节点，不建工作区")
        if report.get("broken"):
            for item in report["broken"]:
                print(f"✗ 坏状态：{item}", file=sys.stderr)
            return 1
        return 0
    if action == "reap":
        report = _worktree_call(args, lambda: worktree.reap(spec, tree, cwd, force=force))
        if args.json:
            _dump(report)
            return 0
        if not report.get("reliable", True):
            print("⚠ 有不可判定项（探针读不到）——已按拒绝侧处理，别把这些当可删",
                  file=sys.stderr)
        entries = ([("leak    ", item) for item in report["leaks"]]
                   + [("residual", item) for item in report["residual"]])
        if not entries:
            print("✓ 无可回收的 worktree / 残留分支")
            return 0
        print(f"可回收 {len(entries)} 项（**只推不跑**：引擎不执行删除，下面命令由人跑）")
        if force:
            # 破例必须记账：显式 --force 下，逐条先把「将丢弃什么」记进输出，再给强删命令。
            print("  ⚠ 显式 --force：破例强删，下列将被丢弃（记账，逐条留痕）")
            ledger = report.get("discarded") or []
            for item in ledger:
                for loss in item["losses"]:
                    print(f"      {item['node']}：{loss}")
            if not ledger:
                print("      （无——所有条目判据为空，强删不额外丢东西）")
        if any(entry["remove_cmd"] for _kind, entry in entries):
            print("  顺序要紧：先删工作区、再删分支"
                  "（工作区还在时 git 会拒删它占用的分支）")
        for kind, entry in entries:
            print(f"  {kind} {entry['node']}  "
                  + ("✓ 判据为空，可安全回收" if entry["safe_to_drop"] else "⚠ 跑了会丢东西"))
            for reason in entry["reasons"]:
                print(f"           理由：{reason}")
            for warn in entry.get("discard_warnings") or []:
                print(f"           ⚠ {warn}")
            if entry["remove_cmd"]:
                print(f"           删工作区：{' '.join(entry['remove_cmd'])}")
            print(f"           删分支：  {' '.join(entry['branch_cmd'])}")
        return 0
    raise EngineError(f"未知动作：{action}")


def cmd_trees(engine: Engine, args) -> int:
    names = engine.trees()
    if not names:
        print("（无树）")
        return 0
    events = store.load_events(engine.spec)      # 一次读，循环里复用（不每棵树重读）
    now_ts = time.time()
    for name in names:
        status = engine.status(name)
        progress = status["progress"]
        mark = "●" if status["closed"] else "·"
        # 迭代边界：有 deadline 才报余量；无则什么都不说（不编一个“无限期”出来）
        tree = engine.tree(name)
        deadline = tree.get("deadline")
        tail = ""
        if deadline:
            try:
                summary = metrics.summary(engine.tree(name), events,
                                          slug=name, now_ts=now_ts)
                overdue = summary["deadline_overdue_days"]
                tail = (f" · 逾期 {overdue} 天" if overdue
                        else f" · 剩 {summary['deadline_remaining_days']} 天")
            except ValueError:
                tail = " · deadline 非法"
        print(f"{mark} {name}: {progress['done']}/{progress['tasks']}（{progress['pct']}%）"
              f" · 就绪 {len(status['ready'])}{tail} · {status['goal']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disflow", description="任务分发流引擎（只推不跑）")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, handler):
        node = sub.add_parser(name)
        node.set_defaults(handler=handler)
        return node

    node = add("init", cmd_init)
    node.add_argument("tree")
    node.add_argument("--goal", required=True,
                      help="树的总体目标（必填；一句话说清要达成什么）")
    node.add_argument("--boards", default=None,
                      help="该树的板定义（逗号分隔的 id，或 boards.json 路径）——"
                           "省略则用项目级 boards.json / 内建默认；给这个 = 逐树覆盖")

    node = add("goal", cmd_goal)
    node.add_argument("tree")
    node.add_argument("goal")

    node = add("expand", cmd_expand)
    node.add_argument("tree")
    node.add_argument("parent")
    node.add_argument("--children", required=True,
                      help="子节点声明数组（JSON 字符串）：形如 "
                           "[{\"id\":\"a\",\"goal\":\"…\"}]；每项至少含 id")
    node.add_argument("--holder", default="", help="谁挂的，记入事件（便于事后追溯）")

    node = add("require", cmd_require)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("dep")

    node = add("unrequire", cmd_unrequire)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("dep")

    node = add("relate", cmd_relate)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("other")

    node = add("unrelate", cmd_unrelate)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("other")

    node = add("produce", cmd_produce)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("name")

    node = add("consume", cmd_consume)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("name")

    node = add("start", cmd_start)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--holder", default="",
                      help="谁领租约（worker 名/会话 id；记入 lease，逾期可追溯）")
    node.add_argument("--ttl", type=int, default=None, help="租约秒数（默认 900）")
    node.add_argument("--dry-run", action="store_true", help="预演：跑门但不落事件")

    node = add("collect", cmd_collect)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--result", required=True,
                      help="结果契约（JSON 对象）：必含 status/summary/changed_files；"
                           "status ∈ {ok,failed,blocked}")

    node = add("done", cmd_done)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--decl", default="",
                      help="声明（JSON 对象，可选）：只放破例/豁免，如 "
                           "{\"scope_waiver\":\"…\"}；证据由引擎自算")
    node.add_argument("--no-result", default="", metavar="REASON",
                      help="破例：本节点确实没有 worker 结果（无 collect）时显式放行，"
                           "理由落进证据（留痕）；不填则无 collect 一律拒收（宿主循环契约）")
    node.add_argument("--dry-run", action="store_true", help="预演：跑全部门但不落事件")

    node = add("fail", cmd_fail)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--reason", required=True,
                      help="失败原因（必填；记入事件，便于事后追溯）")

    node = add("retry", cmd_retry)
    node.add_argument("tree")
    node.add_argument("node")

    node = add("skip", cmd_skip)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--reason", default="",
                      help="跳过原因（可选；不填事件也会落，但事后查不到原因）")

    node = add("drive", cmd_drive)
    node.add_argument("tree")
    node.add_argument("--json", action="store_true",
                      help="机器可读输出（替代人话版就绪计划）")
    node.add_argument("--dry-run", action="store_true", help="显式只读：确认不落事件（FR-13）")

    node = add("brief", cmd_brief)
    node.add_argument("tree")
    node.add_argument("node")

    node = add("host-next", cmd_host_next)
    node.add_argument("tree")
    node.add_argument("--json", action="store_true", help="机器可读输出")

    node = add("plan", cmd_plan)
    node.add_argument("tree")

    node = add("status", cmd_status)
    node.add_argument("tree", nargs="?")
    node.add_argument("--json", action="store_true",
                      help="机器可读输出（不传 tree 时列全部树）")

    node = add("rebuild", cmd_rebuild)
    node.add_argument("--check", action="store_true",
                      help="只检查投影与事件是否一致（不写回；不一致退出码 1）")

    node = add("board", cmd_board)
    node.add_argument("tree")
    node.add_argument("--stage", default=None,
                      help="只显示该阶段列（如 dev；必须是 boards.json 里的一块板，非法列名退出码 2）；"
                           "省略=配置里的全部阶段列")
    node.add_argument("--all", action="store_true", dest="all",
                      help="展开终态（✓ 完成 / — 跳过 / ✗ 失败）；默认只列「运行中 + 待完成」"
                           "（终态用表头一行摘要报数）")
    node.add_argument("--all-columns", action="store_true",
                      help="把没有任务的空列也铺成列头（默认只在表头「列」摘要里报 `design 0/0`，"
                           "不占版面）；`--json` 恒为全列")
    node.add_argument("--json", action="store_true",
                      help="机器可读输出（与人读态同源：同一份布局，含列/分组/父子/深度）")
    node.add_argument("--by", choices=("column", "board"), default="column",
                      help="分组方式：column=一块板 × 四个阶段列（默认）；"
                           "board=四块板 × 一个阶段（板头报上游完成度，即板间工作流）；"
                           "board 与 --stage 互斥")

    node = add("tree", cmd_tree)
    node.add_argument("tree")

    node = add("graph", cmd_graph)
    node.add_argument("tree")
    node.add_argument("--dot", action="store_true",
                      help="输出 Graphviz DOT（省略=人话版依赖图）")

    node = add("import", cmd_import)
    node.add_argument("what")
    node.add_argument("path")
    node.add_argument("--tree", required=True,
                      help="导入到哪棵树（必填；由 import 建该树，同名已存在会报错）")

    node = add("context", cmd_context)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--set", required=True,
                      help="并入节点 context 的 JSON 对象（浅合并，同键覆盖），"
                           "如 {\"k\":\"v\"}")

    node = add("stage", cmd_stage)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("stage", help="新的看板列（必须是 boards.json 里的板 id）")
    node.add_argument("--reason", required=True,
                      help="为何改列（必填；记入 stage_log，可审计）")
    node.add_argument("--holder", default="",
                      help="谁改的（记入 stage_log，便于追溯）")

    node = add("rescope", cmd_rescope)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--touch", required=True,
                       help='新的写面声明，如 \'["src/a.py","docs/"]\'')
    node.add_argument("--reason", required=True,
                       help="为何现在才要加范围（必填，记入 rescope_log）")
    node.add_argument("--dry-run", action="store_true", help="预演：跑门但不落事件")

    node = add("close", cmd_close)
    node.add_argument("tree")
    node.add_argument("--dry-run", action="store_true", help="预演：跑两门但不落事件")

    node = add("reopen", cmd_reopen)
    node.add_argument("tree")
    node.add_argument("--reason", default="",
                      help="为何重开（可选；不填事件也会落，但事后查不到原因）")

    node = add("reap", cmd_reap)
    node.add_argument("tree")

    node = add("metrics", cmd_metrics)
    node.add_argument("tree")
    node.add_argument("--json", action="store_true", help="机器可读输出")

    node = add("deadline", cmd_deadline)
    node.add_argument("tree")
    node.add_argument("date", help="YYYY-MM-DD；'-' 表示清空（非法日期在写入时即拒）")

    node = add("worktree", cmd_worktree)
    node.add_argument("action", choices=["plan", "list", "apply", "reap"],
                      help="只有 apply（创建，可逆）真跑 git；reap 是纯计划")
    node.add_argument("tree")
    node.add_argument("--json", action="store_true", help="机器可读输出")
    node.add_argument("--nodes", action="append", type=_parse_nodes, default=None,
                      metavar="A,B",
                      help="只对这些节点建（逗号分隔，可重复）；省略=全量，仅 apply 支持")
    node.add_argument("--force", action="store_true",
                      help="reap 专用：显式破例强删（remove 加 --force）"
                           "并把将丢弃的项记进输出；仅 reap 接受")

    add("trees", cmd_trees)
    add("doctor", cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = Engine()
    try:
        return args.handler(engine, args)
    except (EngineError, ValueError, OSError) as exc:
        # ValueError/OSError：事件文件损坏、投影损坏、写失败等 —— 报一行，不抛栈
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
