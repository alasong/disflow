#!/usr/bin/env python3
"""对 `docs/requirements.md` §3–§7 的 34 条 FR + 7 条 NFR **逐条**机检 + 分类。

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


def _code_hits(pattern: str, *paths: str) -> list[str]:
    """只匹配**真实代码行**的搜索：滤掉纯注释行与行内含该词的文档字符串行。

    为何需要：`git grep` 扫的是文本，而 Python 模块的 docstring 里常写「不做常驻 daemon」
    这类**说明性句子** —— 符号被当作行为禁时，它们会误命中（本仓实测：
    `disflow/web.py` 的模块 docstring 写了「不做常驻 daemon、不做 WebSocket」，
    于是 FR-16 的 `daemon|cron` 判据报 1 处命中，把**声明合规**读成了**违反**）。

    粗但可预测：去掉以 `#` 开头（允许缩进）的行，再去掉连续三引号之间的行。
    """
    hits = _git_grep(pattern, *paths)[1].splitlines()
    clean: list[str] = []
    for line in hits:
        body = line.split(":", 2)[-1] if ":" in line else line
        stripped = body.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(('"""', "'''", '"""', "'''")) or stripped.endswith(('"""', "'''")):
            continue          # 文档字符串行（单行或边界）——不是可执行代码
        clean.append(line)
    return clean


