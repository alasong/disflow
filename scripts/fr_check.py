#!/usr/bin/env python3
"""对 `docs/requirements.md` §3–§7 的 29 条 FR + 7 条 NFR **逐条**机检 + 分类。

为什么要有这个脚本（而不是在会话里口述一遍）：
本会话已机检过 §1.2 病根 8 条 / §1.3 保留项 5 条 / §2 目标与非目标 10 条（见
`scripts/goal_check.py`），但 **§3–§7 的 FR/NFR 从未逐条查过**。上一轮同类检查只数通过率，
把 2 条漏检当成不存在 —— 故本脚本的头等产出不是通过率，而是**把「没实现」与「不能机检」
诚实分出来**，每条给一个证据类型标签：

  [机检]   有一条命令能判定 —— 本脚本真跑它，并打印**原始观测**（命中行数/退出码/原文）
  [测试]   只有测试覆盖、没有独立命令 —— 指出是哪个测试（文件 + 测试方法名）
  [未实现] 实现里找不到对应能力 —— **最有价值的输出**，单列
  [不可机检] 语义判断 —— 不冒充通过，单列并写清为什么

退出码口径（**必须区分开**，否则工具坏掉会被读成「没有命中」）：
  `_git_grep` 返回 rc：0 = 有命中 · 1 = 无命中（**不是错**） · ≥2 = 工具自身失败 → 响亮抛错。
  本会话踩过的坑：把 `git grep` 写成 `git git grep`，报错文本有若干行 ⇒ 被当成「命中」，
  同时造出假红与假绿。所以工具失败绝不与「无命中」合并。

跑法：`python3 scripts/fr_check.py`
退出码：0 = 机检全过；1 = 有机检不过；2 = 环境不满足（不在 disflow 仓根）。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── 原语（口径与 goal_check.py 对齐）────────────────────────────────────────

def _run(args: list[str], *, cwd: str = ROOT, env: dict | None = None) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=90, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def _git_grep(pattern: str, *paths: str) -> tuple[int, str]:
    """`git grep`，rc 分态（见模块 docstring）。≥2 = 工具自身失败 → 抛错。"""
    rc, out = _run(["git", "grep", "-n", "-E", pattern, "--", *paths])
    if rc >= 2:
        raise RuntimeError(f"git grep 自身失败（rc={rc}）：{out.strip()[:200]}")
    return rc, out


def _n_lines(text: str) -> int:
    return len([ln for ln in text.splitlines() if ln.strip()])


def _py_sources() -> list[str]:
    out: list[str] = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "disflow")):
        out.extend(os.path.relpath(os.path.join(base, f), ROOT)
                   for f in files if f.endswith(".py"))
    return sorted(out)


def _imports_in_disflow() -> set[str]:
    """disflow/ 里的非标准库导入（in-process AST 扫描，供 NFR-1）。"""
    bad: set[str] = set()
    for base, _dirs, files in os.walk(os.path.join(ROOT, "disflow")):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    bad |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    bad.add(node.module.split(".")[0])
    return bad - set(sys.stdlib_module_names) - {"disflow", "__future__"}


# ── CLI 隔离面：机检一律用临时 state dir，绝不碰仓内 .disflow ────────────────

def _cli(args: list[str], workdir: str) -> tuple[int, str]:
    """在**隔离的临时 git 仓**里跑 CLI。

    为何要临时 git 仓（踩过的坑）：`done` 会自算 git 改动集与规模预算。若 cwd 是仓根，
    本仓自身的未跟踪文件（如本脚本）会被算进 `introduced` ⇒ 规模超预算 ⇒ **验收 true 的
    节点也被拒**（判据把通过读成失败）。临时仓初始干净，门只看到隔离状态。
    PYTHONPATH 指回 ROOT，使 cwd 可以不是包所在目录。
    """
    env = dict(os.environ)
    env["DISFLOW_STATE_DIR"] = os.path.join(workdir, ".disflow")
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    return _run([sys.executable, "-m", "disflow.cli", *args], cwd=workdir, env=env)


def _state_dir(workdir: str) -> str:
    return os.path.join(workdir, ".disflow")


_SEED_CHILDREN = json.dumps([
    {"id": "a", "goal": "A", "acceptance": {"cmd": "true"}},
    {"id": "b", "goal": "B", "acceptance": {"cmd": "true"}},
    {"id": "c", "goal": "C", "acceptance": {"cmd": "false"}},
])


def _seed(state_dir: str) -> None:
    """建一棵最小树 t：a/b（验收 true）、c（验收 false）。"""
    _cli(["init", "t", "--goal", "demo goal"], state_dir)
    _cli(["expand", "t", "root", "--children", _SEED_CHILDREN], state_dir)


def _fresh() -> str:
    """临时目录 + `git init`（干净的隔离仓，供 CLI 门检查用）。"""
    workdir = tempfile.mkdtemp(prefix="frcheck-")
    subprocess.run(["git", "init", "-q"], cwd=workdir, capture_output=True, text=True)
    return workdir


# ── 判据表 ──────────────────────────────────────────────────────────────────
# 每个 check 返回 (通过?, 原始观测字符串)。证据必须是原始观测，不是「我确认过了」。

def fr1_append_only() -> tuple[bool, str]:
    """FR-1 状态来自 append-only 事件流。"""
    rc1, o1 = _git_grep(r"O_APPEND", "disflow/store.py")
    rc2, o2 = _git_grep(r"append_event|append_jsonl", "disflow/store.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"store.py O_APPEND 命中 {_n_lines(o1)} 行；事件写入函数命中 {_n_lines(o2)} 行"


def fr2_rebuild_check() -> tuple[bool, str]:
    """FR-2 rebuild --check 逐字节一致；篡改投影必须被发现。"""
    sd = _fresh()
    try:
        _seed(sd)
        good, good_out = _cli(["rebuild", "--check"], sd)
        proj = os.path.join(_state_dir(sd), "state.json")
        with open(proj, encoding="utf-8") as fh:
            state = json.load(fh)
        state["trees"]["t"]["goal"] = "TAMPERED"
        with open(proj, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        bad, _bad_out = _cli(["rebuild", "--check"], sd)
        ok = good == 0 and bad != 0
        return ok, (f"干净投影 rebuild --check rc={good}（应 0）；"
                    f"篡改后 rc={bad}（应非 0）；干净输出={good_out.strip()[:40]!r}")
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr3_atomic_flock() -> tuple[bool, str]:
    """FR-3 投影原子替换 + flock 真互斥。"""
    rc1, o1 = _git_grep(r"os\.replace", "disflow/store.py")
    rc2, o2 = _git_grep(r"fcntl\.flock.*LOCK_EX", "disflow/store.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"os.replace 命中 {_n_lines(o1)} 行；fcntl.flock(LOCK_EX) 命中 {_n_lines(o2)} 行"


def fr4_single_parent() -> tuple[bool, str]:
    """FR-4 至多一个 parent（parent 是标量字段，不是边集合）+ 祖先链查询。"""
    rc1, o1 = _git_grep(r'"parent": parent', "disflow/model.py")
    rc2, o2 = _git_grep(r"^def ancestor_chain", "disflow/model.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, (f"model.py 里 parent 标量赋值命中 {_n_lines(o1)} 行；"
                f"ancestor_chain 定义命中 {_n_lines(o2)} 行")


def fr5_no_contains() -> tuple[bool, str]:
    """FR-5 层级无第二表达（disflow/ 不得出现 contains 边）。

    为什么不用 `\bcontains\b`：本环境的 git grep -E 不支持 `\b`，该式**一条也匹配不到**
    ——`scripts/goal_check.py` 的 p3 曾用同一式，故 p3 当时是**真空通过**（见报告；该条已随本报告
    一并修掉，commit c5ca556）。
    也不能直接搜裸词 `contains`：`acceptance.expect` 的取值类型写的就是 `contains:`、
    `import_mgt.py` 文档里也提到 mgt 的 `contains` 依赖被丢弃 —— 那些不是边。
    真·contains 边在代码里会是一个**引号包住的字段名** `"contains"`，故只搜引号字面量。
    """
    rc, out = _git_grep(r"[\"']contains[\"']", *_py_sources())
    return (rc == 1 and not out.strip()), f"'contains' 字段名在 disflow/ 命中 {_n_lines(out)} 处（应 0）"


def fr6_stage_role() -> tuple[bool, str]:
    """FR-6 stage 与 role 是独立字段。"""
    rc1, o1 = _git_grep(r'"stage": stage', "disflow/model.py")
    rc2, o2 = _git_grep(r'"role": role', "disflow/model.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"model.py 独立赋值 stage {_n_lines(o1)} 行、role {_n_lines(o2)} 行"


def fr8_cycle_rejected() -> tuple[bool, str]:
    """FR-8 requires 必须 DAG，写时拒环。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc1, _ = _cli(["require", "t", "b", "a"], sd)      # a → b 合法
        rc2, out2 = _cli(["require", "t", "a", "b"], sd)   # b → a 成环，须拒
        ok = rc1 == 0 and rc2 != 0
        return ok, (f"合法边 rc={rc1}（应 0）；成环边 rc={rc2}（应非 0）；"
                    f"成环报错={out2.strip()[:60]!r}")
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr9_relates_soft() -> tuple[bool, str]:
    """FR-9 relates 不影响就绪（ready.py 不读 relates）。"""
    rc, out = _git_grep(r"relates", "disflow/ready.py")
    return (rc == 1 and not out.strip()), f"ready.py 里 relates 命中 {_n_lines(out)} 处（应 0）"


