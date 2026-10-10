#!/usr/bin/env python3
"""变异测试：验证测试套件**真的有牙**（不是摆设）。

做法：对实现代码逐个注入「回到已知错误样子」的变异，跑对应测试——
**注入后必须失败、还原后必须通过**。吞掉变异（注入后测试仍全绿）说明那处行为没被测到。

为什么需要它（不是洁癖，是实测）：
本仓已两次由变异测试抓出**测试自身的洞**——
`_link_relates` 整个被跳过却无人喊（因为断言只看了 `new_node` 那条路径），
`diff_size` 的 `per_file` 与 `total` 不一致也无人喊（因为只断言了 `total`）。
只跑「测试全绿」是看不出这些的。

用法：
    python3 scripts/mutation_check.py            # 全跑；有漏抓则退出码 1
    python3 scripts/mutation_check.py -k rescope # 只跑名字含 rescope 的
    python3 scripts/mutation_check.py --list
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 每条：(名字, 文件, 原样, 换成, 要跑哪个测试文件)
Mutation = tuple[str, str, str, str, str]

MUTATIONS: list[Mutation] = [
    # ── 被拒的事件不许落盘（store 先试算后 append）────────────────────────
    ("store 改回「先落盘再试算」", "disflow/store.py",
     "    state = stamp_projection(project(candidate), len(candidate))\n"
     "    append_jsonl(events_path(spec), record)",
     "    append_jsonl(events_path(spec), record)\n"
     "    state = stamp_projection(project(candidate), len(candidate))",
     "test_store.py"),

    # ── 改动归因：路径差集 vs 内容指纹 ───────────────────────────────────
    ("归因退回「按路径做差集」", "disflow/gates.py",
     "        if fingerprint(cwd, path) != base.get(path):",
     "        if path not in base:",
     "test_engine.py"),

    # ── diff_size 的读数分支 ─────────────────────────────────────────────
    ("diff_size 不查 HEAD（暂存改动不可见）", "disflow/gates.py",
     '    head = _numstat(cwd, ["HEAD"], paths)\n'
     "    if head is not None:\n        return head",
     "    head = None\n"
     "    if head is not None:\n        return head",
     "test_gates.py"),
    ("diff_size 不区分已跟踪文件（一律数整文件）", "disflow/gates.py",
     "        if _is_tracked(cwd, path):",
     "        if False:",
     "test_gates.py"),
    ("diff_size 兜底重复计数", "disflow/gates.py",
     "        if path in per_file:\n            continue",
     "        pass",
     "test_gates.py"),

    # ── acceptance.expect 三分支 ─────────────────────────────────────────
    ("contains 不再要求 exit0", "disflow/gates.py",
     "        ok = returncode == 0 and needle in output",
     "        ok = needle in output",
     "test_gates.py"),
    ("regex 不再要求 exit0", "disflow/gates.py",
     "        ok = returncode == 0 and re.search(pattern, output) is not None",
     "        ok = re.search(pattern, output) is not None",
     "test_gates.py"),
    ("非法 expect 静默通过", "disflow/gates.py",
     '    raise ValueError(f"acceptance.expect 非法：{expect!r}'
     '（支持 exit0 | contains:… | regex:…）")',
     '    return False, f"未知 expect：{expect}", {}',
     "test_gates.py"),
    ("验收只看 stdout（丢掉 stderr）", "disflow/gates.py",
     '    output = (proc.stdout or "") + (proc.stderr or "")',
     '    output = proc.stdout or ""',
     "test_gates.py"),

    # ── 写面声明：rescope 与破例 ─────────────────────────────────────────
    ("rescope 不再拦「已碰过的路径」", "disflow/engine.py",
     "            dirty = sorted(set(added) & touched)",
     "            dirty = []",
     "test_rescope.py"),
    ("scope_waiver 逃生口被拆掉", "disflow/engine.py",
     '            if scope["checked"] and scope["violations"] and \\\n'
     '                    not (declarations or {}).get("scope_waiver"):',
     '            if scope["checked"] and scope["violations"]:',
     "test_rescope.py"),
    ("破例不再记入证据", "disflow/engine.py",
     "                if waiver:\n                    evidence[key] = waiver",
     "                if waiver:\n                    _ = waiver",
     "test_rescope.py"),

    # ── 边的增删 ────────────────────────────────────────────────────────
    ("unrequire 不校验边是否存在", "disflow/engine.py",
     '            if dep not in current:\n'
     '                raise EngineError(f"{node} 没有 requires {dep}（现有：{current}）")',
     '            if False:\n                raise EngineError("x")',
     "test_edges.py"),
    ("unrequire 要求 dep 节点存在（破坏故意的不对称）", "disflow/engine.py",
     '            item = self._need(t, node)\n'
     '            current = list(item.get("requires") or [])',
     '            item = self._need(t, node)\n'
     '            self._need(t, dep)\n'
     '            current = list(item.get("requires") or [])',
     "test_edges.py"),

    # ── expand 声明的边字段 ─────────────────────────────────────────────
    ("expand 不接 requires（静默丢弃）", "disflow/state.py",
     "    _link_requires(tree, pending)", "    _ = pending", "test_edges.py"),
    ("expand 不接 relates", "disflow/state.py",
     "    _link_relates(tree, pending)", "    pass", "test_edges.py"),
    ("expand 不校验同批成环", "disflow/state.py",
     '            if graph.would_cycle(tree["nodes"], node_id, dep):\n'
     '                raise ValueError(f"加边即成环：{node_id} requires {dep}")',
     '            if False:\n                raise ValueError("x")',
     "test_edges.py"),

    # ── 声明与实现的一致性（本轮审查抳出的三个“两套清单”）──────────────
    ("声明可给的字段集与 new_node 参数错开", "disflow/model.py",
     'RUNTIME_FIELDS: frozenset[str] = frozenset({"parent", "status", "lease", "evidence"})',
     'RUNTIME_FIELDS: frozenset[str] = frozenset({"status", "lease", "evidence"})',
     "test_declarations.py"),
    ("运行时字段不再拒收（回到静默丢弃）", "disflow/model.py",
     '    runtime = set(spec) & RUNTIME_FIELDS\n    if runtime:',
     '    runtime = set()\n    if runtime:',
     "test_declarations.py"),
    ("事件闭集与派发表漂移", "disflow/state.py",
     '    "node.reclaim": _on_node_reclaim,',
     '    "node.reclaim": _on_node_reclaim,\n    "node.不存在": _on_node_reclaim,',
     "test_declarations.py"),
    ("报错文本退回可漂移的 EVENT_KINDS", "disflow/state.py",
     '        raise ValueError(f"未知事件 kind：{kind!r}（可派发：{sorted(_HANDLERS)}）")',
     '        raise ValueError(f"未知事件 kind：{kind!r}（闭集：{sorted(_HANDLERS)}）")',
     "test_declarations.py"),
    ("fcntl 恢复顶层导入（非 POSIX 上整包不可导入）", "disflow/store.py",
     'import contextlib\nimport json',
     'import contextlib\nimport fcntl\nimport json',
     "test_declarations.py"),

    # ── 空洞通过：记录与报出 ────────────────────────────────────────────
    ("空洞检测失效（永远返回空）", "disflow/gates.py",
     '    reasons: dict[str, str] = {}\n'
     '    if str((acceptance or {}).get("waiver") or "").strip():',
     '    reasons: dict[str, str] = {}\n'
     '    if False:',
     "test_vacuity.py"),
    ("有写面也标空洞（误报）", "disflow/gates.py",
     '    if not touch_plan:\n'
     '        reasons["design_first"] = "touch_plan 为空：无写面可检查"',
     '    if True:\n'
     '        reasons["design_first"] = "touch_plan 为空：无写面可检查"',
     "test_vacuity.py"),
    ("done 不把空洞写进证据", "disflow/engine.py",
     '            evidence["vacuous"] = gates.vacuous_reasons(\n'
     '                acceptance=item.get("acceptance"), '
     'touch_plan=item.get("touch_plan") or [],\n'
     '                introduced=introduced, size=size)',
     '            evidence["vacuous"] = {}',
     "test_vacuity.py"),
    ("done 不再报出空洞", "disflow/cli.py",
     '    if vacuous:\n        print(_vacuous_line("门", vacuous))',
     '    if False:\n        print(_vacuous_line("门", vacuous))',
     "test_vacuity.py"),
    ("close 不再汇总空洞", "disflow/cli.py",
     '    _report_tree_vacuity(engine.tree(args.tree))',
     '    pass',
     "test_vacuity.py"),

    # ── CLI 装配层：旗标接线与报文通道 ───────────────────────────────────
    #   ⚠️ pattern 是**声明的覆盖来源**，必须指准：指向不测该行为的文件
    #     会伪造出「漏抓」（实测：close 的 --dry-run 被 test_cli.py 守住，
    #     而 test_dryrun.py 只调引擎、不接 CLI，指向它就会报假漏抓）。
    ("close 漏传 --dry-run（曾是真缺陷）", "disflow/cli.py",
     "engine.close(args.tree, dry_run=dry)", "engine.close(args.tree)",
     "test_cli.py"),
    ("门拒收报文改回 stdout", "disflow/cli.py",
     'print(f"✗ {\'预演会被拒\' if dry else \'门拒收\'} {args.node}：", file=sys.stderr)',
     'print(f"✗ {\'预演会被拒\' if dry else \'门拒收\'} {args.node}：")',
     "test_cli_lifecycle.py"),
]


def _run_tests(pattern: str) -> int:
    for cache in ROOT.rglob("__pycache__"):
        # 清字节码：陈旧 .pyc 会静默跑旧代码，会让变异结果错得看不出来（实测踩过）
        try:
            shutil.rmtree(cache)
        except OSError:
            continue                     # 清不掉就跳过这一处，不中断整轮
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests",
         "-p", pattern],
        cwd=ROOT, capture_output=True, text=True).returncode


def _check(mutation: Mutation) -> tuple[bool, int, int]:
    """返回 (是否抓到, 注入后退出码, 还原后退出码)。无论如何都会还原。"""
    name, relpath, old, new, pattern = mutation
    path = ROOT / relpath
    original = path.read_text(encoding="utf-8")
    if old not in original:
        raise SystemExit(f"针脚找不到（代码变了？）：{name}\n  文件：{relpath}")
    try:
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        injected = _run_tests(pattern)
    finally:
        path.write_text(original, encoding="utf-8")
    restored = _run_tests(pattern)
    return (injected != 0 and restored == 0), injected, restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="变异测试：验证测试套件有牙")
    parser.add_argument("-k", default="", help="只跑名字含该子串的变异")
    parser.add_argument("--list", action="store_true", help="只列变异，不执行")
    args = parser.parse_args(argv)

    selected = [(name, *rest) for name, *rest in
                ((m[0], m) for m in MUTATIONS) if args.k in name]
    if args.list:
        for name, _m in selected:
            print(name)
        return 0

    bad: list[str] = []
    for name, mutation in selected:
        try:
            caught, injected, restored = _check(mutation)
        except SystemExit as exc:
            print(f"  ⚠️  {name}：{exc}")
            bad.append(name)
            continue
        print(f"  {'✅' if caught else '❌'} {name}"
              f"（注入 exit={injected} / 还原 exit={restored}）")
        if not caught:
            bad.append(name)

    print(f"\n捕获率：{len(selected) - len(bad)}/{len(selected)}")
    if bad:
        print("漏抓（这些行为没有被测试守住）：")
        for name in bad:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