def _serve_start_hits() -> list[str]:
    """服务启动点是否在 `main` 保护下（FR-16 第二层判据）。

    为何用 AST 而不看行文本：`httpd.serve_forever()` 写在 `serve()` 函数体内，
    那行本身不含 `if __name__` —— 按行内容判会把**合法形态误报为违规**（实测踩到）。
    正确判据是**可达性**：从该调用向上找，它是否只被 `main()`（而 `main()` 又被
    `if __name__ == "__main__"` 调用）可达。

    实现：对每个 `serve_forever()` 调用，取其**所在的最外层函数名**，
    再看该函数名是否只在本文件的 `if __name__` 块里被调用。
    """
    import ast
    hits: list[str] = []
    for rel in _py_sources():
        path = rel.strip()
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue
        # 每个函数节点 → main 保护下的调用集合
        guarded_calls: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            left = node.test.left if isinstance(node.test, ast.Compare) else None
            if not (isinstance(left, ast.Name) and left.id == "__name__"):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name):
                        guarded_calls.add(fn.id)
                    elif isinstance(fn, ast.Attribute):
                        guarded_calls.add(fn.attr)
        # 从 main 保护点开始做**调用传递闭包**：main() 里调的 serve() 也受保护。
        # 为何要闭包：实测 `web.py` 的结构是 `if __name__ → main() → serve() → serve_forever()`，
        # 只看直接调用会把合法形态误报为违规（判据必须看可达性，不是看邻接）。
        # **方向**：从已被保护者出发，把它**调用的**函数也加进来（向下传播）。
        changed = True
        while changed:
            changed = False
            for fnode in ast.walk(tree):
                if not isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fnode.name not in guarded_calls:
                    continue
                # fnode 已被保护 ⇒ 它调用的本文件函数也受保护
                for sub in ast.walk(fnode):
                    if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)):
                        continue
                    if sub.func.id not in guarded_calls:
                        guarded_calls.add(sub.func.id)
                        changed = True
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == "serve_forever"):
                continue
            # 找到包围它的函数名（往外层找第一个包含该节点的函数）
            owner = "<module>"
            for fnode in ast.walk(tree):
                if not isinstance(fnode, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(n is node for n in ast.walk(fnode)):
                    owner = fnode.name
                    break
            if owner not in guarded_calls:
                hits.append(f"{path}:{node.lineno} (in {owner}()，未被 main 保护调用)")
    return hits


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
    """FR-16 引擎不内置**常驻**进程（2026-10-11 修订：分「常驻」与「按需入口」两类）。

    判据分两层，因为「常驻」与「按需启停」是两件事：

    - **不出现 `daemon|cron`** —— 引擎不自己起调度器（这半句无争议）。
    - **HTTP 服务的启动点必须在 `main` 保护内** —— 模块级 `serve_forever` 表示
      「一 import 就起服务」（= 常驻，违反 FR-16）；而 `python3 -m disflow.web` 这种
      **按需启停的本地入口**（Ctrl-C 即停）不是常驻进程。

    为何不再禁 `threading` 符号：`ThreadingHTTPServer` 是标准库自带的**请求并发**实现，
    它不产生常驻后台线程池（服务停了线程就没了）。旧判据把符号当行为禁（连同
    `while True` 一并禁），等于把「不常驻」实现成了「不许写并发」—— 与实际约束不符。
    """
    code_hits = _code_hits(r"daemon|cron", *_py_sources())
    if code_hits:
        return False, f"daemon/cron 命中 {len(code_hits)} 处代码行（应 0）：{code_hits[:2]}"
    # 服务启动点：必须在 main 保护下可达（AST 可达性，不看行文本）
    starts = _serve_start_hits()
    if starts:
        return False, f"服务启动点 {len(starts)} 处在 main 保护外（应 0）：{starts[:2]}"
    return True, "无 daemon/cron；无常驻服务启动点"


def fr33_spec_entity() -> tuple[bool, str]:
    """FR-33 Spec 是一等公民：**引擎级**实体（事件类型 + 顶层投影 + CLI）。

    为何这三样都要查（ADR 0006 的「独立顶层实体」定义）：

    - **事件类型**在闭集里：`spec.*` 进了 `vocab.EVENT_KINDS` —— 否则事件不可重放；
    - **顶层投影**：`state.empty()` 带 `specs` 键，**与 `trees` 并列** ——
      退化成树的字段就违背「独立顶层」（那时删树会连带删 Spec，跨交付复用就没了）；
    - **CLI 可达**：`specs` 动词在解析器里 —— 实体不能只有数据没入口。

    反面对照（为何这三条抓得住「假实现」）：只在 `state.py` 加个字典而没进事件闭集 ⇒
    第一条红；把 `specs` 塞进 `state["trees"][X]` ⇒ 第二条红；只有库函数没 CLI ⇒ 第三条红。
    """
    rc, out = _git_grep(r'"spec\.(init|link|close)"', "disflow/vocab.py")
    if rc != 0 or not out.strip():
        return False, f"`spec.*` 未进事件闭集（vocab.EVENT_KINDS）—— git grep rc={rc}"
    rc, out = _git_grep(r'\{"trees": \{\}, "specs": \{\}\}', "disflow/state.py")
    if rc != 0 or not out.strip():
        return False, f"投影顶层未带并列的 `specs` 键（state.empty）—— git grep rc={rc}"
    rc, out = _git_grep(r'add\("specs"', "disflow/cli.py")
    if rc != 0 or not out.strip():
        return False, f"`specs` 未接进 CLI（build_parser）—— git grep rc={rc}"
    return True, "spec.* 事件闭集 + 顶层 specs 投影 + CLI 入口均在场"


def fr18_acceptance_gate() -> tuple[bool, str]:
    """FR-18 done 前必须过验收门：验收失败必须拒，验收通过才放行。

    先落 `collect`（宿主循环契约，见 `cli.cmd_done`）：无 collect 的 `done` 在**进门之前**
    就被 CLI 拒（缺 collect 契约），拒因不是门 ⇒ 判据会把「契约拒」误读成「门在工作」。
    collect 后，拒/放行才由**门**决定；若把验收门改坏（`done` 恒放行），本判据仍会红。
    """
    sd = _fresh()
    try:
        _seed(sd)
        result = json.dumps({"status": "ok", "summary": "s", "changed_files": []})
        _cli(["collect", "t", "c", "--result", result], sd)   # acceptance.cmd=false
        _cli(["collect", "t", "a", "--result", result], sd)   # acceptance.cmd=true
        rc_fail, _ = _cli(["done", "t", "c", "--dry-run"], sd)   # 验收 false ⇒ 门应拒
        rc_pass, _ = _cli(["done", "t", "a", "--dry-run"], sd)   # 验收 true ⇒ 门应放
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
    """FR-20 拒收给缺项清单 + 契约片段。

    先落 `collect`：本判据要的是**门**的拒收（打印缺项清单 + `契约片段：` 段）。缺 collect
    的 CLI 契约拒收只说契约、**不打印 `契约片段`** ⇒ 不先 collect 会把这条判据照成假红。
    """
    sd = _fresh()
    try:
        _seed(sd)
        result = json.dumps({"status": "ok", "summary": "s", "changed_files": []})
        _cli(["collect", "t", "c", "--result", result], sd)
        rc, out = _cli(["done", "t", "c"], sd)          # 实写，验收 false ⇒ 门拒
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
    """NFR-3 文件式：**数据与运行形态**是文件式（无数据库、无常驻服务）。

    判据的**分界**（2026-10-11 修订，实测发现措辞与实现不一致）：

    - **数据库：一律禁** —— 这才是「文件式」的本体（事件流 + 投影就是存储层）。
    - **HTTP 客户端/静态服务：不禁** —— `disflow/web.py` 是按需启停的本地入口
      （`python3 -m disflow.web` 才起，Ctrl-C 即停），它**不是常驻进程**；
      「不常驻」由 FR-16 管，而 FR-16 禁的是「引擎**自己**起并保活的服务」。
      旧正则把 `http.server` 当符号禁，等于把**形态约束实现成了符号禁用** ——
      结果与需求原意（「无服务、无数据库」与「运行产物与源码分离」并列，
      同组 NFR-1/2/4/5/6/7 全是对**引擎形态**的约束）不符。

    保住的拦截力：数据库驱动仍命中即 FAIL（NFR-3 的核心）；常驻服务由下一条挡住。
    不用 `\b`（见 FR-5 注释：本环境 `\b` 失效）。
    """
    rc, out = _git_grep(
        r"sqlite3|flask|fastapi|django|\\.db([^[:alnum:]_]|$)|psycopg|pymysql|redis",
        *_py_sources())
    return (rc == 1 and not out.strip()), f"数据库/服务框架相关命中 {_n_lines(out)} 处（应 0）"


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


def fr34_spec_association() -> tuple[bool, str]:
    """FR-34 Spec **关联**资产（多对多、非归属）。

    判据：① 至少一个资产带 `spec` 块（`required_roles` 非空 + `delivers` 数组）= 关联存在；
    ② **无**资产带 `parent` 键 = 不是「挂在 Spec 下」的归属；③ `delivery` 交付门可机跑。
    """
    with open(os.path.join(ROOT, "assets", "registry.json"), encoding="utf-8") as fh:
        assets = json.load(fh)["assets"]
    specs = [k for k, a in assets.items()
             if isinstance(a.get("spec"), dict)
             and a["spec"].get("required_roles")
             and isinstance(a["spec"].get("delivers"), list)]
    owners = [k for k, a in assets.items() if "parent" in a]
    rc, _out = _run([sys.executable, "scripts/assets_scan.py", "delivery"])
    ok = bool(specs) and not owners and rc in (0, 1)
    return ok, (f"带 spec 块的资产 {len(specs)} 个 {specs}；"
                f"带 parent（归属）的资产 {len(owners)} 个（应 0）；delivery 门 rc={rc}")


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
    ("FR-33", "Spec 是一等公民（事件类型 + 顶层投影 + CLI）", fr33_spec_entity),
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
    ("FR-34", "Spec 关联资产（多对多、非归属）", fr34_spec_association),
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
     "**不在** `is_ready` 内（见报告「本报告的遗漏面」）。"),
    ("FR-22", "多板 = 多棵树，隔离",
     "tests/test_multitree.py::MultiTreeTest::test_trees_are_isolated",
     "隔离是两棵树并存时的行为，无单条 grep 能判定。"),
    ("FR-30", "分发器是看板的附属（逐列 WIP 上限）",
     "tests/test_board_policy.py::WipPolicyTest",
     "「列满则其余就绪者不派」是具体节点图上的行为（在跑计数 vs 上限），无单条 grep 能判定。"),
    ("FR-31", "验收器是看板的附属（逐列默认验收）",
     "tests/test_board_policy.py::AcceptancePolicyTest",
     "「节点未自带则派生、自带则优先」是 `done` 路径上的行为，grep 只能证字段在场。"),
    ("FR-32", "动态看板：结构可预设、逐树覆盖",
     "tests/test_boards_per_tree.py::PerTreeBoardsTest::test_two_trees_can_have_different_boards",
     "「逐树覆盖」是两棵树并存、同一进程读不同板集的行为，无单条 grep 能判定。"),
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
     "且 design.md §5.4 把 cron 明确列为**宿主外部**（`外部 cron | drive + 派发`）。"
     "「引擎自身不常驻」那半句是机检的（FR-16），但整条 FR-17 无法机检。"),
]