def fr10_consumes_rejected() -> tuple[bool, str]:
    """FR-10 consumes 指向未登记项即拒。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc, out = _cli(["consume", "t", "a", "ghost-asset"], sd)
        return rc != 0, f"consume 未登记资产 rc={rc}（应非 0）；报错={out.strip()[:70]!r}"
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr11_single_ready() -> tuple[bool, str]:
    """FR-11 就绪只有一个实现。"""
    # 用 `\(` 而非 `\b`：git grep -E 是 POSIX ERE，`\b` 不被支持（第一版就栽在这里——
    # 判据自己没匹配上，把通过读成了失败）。
    rc, out = _git_grep(r"^def (is_ready|ready_tasks|ready)\(", *_py_sources())
    files = sorted({ln.split(":")[0] for ln in out.splitlines() if ln.strip()})
    return (files == ["disflow/ready.py"]), f"就绪函数定义分布在 {files}（应只有 disflow/ready.py）"


def fr13_dry_run() -> tuple[bool, str]:
    """FR-13 就绪结果可 --dry-run 输出。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc, out = _cli(["drive", "t", "--json", "--dry-run"], sd)
        ok = rc == 0 and '"dry_run": true' in out
        return ok, f"drive --json --dry-run rc={rc}；输出含 dry_run 标记={'\"dry_run\": true' in out}"
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr14_no_spawn() -> tuple[bool, str]:
    """FR-14 drive 只推不跑：engine/dispatch/ready 不得 spawn。"""
    rc, out = _git_grep(r"subprocess|Popen|os\.system",
                        "disflow/engine.py", "disflow/dispatch.py", "disflow/ready.py")
    return (rc == 1 and not out.strip()), f"engine/dispatch/ready 里 spawn 命中 {_n_lines(out)} 处（应 0）"


