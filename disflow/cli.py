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

from . import config, graph, metrics, store, views, worktree
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

def cmd_init(engine: Engine, args) -> int:
    engine.init_tree(args.tree, args.goal)
    print(f"✓ 建树 {args.tree}：{args.goal}")
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


def cmd_done(engine: Engine, args) -> int:
    declarations = _loads(args.decl) if args.decl else None
    dry = bool(getattr(args, "dry_run", False))
    try:
        outcome = engine.done(args.tree, args.node, declarations, dry_run=dry)
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
    if not plan:
        print("无就绪节点。")
    else:
        print(f"就绪首波 {len(result['ready_wave'])} 个 / 共 {len(result['waves'])} 波"
              f"（**只推不跑**，spawn 归宿主）：")
        for item in plan:
            print(f"  {item['node']}  {item['stage']}/{item['role']}  {item['goal']}")
            print(f"    → {item['brief_cmd']}")
    if result["blocked"]:
        print("被阻塞：")
        for node_id, deps in sorted(result["blocked"].items()):
            print(f"  {node_id} ← {', '.join(deps)}")
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


def cmd_doctor(engine: Engine, args) -> int:
    problems: list[str] = []
    advisories: list[str] = []
    matched, diffs = store.projection_matches(engine.spec, project=project)
    if not matched:
        problems.extend(f"投影漂移：{d}" for d in diffs)
        # 报告要带修法：漂移是投影函数变了/被手改，事件才是真相
        problems.append("修法：`disflow rebuild` 从事件重算投影（投影是派生的，事件是真相）")
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
        if advisories:
            print("  （另有需人判断项，因本轮已有故障而不单独报；见 exit 0 时的输出）",
                  file=sys.stderr)
        return 1
    # 全绿时把“这些值从哪来”报出来：配置是独立文件，改了它行为会变，所以得看得见
    cfg_path = config.config_path(engine.cwd)
    print(f"配置：{cfg_path}" + ("（存在）" if os.path.isfile(cfg_path) else "（无 → 全用内建默认）"))
    for key, value, _source in config.describe(engine.cfg, engine.cwd):
        print(f"  {key} = {value}")
    if advisories:
        # **不计入退出码**，也不叫“故障”：doctor 无法知道这是“worker 崩了”还是
        # “人在正常干活”——那就不能假装知道，而要给事实（过期多久）+ 两种读法。
        print("⚠ 需人判断（不是故障，不计入退出码）：")
        for item in advisories:
            print(f"  - {item}")
        print(_DOCTOR_ADVISORY_FOOTNOTE)
    print("✓ doctor 全绿")
    return 0


def cmd_board(engine: Engine, args) -> int:
    for line in views.board_lines(engine.tree(args.tree), args.stage):
        print(line)
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


def cmd_metrics(engine: Engine, args) -> int:
    """量化：`points`（声明）+ `velocity`/`burndown`（派生）。只读，不改任何状态。"""
    tree = engine.tree(args.tree)
    events = store.load_events(engine.spec)
    now_ts = time.time()
    summary = metrics.summary(tree, events, slug=args.tree, now_ts=now_ts)
    burn = metrics.burndown(tree, events, slug=args.tree, now_ts=now_ts)
    if args.json:
        _dump({"summary": summary, "burndown": burn})
        return 0
    print(f"树 {args.tree}：量化")
    estimated = summary["points_total"] > 0
    if not estimated:
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
        first, last = burn["series"][0], burn["series"][-1]
        line = f"  燃尽    {first['day']} {_pts(first['remaining'])} → {last['day']} {_pts(last['remaining'])}"
        if burn["ideal"]:
            line += f"（理想线 → {burn['ideal'][-1]['day']} 归零）"
        print(line)
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
        report = _worktree_call(args, lambda: worktree.reap(spec, tree, cwd))
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
    node.add_argument("--goal", required=True)

    node = add("goal", cmd_goal)
    node.add_argument("tree")
    node.add_argument("goal")

    node = add("expand", cmd_expand)
    node.add_argument("tree")
    node.add_argument("parent")
    node.add_argument("--children", required=True)
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
    node.add_argument("--holder", default="")
    node.add_argument("--ttl", type=int, default=None, help="租约秒数（默认 900）")
    node.add_argument("--dry-run", action="store_true", help="预演：跑门但不落事件")

    node = add("collect", cmd_collect)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--result", required=True)

    node = add("done", cmd_done)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--decl", default="")
    node.add_argument("--dry-run", action="store_true", help="预演：跑全部门但不落事件")

    node = add("fail", cmd_fail)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--reason", required=True)

    node = add("retry", cmd_retry)
    node.add_argument("tree")
    node.add_argument("node")

    node = add("skip", cmd_skip)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--reason", default="")

    node = add("drive", cmd_drive)
    node.add_argument("tree")
    node.add_argument("--json", action="store_true")
    node.add_argument("--dry-run", action="store_true", help="显式只读：确认不落事件（FR-13）")

    node = add("brief", cmd_brief)
    node.add_argument("tree")
    node.add_argument("node")

    node = add("plan", cmd_plan)
    node.add_argument("tree")

    node = add("status", cmd_status)
    node.add_argument("tree", nargs="?")
    node.add_argument("--json", action="store_true")

    node = add("rebuild", cmd_rebuild)
    node.add_argument("--check", action="store_true")

    node = add("board", cmd_board)
    node.add_argument("tree")
    node.add_argument("--stage", default=None)

    node = add("tree", cmd_tree)
    node.add_argument("tree")

    node = add("graph", cmd_graph)
    node.add_argument("tree")
    node.add_argument("--dot", action="store_true")

    node = add("import", cmd_import)
    node.add_argument("what")
    node.add_argument("path")
    node.add_argument("--tree", required=True)

    node = add("context", cmd_context)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--set", required=True)

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