# [未实现]：实现里找不到对应能力。（编号, 标题, 缺什么, 若要做验收点）
#
# 2026-10-12：FR-33 由 `fr33_spec_entity` 接管（Spec 已落成引擎级实体）——
# 本条从「未实现」移入机检。曾在此处的文字（「无引擎级 Spec 实体…」）已成历史事实，
# 保留在 `docs/decisions/0006` 的「待实现」段落里（ADR 记当时状态，不改写）。
UNIMPLEMENTED: list[tuple[str, str, str, str]] = []

# ◐ 部分缺口：整条机检通过，但**子句**在实现里找不到。（编号, 子句, 缺什么, 验收点）
# 判据：子句必须在**需求原文里真的写着**，且确实没实现——报告不得替需求发明子句（否则计数与正文自相矛盾，
# 也会把「已满足」写成「缺口」）。经此复核，当前**没有**真正的 ◐：两条曾标 ◐ 的都已归入 DOWNGRADED。
PARTIAL: list[tuple[str, str, str, str]] = []

# 已复核·非缺口：曾标 ◐，复核后确认**需求已满足** ⇒ **不计入上面的 ◐ 计数**。
# 两条都属同一类错：把「不变量 / 系统能力」与「报告替需求发明的实现机制」搞混。（编号, 子句, 复核结论, 备注）
DOWNGRADED: list[tuple[str, str, str, str]] = [
    ("FR-17", "cron / 事件触发（内置）",
     "FR-17 原文**没写「内置」**——它要的是「系统支持 cron / 事件 / 显式三种触发方式，"
     "**引擎自身不常驻**」。design.md §5.4 把三种都列了（外部 cron → `drive` + 派发；"
     "事件 → 上游 `done` 后触发；显式 → 人主动跑），触发都在宿主侧，与「不常驻」自洽 "
     "⇒ 需求已满足。「内置」是**本报告替需求加的**子句，既非原文、又与「不常驻」互相矛盾 "
     "⇒ 原先那条 ◐ 是报告自己的过读，不是缺口。",
     "不必新增机制；`drive --watch`/触发器适配层会与 FR-16「不常驻」冲突，故不做。"),
    ("FR-22", "每棵树一个权威分发者",
     "需求要的是**不允许双重权威**；`drive` 是**纯函数**（`ready` 不看时钟、不读外部状态）"
     "⇒ 两个分发者在同一棵树上**算不出不同的波**（实测：两边都拿到 `['a','b']`）⇒ 不存在双权威。"
     "唯一代价是重复派发，而它在 `start` 被租约拦住（第二个 exit=1）⇒ 浪费一次启动，不坏数据。"
     "原先要的「分发者身份 + 同树多分发者检测」是**一种可选机制**，需求没要求，记为打磨项而非缺口。",
     "若要可观测性：`drive --holder X` 记「谁在调度」+ doctor 报同树多分发者；"
     "但那会引入一个需要过期语义的新状态（死掉的宿主永远占着），而正确性收益为 0。"),
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
    "`disflow/vocab.py` 的 `STORAGE[\"state\"][\"dir\"]` 约定，未机检（没有一条命令能判定“分离”）。",
    "**FR-12 的实现位置与文义有偏差（未判缺陷）**：文义说 `ready(n)` 覆盖“本波写面不重叠”，"
    "实际由 `dispatch.pack_waves` 实现，`is_ready` 不含写面。`[判断]`：这是设计选择还是漏实现，需人定夺。",
    "**两个机检脚本自身曾不在任何自动化套件里**（`fr_check.py` / `goal_check.py` 都不在 "
    "`unittest discover` 的覆盖内）。**本节点 n10-docs-truth 已加守卫**："
    "`tests/test_docs.py::MachineCheckGuardTest` 当场跑两个脚本并断言 exit 0（`mutation_check.py` "
    "不进守卫——真跑一次 ~4 分钟）。事故归因（三段，实测）：① **引入者** = 节点 `n7-accounting` "
    "（`26749cb`）给 CLI 加了「`done` 前先 `collect`」契约，却未同步本脚本 FR-18/FR-20 的探针；"
    "② **为何没被发现** = 本脚本不在 `unittest` 套件里，n7 的验收与全量 unittest 都覆盖不到它；"
    "③ **宿主过程错误** = 宿主合并 n7 后只重跑了 unittest 与自举探针，**没重跑两个机检脚本** "
    "⇒ 红灯活了约 40 分钟。守卫就是这条「机检必须进套件」的落地。",
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
    out.append("## 总表（34 FR + 7 NFR = 41 条）")
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
        out.append("**（空）**：34 FR + 7 NFR 里没有整条未实现的。"
                   f"**◐ 部分缺口** {len(PARTIAL)} 条（整条机检通过、子句在实现里找不到）；"
                   f"**已复核·非缺口** {len(DOWNGRADED)} 条（曾标 ◐、复核后确认需求已满足）。")
    for cid, title, missing, acceptance in UNIMPLEMENTED:
        out.append(f"- **{cid}**：{title}")
        out.append(f"  - 缺什么：{missing}")
        out.append(f"  - 若要做，验收点：{acceptance.removeprefix('若要做：')}")
    out.append("")

    out.append("## [不可机检]（语义判断，不冒充通过）")
    out.append("")
    for cid, title, why in UNJUDGEABLE:
        out.append(f"- **{cid}**：{title}")
        out.append(f"  - 为什么不可机检：{why}")
    out.append("")

    out.append("## [◐ 部分缺口]（整条机检通过，但子句在实现里找不到）")
    out.append("")
    if not PARTIAL:
        out.append("**（空）**：没有「整条机检通过、但子句在实现里找不到」的需求——"
                   "曾标 ◐ 的两条都在下节「已复核·非缺口」。")
    for cid, clause, missing, acceptance in PARTIAL:
        out.append(f"- **{cid} / {clause}**")
        out.append(f"  - 缺什么：{missing}")
        out.append(f"  - 若要做，验收点：{acceptance.removeprefix('若要做：')}")
    out.append("")

    out.append("## [已复核·非缺口]（曾标 ◐，复核后确认需求已满足——不计入上面的 ◐ 计数）")
    out.append("")
    for cid, clause, conclusion, note in DOWNGRADED:
        out.append(f"- **{cid} / {clause}**")
        out.append(f"  - 复核结论：{conclusion}")
        out.append(f"  - 备注：{note.removeprefix('若要做：')}")
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
        description="对 docs/requirements.md §3–§7 的 34 FR + 7 NFR 逐条机检并分类")
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
        print("  （空：34 FR + 7 NFR 里没有整条未实现的）")
    for cid, title, missing, acceptance in UNIMPLEMENTED:
        print(f"  [{cid}] {title}")
        print(f"        缺什么：{missing}")
        print(f"        验收点：{acceptance.removeprefix('若要做：')}")

    print("\n[不可机检]（语义判断，不冒充通过）")
    for cid, title, why in UNJUDGEABLE:
        print(f"  [{cid}] {title}")
        print(f"        {why}")

    print("\n[◐ 部分缺口]（整条机检通过，但子句缺失——诚实单列）")
    if not PARTIAL:
        print("  （空：没有整条机检通过、但子句在实现里找不到的需求）")
    for cid, clause, missing, acceptance in PARTIAL:
        print(f"  [{cid}] {clause}")
        print(f"        缺什么：{missing}")
        print(f"        验收点：{acceptance.removeprefix('若要做：')}")

    print("\n[已复核·非缺口]（曾标 ◐，复核后确认需求已满足——不计入 ◐ 计数）")
    for cid, clause, conclusion, note in DOWNGRADED:
        print(f"  [{cid}] {clause}")
        print(f"        复核结论：{conclusion}")
        print(f"        备注：{note.removeprefix('若要做：')}")

    total = len(MACHINE) + len(TEST_ONLY) + len(UNJUDGEABLE) + len(UNIMPLEMENTED)
    print(f"\n四类计数：机检 {len(MACHINE)}（通过 {len(MACHINE) - len(failed)} · 失败 {len(failed)}）"
          f" · 测试 {len(TEST_ONLY)} · 未实现 {len(UNIMPLEMENTED)} · 不可机检 {len(UNJUDGEABLE)}"
          f" · 合计 {total}（应 41）；另有 ◐ 部分缺口 {len(PARTIAL)} 条")
    for name in failed:
        print("  FAIL:", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