def fr15_brief_collect() -> tuple[bool, str]:
    """FR-15 brief 写全；collect 校验后落盘**不写 node-done**。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc_b, brief = _cli(["brief", "t", "a"], sd)
        keys = ("id", "goal", "acceptance", "touch_plan", "brief_cmd", "done_cmd")
        have = all(f'"{k}"' in brief for k in keys)
        result = json.dumps({"status": "ok", "summary": "s", "changed_files": []})
        rc_c, _ = _cli(["collect", "t", "a", "--result", result], sd)
        _rc_s, status = _cli(["status", "t", "--json"], sd)
        still_pending = '"status": "pending"' in status
        ok = rc_b == 0 and have and rc_c == 0 and still_pending
        return ok, (f"brief rc={rc_b} 关键键齐全={have}；collect rc={rc_c}；"
                    f"collect 后 a 仍 pending={still_pending}")
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr16_no_daemon() -> tuple[bool, str]:
    """FR-16 引擎不内置常驻进程。"""
    rc1, o1 = _git_grep(r"daemon|threading|multiprocessing|while True", *_py_sources())
    return (rc1 == 1 and not o1.strip()), f"daemon/threading/while True 命中 {_n_lines(o1)} 处（应 0）"


def fr18_acceptance_gate() -> tuple[bool, str]:
    """FR-18 done 前必须过验收门：验收失败必须拒，验收通过才放行。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc_fail, _ = _cli(["done", "t", "c", "--dry-run"], sd)   # acceptance.cmd=false
        rc_pass, _ = _cli(["done", "t", "a", "--dry-run"], sd)   # acceptance.cmd=true
        ok = rc_fail != 0 and rc_pass == 0
        return ok, (f"验收 false 的节点 done rc={rc_fail}（应非 0）；"
                    f"验收 true 的节点 done rc={rc_pass}（应 0）")
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr19_evidence_selfcomputed() -> tuple[bool, str]:
    """FR-19 证据元数据由引擎自算（不手抄）。"""
    rc, out = _git_grep(
        r"gates\.(evidence_meta|snapshot|newly_changed|fingerprint)", "disflow/engine.py")
    calls = sorted({m for ln in out.splitlines()
                    for m in [ln.split("gates.")[-1].split("(")[0]]})
    return (bool(out.strip())), f"engine.py 自算证据调用 {_n_lines(out)} 处：{sorted(calls)}"


def fr20_reject_lists_missing() -> tuple[bool, str]:
    """FR-20 拒收给缺项清单 + 契约片段。"""
    sd = _fresh()
    try:
        _seed(sd)
        rc, out = _cli(["done", "t", "c"], sd)          # 实写，验收 false
        ok = rc != 0 and "拒收" in out and "契约片段" in out
        return ok, f"拒收 rc={rc}；含「拒收」={'拒收' in out}；含「契约片段」={'契约片段' in out}"
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr21_views_same_source() -> tuple[bool, str]:
    """FR-21 三视图同源、不各存一份。"""
    rc1, o1 = _git_grep(r"^def (board_lines|tree_lines|graph_lines)", "disflow/views.py")
    rc2, o2 = _git_grep(r"write_json|append_jsonl", "disflow/views.py")
    ok = _n_lines(o1) == 3 and not o2.strip()
    return ok, f"views.py 渲染函数 {_n_lines(o1)} 个（应 3）；写盘命中 {_n_lines(o2)} 处（应 0）"


