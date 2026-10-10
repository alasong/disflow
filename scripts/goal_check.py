#!/usr/bin/env python3
"""对 disflow 自己的目标做机检：23 条，每条都是 present/absent 断言 + 一条真跑的命令。

为什么要有这个脚本（而不是在会话里口述一遍）：
上一轮同类检查是**会话内手跑**的，结论（19/21）无法复现、也无法在改动后重放。
`gap-fact-check` 的口径是「『有 X / 缺 X』是可机检的事实声明，不让 LLM 自证」——
所以判据必须落到脚本里，能被第三方重跑。

覆盖面 = `docs/requirements.md`：
  §1.2 病根 8 条（P1–P8，断言在 disflow 里 absent）
  §1.3 保留项 5 条（断言 present）
  §2   目标 5 条（断言 present）
  §2   非目标 5 条（断言 absent）
共 23 条。**注意**：上一轮只机检了 §2 非目标的 3 条（漏了 2 条），此处补全，
所以本轮数字与上轮不可直接比较（23 vs 21）。

判据分两类，脚本自己标出来：
  [机检] 能由一条命令判定 —— 本脚本真跑它
  [判断] 不能机检（语义判断）—— **不冒充通过**，单列在末尾

跑法：`python3 scripts/goal_check.py [--verbose]`
退出码：0 = 机检全过；1 = 有机检不过；2 = 环境不满足（不在 disflow 仓根）
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args: list[str]) -> tuple[int, str]:
    """跑一条命令，返回 (退出码, stdout+stderr)。调用方传完整 argv（不自动拼 git）。"""
    proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


def _grep(pattern: str, *paths: str) -> tuple[int, str]:
    """在给定路径里搜索（用 git grep，尊重 .gitignore）。

    rc 口径（区分开才能不把「工具坏了」读成「没有命中」）：
      0 = 有命中 · 1 = 无命中（**不是错**） · ≥2 = 工具自身失败 → 响亮抛错。
    为什么必须分开：本脚本的头一版把 `git grep` 写成了 `git git grep`，报错文本
    有 4 行，于是「命中 4 处」—— P 类断言因非空而失败、P8 类因行数够而**真空通过**。
    宽错比不报错更坏：它同时扫出假红与假绿。
    """
    rc, out = _run(["git", "grep", "-n", "-E", pattern, "--", *paths])
    if rc >= 2:
        raise RuntimeError(f"git grep 自身失败（rc={rc}）：{out.strip()[:200]}")
    return rc, out


def _py_sources() -> list[str]:
    """disflow 包内所有 .py（不含 tests/scripts）。"""
    out: list[str] = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "disflow")):
        out.extend(os.path.relpath(os.path.join(base, f), ROOT)
                   for f in files if f.endswith(".py"))
    return sorted(out)


# ── 判据表：(编号, 断言方向, 一句话, 检查函数) ────────────────────────────────
# 检查函数返回 (通过?, 证据字符串)。证据必须是**原始观测**（命中行数/原文片段），
# 不是「我确认过了」——本会话已两次栽在「判据自己没牙」上。

def p1_single_source() -> tuple[bool, str]:
    """P1 双源状态：除了事件流，不得有第二份被写的事实源。"""
    code, hits = _grep(r"global_status|_registry_sync|registry\.json", *_py_sources())
    offenders = [ln for ln in hits.splitlines() if ln and "import_mgt.py" not in ln]
    return (not offenders,
            f"global_status/registry.json 出现 {len(hits.splitlines())} 处，"
            f"其中非 import_mgt（适配器只读）的 {len(offenders)} 处：{offenders[:2]}")


def p2_single_machine() -> tuple[bool, str]:
    """P2 双状态机：节点状态只有一个闭集，不并存 global_status。

    探针踩过的坑（记录下来，别再写回错形状）：`STORED_STATUS` 住在 `core.py`、
    且是 `STORED_STATUS: tuple[str, ...] = (...)` —— 带类型标注，故 `^STORED_STATUS` 后跟
    空白再跟等号那条正则一条也匹配不到（第一版就是这么写出假红的）。
    """
    code, hits = _grep(r"^STORED_STATUS", "disflow/core.py")
    code2, hits2 = _grep(r"global_status", *_py_sources())
    outside = [ln for ln in hits2.splitlines() if "import_mgt.py" not in ln]
    return (bool(hits.strip()) and not outside,
            f"core.py 里 STORED_STATUS 定义 {len(hits.splitlines())} 处"
            f"（{hits.splitlines()[0].split('=')[0].strip() if hits.strip() else '无'}）；"
            f"global_status 非适配器处 {len(outside)} 处")


def p3_no_contains_edge() -> tuple[bool, str]:
    """P3 树/图混用：不得有第二套层级表达（contains 边）。"""
    code, hits = _grep(r"\bcontains\b", *(_py_sources()))
    return not hits.strip(), f"'contains' 在 disflow/ 命中 {len(hits.splitlines())} 处"


def p4_no_daemon() -> tuple[bool, str]:
    """P4 多保活 daemon：仓内不得出现常驻轮询循环。"""
    code, hits = _grep(r"while True|daemon|time\.sleep\(", *(_py_sources()))
    return not hits.strip(), f"while True/daemon/sleep 命中 {len(hits.splitlines())} 处：" \
                             f"{[ln.split(':')[0] for ln in hits.splitlines()][:3]}"


def p5_ready_single_impl() -> tuple[bool, str]:
    """P5 就绪判断散落：就绪只允许一处实现。"""
    code, hits = _grep(r"^def ready|^def _ready", *(_py_sources()))
    files = {ln.split(":")[0] for ln in hits.splitlines()}
    return (len(files) <= 1, f"就绪实现分布在 {sorted(files)}（应只有 ready.py）")


def p6_no_handmade_protocol() -> tuple[bool, str]:
    """P6 手搓消息层：不得存在六种目录协议式的脚手目录。"""
    bad = [d for d in ("spawn", "results", "fix", "consult") if os.path.isdir(os.path.join(ROOT, d))]
    return (not bad, f"仓根存在协议目录 {bad}（应为空）")


def p7_stage_role_split() -> tuple[bool, str]:
    """P7 阶段与角色混同：stage 与 role 必须是两个独立字段。"""
    code, hits = _grep(r'"stage".*"role"|"role".*"stage"', *(_py_sources()))
    code2, decl = _grep(r"stage|role", "disflow/model.py")
    both = ("stage" in decl) and ("role" in decl)
    return (both, f"model.py 同时声明 stage 与 role = {both}；同行共现 {len(hits.splitlines())} 处（0 更好）")


def p8_node_level_gate() -> tuple[bool, str]:
    """P8 无节点级统一门：必须有节点级证据契约（且真的被 done 调用）。

    口径不是「函数名带 gate」：disflow 的门是**能力函数**（跑验收 / 查作用域 /
    取指纹 / 判真空），命名不带 ``gate_`` 前缀。第一版按 ``^def gate_`` 数，
    得 0 个 → 假红。改成数「证据契约四件事」并验证调用点。
    """
    primitives = ("run_acceptance", "scope_check", "fingerprint", "vacuous_reasons")
    pattern = "^def (" + "|".join(primitives) + ")"
    code, hits = _grep(pattern, "disflow/gates.py")
    found = {m.group(1) for ln in hits.splitlines()
             if (m := re.search(r":def (\w+)\(", ln))}
    code2, used = _grep(r"gates\.", "disflow/engine.py")
    return (len(found) == len(primitives) and bool(used.strip()),
            f"gates.py 里证据契约函数 {len(found)}/{len(primitives)} 个 {sorted(found)}；"
            f"engine.py 调用 gates.* {len(used.splitlines())} 处")


def k_worktree_isolation() -> tuple[bool, str]:
    """§1.3 保留项：worktree 单写者收口。"""
    ok = os.path.isfile(os.path.join(ROOT, "disflow/worktree.py"))
    code, hits = _grep(r"^def path_for", "disflow/worktree.py")
    return (ok and bool(hits.strip()), f"worktree.py 存在={ok}；path_for 定义 {len(hits.splitlines())} 处")


def k_dep_wave_cycle() -> tuple[bool, str]:
    """§1.3 保留项：依赖波次 + 环检测。"""
    code, cycle = _grep(r"cycle|环", "disflow/graph.py")
    code2, wave = _grep(r'"wave"|wave', "disflow/ready.py", "disflow/engine.py")
    return (bool(cycle.strip()) and bool(wave.strip()),
            f"graph.py 环检测字样 {len(cycle.splitlines())} 处；ready/engine 波次 {len(wave.splitlines())} 处")


def k_quantify() -> tuple[bool, str]:
    """§1.3 保留项：量化（points / velocity）。"""
    ok = os.path.isfile(os.path.join(ROOT, "disflow/metrics.py"))
    code, hits = _grep(r"points_unestimated|velocity", "disflow/metrics.py")
    return (ok and bool(hits.strip()), f"metrics.py 存在={ok}；points_unestimated/velocity {len(hits.splitlines())} 处")


def k_verify_separate() -> tuple[bool, str]:
    """§1.3 保留项：验证与开发分离（验收判据是可跑命令，不是自述）。"""
    code, hits = _grep(r"verify_cmd|\"acceptance\"", *(_py_sources()))
    return bool(hits.strip()), f"verify_cmd/acceptance 字样 {len(hits.splitlines())} 处"


def k_iteration_burndown() -> tuple[bool, str]:
    """§1.3 保留项：迭代 / 燃尽。"""
    code, hits = _grep(r"^def burndown", "disflow/metrics.py")
    code2, dl = _grep(r"deadline", "disflow/spec.py", "disflow/state.py", "disflow/engine.py")
    return (bool(hits.strip()) and bool(dl.strip()),
            f"burndown 定义 {len(hits.splitlines())} 处；deadline 在 spec/state/engine {len(dl.splitlines())} 处")


def g1_single_fact_source() -> tuple[bool, str]:
    """§2 目标 1：单一事实源的决定论引擎。"""
    code, hits = _grep(r"events\.jsonl|append_jsonl", *(_py_sources()))
    return bool(hits.strip()), f"事件流作为唯一写入源：命中 {len(hits.splitlines())} 处"


def g2_tree_graph_split() -> tuple[bool, str]:
    """§2 目标 2：树与图职责不重叠（parent 管层级、requires 管调度）。"""
    code, parent = _grep(r'"parent"', "disflow/model.py")
    code2, req = _grep(r'"requires"', "disflow/model.py")
    return (bool(parent.strip()) and bool(req.strip()),
            f"model.py 里 parent {len(parent.splitlines())} 处、requires {len(req.splitlines())} 处")


def g3_push_only() -> tuple[bool, str]:
    """§2 目标 3：只推不跑——引擎不得 spawn 子进程执行任务。"""
    code, hits = _grep(r"subprocess|Popen|os\.system", "disflow/engine.py", "disflow/dispatch.py", "disflow/ready.py")
    return not hits.strip(), f"engine/dispatch/ready 里 subprocess 命中 {len(hits.splitlines())} 处（应为 0）"


def g4_board_is_view() -> tuple[bool, str]:
    """§2 目标 4：看板是视图不是第二存储；多板 = 多棵隔离的树。"""
    code, hits = _grep(r"^def board|^def render", "disflow/views.py")
    code2, store = _grep(r"write_json|append_jsonl", "disflow/views.py")
    return (bool(hits.strip()) and not store.strip(),
            f"views.py 渲染函数 {len(hits.splitlines())} 个；其中写盘 {len(store.splitlines())} 处（应为 0）")


def g5_mgt_migration() -> tuple[bool, str]:
    """§2 目标 5：从 mgt 可迁移。"""
    ok = os.path.isfile(os.path.join(ROOT, "disflow/import_mgt.py"))
    code, hits = _grep(r"def build_import|def apply_import", "disflow/import_mgt.py")
    return (ok and bool(hits.strip()), f"import_mgt.py 存在={ok}；导入函数 {len(hits.splitlines())} 个")


def n1_no_web_db_deps() -> tuple[bool, str]:
    """§2 非目标：不做 Web/DB，零第三方依赖。"""
    code, out = _run([sys.executable, "-c", """
