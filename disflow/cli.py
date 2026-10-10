"""disflow · 命令行（只装配，不含业务逻辑）。

所有业务在 `engine` / `dispatch` / `gates`；本模块只做参数解析与打印。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import graph, store, views
from .engine import Engine, EngineError, GateReject
from .import_mgt import apply_import, build_import, load_mgt
from .state import project


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
    engine.expand(args.tree, args.parent, _loads(args.children))
    print(f"✓ {args.parent} 展开子节点")
    return 0


def cmd_require(engine: Engine, args) -> int:
    engine.require(args.tree, args.node, args.dep)
    print(f"✓ {args.node} requires {args.dep}")
    return 0


def cmd_relate(engine: Engine, args) -> int:
    engine.relate(args.tree, args.node, args.other)
    print(f"✓ {args.node} relates {args.other}")
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
    engine.start(args.tree, args.node, args.holder or "")
    print(f"✓ {args.node} 开工（holder={args.holder or 'unknown'}）")
    return 0


def cmd_collect(engine: Engine, args) -> int:
    engine.collect(args.tree, args.node, _loads(args.result))
    print(f"✓ {args.node} 结果已校验落盘（未写 done）")
    return 0


def cmd_done(engine: Engine, args) -> int:
    declarations = _loads(args.decl) if args.decl else None
    try:
        engine.done(args.tree, args.node, declarations)
    except GateReject as reject:
        print(f"✗ 门拒收 {args.node}：")
        for item in reject.missing:
            print(f"  - {item}")
        print("契约片段：")
        _dump(reject.contract)
        return 2
    print(f"✓ {args.node} done（证据已自算落盘）")
    return 0


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
              f" · 卡死 {'是' if status['stuck'] else '否'}")
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


def cmd_doctor(engine: Engine, args) -> int:
    problems: list[str] = []
    matched, diffs = store.projection_matches(engine.spec, project=project)
    if not matched:
        problems.extend(f"投影漂移：{d}" for d in diffs)
    state = engine.load()
    for name, tree in sorted(state.get("trees", {}).items()):
        cycles = graph.find_cycles(tree["nodes"])
        if cycles:
            problems.append(f"{name}: requires 成环 {cycles}")
        for node_id, node in tree["nodes"].items():
            for dep in node.get("requires") or []:
                if dep not in tree["nodes"]:
                    problems.append(f"{name}/{node_id}: requires 悬空 {dep}")
            for consumed in node.get("consumes") or []:
                if consumed not in tree["assets"]:
                    problems.append(f"{name}/{node_id}: consumes 未登记 {consumed}")
    if problems:
        print("✗ doctor 发现：")
        for item in problems:
            print(f"  - {item}")
        return 1
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
        print(f"不支持导入源：{args.what}（当前仅 mgt）", file=sys.stderr)
        return 1
    mgt = load_mgt(args.path)
    if not mgt["todos"]:
        print(f"未找到 mgt 状态：{args.path}/.fat/**/state.json", file=sys.stderr)
        return 1
    stats = apply_import(engine, args.tree, build_import(mgt))
    _dump(stats)
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

    node = add("require", cmd_require)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("dep")

    node = add("relate", cmd_relate)
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

    node = add("collect", cmd_collect)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--result", required=True)

    node = add("done", cmd_done)
    node.add_argument("tree")
    node.add_argument("node")
    node.add_argument("--decl", default="")

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

    add("doctor", cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = Engine()
    try:
        return args.handler(engine, args)
    except EngineError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