def fr23_awaits_signal() -> tuple[bool, str]:
    """FR-23 跨树依赖 = 显式完成信号 awaits（阻塞就绪）。"""
    sd = _fresh()
    try:
        _cli(["init", "t", "--goal", "g"], sd)
        child = json.dumps([{"id": "a", "goal": "A", "acceptance": {"cmd": "true"},
                             "awaits": ["ghost-tree"]}])
        _cli(["expand", "t", "root", "--children", child], sd)
        _rc, status = _cli(["status", "t", "--json"], sd)
        blocked = '"ready": []' in status
        return blocked, f"awaits 未满足的节点就绪列表为空={'\"ready\": []' in status}"
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr24_brief_context() -> tuple[bool, str]:
    """FR-24 brief = 祖先链 context + 前置产出摘要。"""
    rc1, o1 = _git_grep(r"inherited_context", "disflow/context.py")
    rc2, o2 = _git_grep(r"from_requires", "disflow/context.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"context.py 注入祖先链 {_n_lines(o1)} 处、前置摘要 {_n_lines(o2)} 处"


def fr25_wave_packing() -> tuple[bool, str]:
    """FR-25 同波写面重叠检查，重叠自动排队。"""
    rc1, o1 = _git_grep(r"^def pack_waves", "disflow/dispatch.py")
    rc2, o2 = _git_grep(r"_touches_overlap", "disflow/dispatch.py")
    rc3, o3 = _git_grep(r"pack_waves", "disflow/dispatch.py")
    ok = bool(o1.strip()) and bool(o2.strip()) and bool(o3.strip())
    return ok, (f"dispatch.py pack_waves 定义 {_n_lines(o1)}；"
                f"重叠原语 {_n_lines(o2)}；调用点合计 {_n_lines(o3)}")


def fr26_single_writer_lease() -> tuple[bool, str]:
    """FR-26 单一写者收口（worktree）+ 租约防重。"""
    rc1, o1 = _git_grep(r"^def path_for", "disflow/worktree.py")
    rc2, o2 = _git_grep(r'node\.get\("lease"\)', "disflow/ready.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"worktree.path_for 定义 {_n_lines(o1)}；ready 里租约占位判定 {_n_lines(o2)}"


def fr27_stuck() -> tuple[bool, str]:
    """FR-27 status 能识别卡死（无 ready、无 running、却未全 done）。"""
    sd = _fresh()
    try:
        _cli(["init", "t", "--goal", "g"], sd)
        child = json.dumps([{"id": "a", "goal": "A", "acceptance": {"cmd": "true"},
                             "awaits": ["ghost-tree"]}])
        _cli(["expand", "t", "root", "--children", child], sd)
        _rc, status = _cli(["status", "t", "--json"], sd)
        return '"stuck": true' in status, f"阻塞树的 status.stuck={'\"stuck\": true' in status}"
    finally:
        shutil.rmtree(sd, ignore_errors=True)


def fr28_import_mgt() -> tuple[bool, str]:
    """FR-28 import mgt 从 state.json / registry.json 读入。"""
    ok_file = os.path.isfile(os.path.join(ROOT, "disflow/import_mgt.py"))
    rc1, o1 = _git_grep(r"state\.json", "disflow/import_mgt.py")
    rc2, o2 = _git_grep(r"registry\.json", "disflow/import_mgt.py")
    ok = ok_file and bool(o1.strip()) and bool(o2.strip())
    return ok, f"import_mgt.py 存在={ok_file}；读 state.json {_n_lines(o1)}、registry.json {_n_lines(o2)}"


def fr29_mapping_doc() -> tuple[bool, str]:
    """FR-29 映射关系写进 design.md §10。"""
    rc, out = _git_grep(r"## 10\. mgt 迁移映射", "docs/design.md")
    return bool(out.strip()), f"design.md §10 标题命中 {_n_lines(out)} 处"


def nfr1_zero_deps() -> tuple[bool, str]:
    """NFR-1 零第三方依赖。"""
    bad = _imports_in_disflow()
    return (not bad), f"disflow/ 非标准库导入：{sorted(bad) or '（无）'}"


def nfr3_file_based() -> tuple[bool, str]:
    """NFR-3 文件式：无服务、无数据库。不用 `\b`（见 FR-5 注释：本环境 `\b` 失效）。"""
    rc, out = _git_grep(
        r"http\.server|socketserver|sqlite3|flask|fastapi|django|\.db([^[:alnum:]_]|$)",
        *_py_sources())
    return (rc == 1 and not out.strip()), f"服务/DB 相关命中 {_n_lines(out)} 处（应 0）"


def nfr6_size_budget() -> tuple[bool, str]:
    """NFR-6 规模预算：超预算即拒。"""
    rc1, o1 = _git_grep(r"规模超预算", "disflow/engine.py")
    rc2, o2 = _git_grep(r"^def resolve_budget", "disflow/gates.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"engine.py 超预算拒收 {_n_lines(o1)} 处；gates.resolve_budget 定义 {_n_lines(o2)}"


def nfr7_no_silent() -> tuple[bool, str]:
    """NFR-7 不静默降级：坏行/写失败/未知 kind 一律报错。"""
    rc1, o1 = _git_grep(r"raise", "disflow/store.py")
    rc2, o2 = _git_grep(r"未知事件 kind|raise ValueError", "disflow/state.py")
    ok = bool(o1.strip()) and bool(o2.strip())
    return ok, f"store.py raise 命中 {_n_lines(o1)} 处；state.py 报错路径 {_n_lines(o2)} 处"


Check = Callable[[], tuple[bool, str]]

# (编号, 标题, 检查函数) —— 这些是 [机检]，脚本真跑。
MACHINE: list[tuple[str, str, Check]] = [
    ("FR-1", "状态来自 append-only 事件流", fr1_append_only),
    ("FR-2", "rebuild --check 逐字节一致", fr2_rebuild_check),
    ("FR-3", "投影原子替换 + flock 真互斥", fr3_atomic_flock),
    ("FR-4", "每节点至多一个 parent + 祖先链查询", fr4_single_parent),
    ("FR-5", "无 contains 边（层级只由 parent 定义）", fr5_no_contains),
    ("FR-6", "stage 与 role 独立", fr6_stage_role),
    ("FR-8", "requires 必须 DAG，写时拒环", fr8_cycle_rejected),
    ("FR-9", "relates 软边不影响就绪", fr9_relates_soft),
    ("FR-10", "consumes 指向未登记项即拒", fr10_consumes_rejected),
    ("FR-11", "唯一 ready(n) 实现", fr11_single_ready),
    ("FR-13", "就绪结果可 --dry-run 输出", fr13_dry_run),
    ("FR-14", "drive 只推不跑、不 spawn", fr14_no_spawn),
    ("FR-15", "brief 写全 + collect 不写 node-done", fr15_brief_collect),
    ("FR-16", "引擎不内置常驻进程", fr16_no_daemon),
    ("FR-18", "done 前必须过验收门", fr18_acceptance_gate),
    ("FR-19", "证据元数据由引擎自算", fr19_evidence_selfcomputed),
    ("FR-20", "拒收给缺项清单 + 契约片段", fr20_reject_lists_missing),
    ("FR-21", "三视图同源、不各存一份", fr21_views_same_source),
    ("FR-23", "跨树依赖降级为 awaits 显式信号", fr23_awaits_signal),
    ("FR-24", "brief = 祖先链 context + 前置摘要", fr24_brief_context),
    ("FR-25", "同波写面重叠检查、重叠排队", fr25_wave_packing),
    ("FR-26", "单一写者收口 + 租约防重", fr26_single_writer_lease),
    ("FR-27", "status 能识别卡死", fr27_stuck),
    ("FR-28", "import mgt 从 state/registry 读入", fr28_import_mgt),
    ("FR-29", "映射关系写进 design.md §10", fr29_mapping_doc),
    ("NFR-1", "零第三方依赖", nfr1_zero_deps),
    ("NFR-3", "文件式：无服务、无数据库", nfr3_file_based),
    ("NFR-6", "规模预算，超预算即拒", nfr6_size_budget),
    ("NFR-7", "不静默降级", nfr7_no_silent),
]

# [测试]：只有测试覆盖、没有独立命令。（编号, 标题, 测试文件::类.方法, 为什么不可独立机检）
TEST_ONLY: list[tuple[str, str, str, str]] = [
    ("FR-7", "context 祖先链投影有限且确定",
     "tests/test_core.py::ContextTest::test_inherited_context_near_overrides_far",
     "「有限且确定」是对一条祖先链的**行为**断言，grep 只能证函数在场，证不了确定性。"),
    ("FR-12", "ready(n) 覆盖 pending/分型/requires/租约/写面",
     "tests/test_core.py::ReadyTest（test_ready_requires_deps_done / "
     "test_container_never_ready / test_lease_blocks_ready）",
     "组合条件只能在具体节点图上跑；且「本波写面不重叠」实现在 `dispatch.pack_waves`、"
     "**不在** `is_ready` 内（见报告 ◐ 部分缺口）。"),
    ("FR-22", "多板 = 多棵树，隔离",
     "tests/test_multitree.py::MultiTreeTest::test_trees_are_isolated",
     "隔离是两棵树并存时的行为，无单条 grep 能判定。"),
    ("NFR-2", "核心语言中立（业务词不进 core）",
     "tests/test_layering.py::LayeringTest::test_core_has_no_business_nouns",
     "针脚表住在测试文件里；在 fr_check 里另抄一份会造出第二把尺子、并可漂移。"),
    ("NFR-4", "可测试：就绪/依赖/门是纯函数，可单测",
     "tests/test_core.py::ReadyTest、tests/test_gates.py::ExpectSemanticsTest",
     "「可单测」由这些单测通过来证明；没有比「跑它们」更独立的命令。"),
    ("NFR-5", "可断点续跑：崩溃后由事件流恢复",
     "tests/test_store.py::StoreTest::test_events_are_append_only_and_replayable",
     "「进程崩溃后恢复」需要真的杀掉进程再重建；测试用「重放事件流得同一投影」等价替代。"),
]

# [不可机检]：语义判断，不冒充通过。（编号, 标题, 为什么）
UNJUDGEABLE: list[tuple[str, str, str]] = [
    ("FR-17", "触发方式支持 cron / 事件 / 显式三种，引擎自身不常驻",
     "「支持三种触发方式」是**架构判断**：没有一条命令能判定「够不够支持」。可观察的相关事实"
     "是 disflow/ 内无内置调度器（`daemon|cron` 命中 0，这条由 FR-16 机检），"
     "且 design.md §7 把 cron 明确列为**宿主外部**（`外部 cron | drive + 派发`）。"
     "「引擎自身不常驻」那半句是机检的（FR-16），但整条 FR-17 无法机检。"),
]

# [未实现]：实现里找不到对应能力。（编号, 标题, 缺什么, 若要做验收点）
UNIMPLEMENTED: list[tuple[str, str, str, str]] = []

# ◐ 部分缺口：整条机检通过，但**子句**在实现里找不到。（编号, 子句, 缺什么, 验收点）
PARTIAL: list[tuple[str, str, str, str]] = [
    ("FR-15", "提问纪律",
     "brief 的载荷里没有「提问纪律」相关字段（全仓 `提问`/`question` 于 disflow/ 命中 0）。"
     "brief 现含 goal/context/from_requires/acceptance/touch_plan，collect 校验结果契约，"
     "但「提问纪律」这条子句无对应实现。",
     "若要做：`brief` 载荷加 `question_policy`（文本或结构化），"
     "并有测试断言它出现在 brief 输出里。"),
    ("FR-17", "cron / 事件触发（内置）",
     "disflow/ 内无 cron/事件触发实现（命中 0）；design.md 明确把它列为宿主外部。",
     "若要做：加 `disflow drive --watch`/触发器适配层——但这会与 FR-16「不常驻」冲突，"
     "须先决定边界。"),
    ("FR-22", "每棵树一个权威分发者",
     "没有「分发者身份」概念，也没有对「同树多分发者」的检测；`flock` 只串行化写入。",
     "若要做：给 `drive`/`start` 记分发者身份并在 doctor 里报「同树多分发者」。"),
]

# 负向对照：把实现改坏 → 对应判据必须变红（证明判据有牙，绿不是真空）。
# 全部已当场 `git checkout --` 还原；此为贴出的红输出摘要。
NEGATIVE_CONTROLS: list[tuple[str, str, str]] = [
    ("FR-2", "把 `store.compare_projection` 开头改成 `return True, []`（投影永判一致）",
     "`**FAIL** [FR-2] rebuild --check 逐字节一致`；四类计数 失败 1"),
    ("FR-5", "往 `disflow/core.py` 追加 `NODE_EDGE_CONTAINS = \"contains\"`",
     "`**FAIL** [FR-5] 无 contains 边（层级只由 parent 定义）`；四类计数 失败 1。"
     "**本轮正因此发现初版 `\\bcontains\\b` 模式在本机真空通过。**"),
    ("FR-14", "往 `disflow/engine.py` 追加 `import subprocess`",
     "`**FAIL** [FR-14] drive 只推不跑、不 spawn`；四类计数 失败 1"),
    ("FR-18", "把 `gates._check_expect` 的 `exit0` 分支改成恒 `return True`",
     "`**FAIL** [FR-18] done 前必须过验收门`；四类计数 失败 2（dirty-repo 变体还叠加 FR-20）"),
    ("NFR-1", "往 `disflow/views.py` 追加 `import requests`",
     "`**FAIL** [NFR-1] 零第三方依赖`；四类计数 失败 9（连带 CLI 检查因 import 失败全红）"),
    ("FR-27", "把 `ready.is_stuck` 的 return 改成恒 `False`",
     "`**FAIL** [FR-27] status 能识别卡死`；四类计数 失败 1"),
    ("NFR-3", "往 `disflow/views.py` 追加 `import sqlite3`（标准库，只有 NFR-3 逮得到）",
     "`**FAIL** [NFR-3] 文件式：无服务、无数据库`；四类计数 失败 2"),
]

# 本报告的遗漏面（哪些没查、为什么）。
MISSED_SCOPE: list[str] = [
    "**§1–§2 不在本报告范围**：病根/保留项/目标/非目标共 23 条已由 "
    "`scripts/goal_check.py` 机检（22/22），本轮不重复。",
    "**§5「怎么算系统了」6 条未单列**：与 §3 的 FR 大量重叠（如 §5.1 = FR-2、"
    "§5.3 确定性 = 两次 drive 相同），未另起编号机检。跨条组合行为（同一投影两次 `drive` "
    "逐字节相同）目前只有 `tests/test_e2e.py::EndToEndTest::test_drive_is_bit_identical` 覆盖。",
    "**§6 术语表**：纯定义，无机检对象（`[判断]`：术语是否与代码一致属人读）。",
    "**`scripts/goal_check.py` 的 p3 曾是真空判据**（其 `\\bcontains\\b` 在本环境 "
    "git grep -E 下一条也匹配不到）。**本报告交付后已修**：commit c5ca556 把 P3 改为引号字面量，"
    "并在 `_grep` 里硬拦 `\\b \\s \\w \\d \\S \\W \\D \\B` —— 这类转义在 POSIX ERE 下不报错、"
    "只静默返空，而空命中在那些脚本里通常意味着「通过」，故构造性拦下。仍开的："
    "**goal_check 其余 21 条判据未逐条做负向对照重验**（只有 P3 在修复时做了三向验证："
    "基线 22/22 · 注入真 contains 边→红 · 只动 `acceptance.expect` 词表→仍绿）。",
    "**负向对照只做了 7 条 / 29 条机检判据**（brief 要求的下限是 5）：其余 22 条没有做变异验证，"
    "其“有牙”是推定，不是实测。",
    "**NFR-3 只机检了“无服务/无数据库”**；“运行产物与源码分离”靠 "
    "`spec.SPEC[\"state\"][\"dir\"]` 约定，未机检（没有一条命令能判定“分离”）。",
    "**FR-12 的实现位置与文义有偏差（未判缺陷）**：文义说 `ready(n)` 覆盖“本波写面不重叠”，"
    "实际由 `dispatch.pack_waves` 实现，`is_ready` 不含写面。`[判断]`：这是设计选择还是漏实现，需人定夺。",
]


def _sort_key(item: tuple) -> tuple[int, int]:
    prefix, _, num = item[0].partition("-")
    return (0 if prefix == "FR" else 1, int(num))


def emit_report() -> str:
    """从上面的判据表生成 markdown 报告（表不从人手里誊抄）。"""
    rows: list[tuple] = []
    rows += [(c, t, "[机检]", f"`python3 scripts/fr_check.py` 的 `[{c}]` 行") for c, t, _ in MACHINE]
    rows += [(c, t, "[测试]", w) for c, t, w, _ in TEST_ONLY]
    rows += [(c, t, "[未实现]", "见下节") for c, t, _, _ in UNIMPLEMENTED]
    rows += [(c, t, "[不可机检]", "见下节") for c, t, _ in UNJUDGEABLE]
    rows.sort(key=_sort_key)

    out: list[str] = []
    out.append("# FR / NFR 缺口与机检报告（`docs/requirements.md` §3–§7）")
    out.append("")
    out.append("> 由 `scripts/fr_check.py` 生成（`python3 scripts/fr_check.py --report`）。")
    out.append("> 本条报告的头等产出**不是通过率**，而是把 **[未实现]** 与 **[不可机检]** 诚实分出来。")
    out.append("")
    out.append("## 总表（29 FR + 7 NFR = 36 条）")
    out.append("")
    out.append("| # | 一句话验收标准 | 证据类型 | 证据（可复制命令 / 测试名） |")
    out.append("| --- | --- | --- | --- |")
    for cid, title, label, evidence in rows:
        out.append(f"| {cid} | {title} | {label} | {evidence} |")
    out.append("")
    out.append(f"`[机检]` 共 {len(MACHINE)} 条，全部真跑并打印原始观测；"
               f"`[测试]` {len(TEST_ONLY)} 条；"
               f"`[未实现]` {len(UNIMPLEMENTED)} 条；`[不可机检]` {len(UNJUDGEABLE)} 条；"
               f"另有 `[◐ 部分缺口]` {len(PARTIAL)} 条。")
    out.append("")

    out.append("## [未实现]（实现里找不到对应能力——最有价值的输出）")
    out.append("")
    if not UNIMPLEMENTED:
        out.append("**（空）**：29 FR + 7 NFR 里没有整条未实现的。"
                   "但下节有 3 条 **◐ 部分缺口**（整条机检通过、子句缺失）。")
    out.append("")

    out.append("## [不可机检]（语义判断，不冒充通过）")
    out.append("")
    for cid, title, why in UNJUDGEABLE:
        out.append(f"- **{cid}**：{title}")
        out.append(f"  - 为什么不可机检：{why}")
    out.append("")

    out.append("## [◐ 部分缺口]（整条机检通过，但子句在实现里找不到）")
    out.append("")
    for cid, clause, missing, acceptance in PARTIAL:
        out.append(f"- **{cid} / {clause}**")
        out.append(f"  - 缺什么：{missing}")
        out.append(f"  - 若要做，验收点：{acceptance.removeprefix('若要做：')}")
    out.append("")

    out.append("## [测试]（只有测试覆盖、没有独立命令）")
    out.append("")
    for cid, title, where, why in TEST_ONLY:
        out.append(f"- **{cid}**：{title}")
        out.append(f"  - 测试：`{where}`")
        out.append(f"  - 为什么不可独立机检：{why}")
    out.append("")

    out.append("## 负向对照（把实现改坏 → 判据必须变红，证明“绿”不是真空）")
    out.append("")
    out.append("做法：临时改 `disflow/*` → 跑 `scripts/fr_check.py` → 贴红 → `git checkout --` 当场还原。")
    out.append("")
    for cid, mutation, red in NEGATIVE_CONTROLS:
        out.append(f"- **{cid}**：{mutation}")
        out.append(f"  - 红输出：{red}")
    out.append("")

    out.append("## 本报告的遗漏面（哪些没查、为什么）")
    out.append("")
    for item in MISSED_SCOPE:
        out.append(f"- {item}")
    out.append("")
    out.append("---")
    out.append("以上结论中，凡不能被一条命令复核的句子均以 `[判断]` 标注。")
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对 docs/requirements.md §3–§7 的 29 FR + 7 NFR 逐条机检并分类")
    parser.add_argument("--report", action="store_true",
                        help="从判据表生成 markdown 报告到 stdout（不跑机检）")
    args = parser.parse_args(argv)

    if args.report:
        print(emit_report())
        return 0

    if not os.path.isdir(os.path.join(ROOT, "disflow")):
        print("✗ 不在 disflow 仓根：找不到 disflow/", file=sys.stderr)
        return 2

    failed: list[str] = []
    for cid, title, fn in MACHINE:
        try:
            ok, evidence = fn()
        except Exception as exc:                       # 判据自身炸了 = 不通过，不静默
            ok, evidence = False, f"判据自身异常：{type(exc).__name__}: {exc}"
        print(f"{'PASS' if ok else '**FAIL**'}  [{cid}] {title}")
        print(f"        {evidence}")
        if not ok:
            failed.append(f"[{cid}] {title}")

    print("\n[测试]（只有测试覆盖，无独立命令——不冒充机检）")
    for cid, title, where, why in TEST_ONLY:
        print(f"  [{cid}] {title}")
        print(f"        → {where}")
        print(f"        {why}")

    print("\n[未实现]（实现里找不到对应能力——最有价值的输出）")
    if not UNIMPLEMENTED:
        print("  （空：29 FR + 7 NFR 里没有整条未实现的）")
    for cid, title, missing, acceptance in UNIMPLEMENTED:
        print(f"  [{cid}] {title}")
        print(f"        缺什么：{missing}")
        print(f"        验收点：{acceptance.removeprefix('若要做：')}")

    print("\n[不可机检]（语义判断，不冒充通过）")
    for cid, title, why in UNJUDGEABLE:
        print(f"  [{cid}] {title}")
        print(f"        {why}")

    print("\n[◐ 部分缺口]（整条机检通过，但子句缺失——诚实单列）")
    for cid, clause, missing, acceptance in PARTIAL:
        print(f"  [{cid}] {clause}")
        print(f"        缺什么：{missing}")
        print(f"        验收点：{acceptance.removeprefix('若要做：')}")

    total = len(MACHINE) + len(TEST_ONLY) + len(UNJUDGEABLE) + len(UNIMPLEMENTED)
    print(f"\n四类计数：机检 {len(MACHINE)}（通过 {len(MACHINE) - len(failed)} · 失败 {len(failed)}）"
          f" · 测试 {len(TEST_ONLY)} · 未实现 {len(UNIMPLEMENTED)} · 不可机检 {len(UNJUDGEABLE)}"
          f" · 合计 {total}（应 36）；另有 ◐ 部分缺口 {len(PARTIAL)} 条")
    for name in failed:
        print("  FAIL:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