import ast, os, sys
stdlib = set(sys.stdlib_module_names)
local = {"disflow"}
bad = set()
for base, _d, files in os.walk("disflow"):
    for f in files:
        if not f.endswith(".py"): continue
        t = ast.parse(open(os.path.join(base, f), encoding="utf-8").read())
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                bad |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                bad.add(n.module.split(".")[0])
bad -= stdlib | local | {"__future__"}
print(" ".join(sorted(bad)))
"""])
    if code != 0:
        raise RuntimeError(f"AST 扫描自身失败（rc={code}）：{out.strip()[:200]}")
    bad = out.strip()
    return (not bad, f"disflow/ 的非标准库导入：{bad or '（无）'}")


def n2_no_scheduler_daemon() -> tuple[bool, str]:
    """§2 非目标：不做常驻调度 daemon。"""
    code, hits = _grep(r"daemon|threading|multiprocessing", *(_py_sources()))
    return not hits.strip(), f"daemon/threading/multiprocessing 命中 {len(hits.splitlines())} 处"


def n3_no_llm_in_decisions() -> tuple[bool, str]:
    """§2 非目标：不让 LLM 参与确定性判定（就绪/依赖/波次）。"""
    code, hits = _grep(r"openai|anthropic|llm|prompt|completion", "disflow/ready.py", "disflow/graph.py", "disflow/state.py")
    return not hits.strip(), f"ready/graph/state 里 LLM 相关命中 {len(hits.splitlines())} 处"


def n5_no_distributed() -> tuple[bool, str]:
    """§2 非目标：不做多机分布式调度。"""
    code, hits = _grep(r"socket|http\.client|urllib|requests", *(_py_sources()))
    return not hits.strip(), f"网络相关导入/调用命中 {len(hits.splitlines())} 处"


Check = Callable[[], tuple[bool, str]]

CHECKS: list[tuple[str, str, Check]] = [
    ("P1", "双源状态", p1_single_source),
    ("P2", "双状态机", p2_single_machine),
    ("P3", "树/图混用（contains 边）", p3_no_contains_edge),
    ("P4", "多保活 daemon", p4_no_daemon),
    ("P5", "就绪判断散落", p5_ready_single_impl),
    ("P6", "手搓消息层", p6_no_handmade_protocol),
    ("P7", "阶段与角色混同", p7_stage_role_split),
    ("P8", "无节点级统一门", p8_node_level_gate),
    ("保留1", "worktree 单写者收口", k_worktree_isolation),
    ("保留2", "依赖波次 + 环检测", k_dep_wave_cycle),
    ("保留3", "量化 points/velocity", k_quantify),
    ("保留4", "验证与开发分离", k_verify_separate),
    ("保留5", "迭代 / 燃尽", k_iteration_burndown),
    ("目标1", "单一事实源的决定论引擎", g1_single_fact_source),
    ("目标2", "树与图职责不重叠", g2_tree_graph_split),
    ("目标3", "只推不跑、无常驻", g3_push_only),
    ("目标4", "看板是视图、多板隔离", g4_board_is_view),
    ("目标5", "从 mgt 可迁移", g5_mgt_migration),
    ("非目标1", "不做 Web/DB、零第三方依赖", n1_no_web_db_deps),
    ("非目标2", "不做常驻调度 daemon", n2_no_scheduler_daemon),
    ("非目标3", "不让 LLM 参与确定性判定", n3_no_llm_in_decisions),
    ("非目标5", "不做多机分布式调度", n5_no_distributed),
]

# 不能机检的条目**不冒充通过**，单列。写清为什么。
JUDGMENTS = [
    ("非目标4", "不追求通用工作流引擎（不替代 Airflow/Temporal）",
     "这是**语义判断**：没有一条命令能判定「够不够通用」。可观察的相关事实是"
     "「核心层零业务词表」（tests/test_layering.py::test_core_has_no_business_words），"
     "但那只是必要条件，不是这条的判断。"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="对 disflow 自己的目标做机检（23 条）")
    parser.add_argument("--verbose", action="store_true", help="打印每条的原始观测")
    args = parser.parse_args(argv)

    if not os.path.isdir(os.path.join(ROOT, "disflow")):
        print("✗ 不在 disflow 仓根：找不到 disflow/", file=sys.stderr)
        return 2

    failed: list[str] = []
    for cid, title, fn in CHECKS:
        try:
            ok, evidence = fn()
        except Exception as exc:                      # 判据自身炸了 = 不通过，不静默
            ok, evidence = False, f"判据自身异常：{type(exc).__name__}: {exc}"
        print(f"{'PASS' if ok else '**FAIL**'}  [{cid}] {title}")
        if args.verbose or not ok:
            print(f"        {evidence}")
        if not ok:
            failed.append(f"[{cid}] {title}")

    print("\n[判断]（不能机检，不计入通过数，也不冒充通过）")
    for cid, title, why in JUDGMENTS:
        print(f"  [{cid}] {title}")
        print(f"        {why}")

    total = len(CHECKS)
    print(f"\n机检 {total} 条 · 通过 {total - len(failed)} · 失败 {len(failed)}"
          f" · 另有 {len(JUDGMENTS)} 条为语义判断不计入")
    for name in failed:
        print("  FAIL:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
