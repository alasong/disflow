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
import atexit
import concurrent.futures
import contextlib
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 每条：(名字, 文件, 原样, 换成, 要跑哪个测试文件)
Mutation = tuple[str, str, str, str, str]


class PinNotFound(Exception):
    """针脚在文件里找不到 = harness 自己的错（代码改了、针脚没跟上）。

    与“漏抓”必须分开：漏抓是「注入生效了但测试没红」= 真覆盖缺口；
    针脚未应用是「根本没注入」——把后者说成“这个行为没有被测试守住”是**编造**。
    """


class _Restorer:
    """变异注入期间登记被改写文件的原文；**正常退出 / SIGINT / SIGTERM** 一律写回。

    为什么需要（实测事故，2026-10-11）：`tests/test_mutation_harness.py` 会在**真工作树**
    上注入变异（用一条有效针脚调 `main()`），靠 `_check` 的 `finally` 还原。进程若在这个
    窗口被**杀掉**（宿主 kill 全量套件即如此），`finally` 不执行 ⇒ 变异残留在 `disflow/*.py`，
    此后每次跑都红，且从报错看不出是残留（本案：`metrics._done_as_of` 的 `<=` 被留成 `>`）。
    本类把还原从「依赖控制流」改成「依赖退出钩子」：正常退出与可捕获的信号都能还原。
    SIGKILL 拦不住（不做承诺），但宿主默认发的是 SIGTERM。
    """

    def __init__(self) -> None:
        self._pending: dict[str, str] = {}
        self._hooked = False

    def _install(self) -> None:
        if self._hooked:
            return
        atexit.register(self.restore_all)
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(ValueError, OSError):
                signal.signal(sig, self._on_signal)   # 非主线程/平台不支持：atexit 兜底
        self._hooked = True

    def arm(self, path: pathlib.Path, original: str) -> None:
        self._install()
        self._pending[str(path)] = original

    def disarm(self, path: pathlib.Path) -> None:
        self._pending.pop(str(path), None)

    def restore_all(self) -> None:
        for raw, original in list(self._pending.items()):
            with contextlib.suppress(OSError):
                pathlib.Path(raw).write_text(original, encoding="utf-8")
            self._pending.pop(raw, None)

    def _on_signal(self, signum: int, _frame: object) -> None:
        self.restore_all()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)      # 还原后按默认行为终止（退出码如实）


_RESTORER = _Restorer()

MUTATIONS: list[Mutation] = [
    # ── 被拒的事件不许落盘（store 先试算后 append）────────────────────────
    ("store 改回「先落盘再试算」", "disflow/store.py",
     "    state = stamp_projection(project(candidate), len(candidate))\n"
     "    append_jsonl(events_path(storage), record)",
     "    append_jsonl(events_path(storage), record)\n"
     "    state = stamp_projection(project(candidate), len(candidate))",
     "test_store.py"),

    # ── 改动归因：路径差集 vs 内容指纹 ───────────────────────────────────
    ("归因退回「按路径做差集」", "disflow/gates.py",
     "        if fingerprint(cwd, path, cfg) != base.get(path):",
     "        if path not in base:",
     "test_engine.py"),

    # ── diff_size 的读数分支 ─────────────────────────────────────────────
    ("diff_size 不查 HEAD（暂存改动不可见）", "disflow/gates.py",
     '    head = _numstat(cwd, ["HEAD"], paths, cfg)\n'
     "    if head is not None:\n        return head",
     "    head = None\n"
     "    if head is not None:\n        return head",
     "test_gates.py"),
    ("diff_size 不区分已跟踪文件（一律数整文件）", "disflow/gates.py",
     "        if _is_tracked(cwd, path, cfg):",
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

    # ── 配置层：独立文件 + 优先级 + 写时词汇校验 ─────────────────────────
    ("节点声明的 acceptance.timeout 不再生效", "disflow/gates.py",
     '    declared = (acceptance or {}).get("timeout")',
     '    declared = None',
     "test_config.py"),
    ("配置文件被完全忽略（永远出厂默认）", "disflow/config.py",
     '    boards = load_boards(cwd)\n    if not os.path.isfile(path):',
     '    boards = load_boards(cwd)\n    if True:',
     "test_config.py"),
    ("未知配置键被静默忽略", "disflow/config.py",
     '    unknown = sorted(set(raw) - set(_VALIDATORS))',
     '    unknown = []',
     "test_config.py"),
    ("配置类型错不再报错", "disflow/config.py",
     '    if isinstance(value, bool) or not isinstance(value, (int, float)):',
     '    if value is None:',
     "test_config.py"),
    ("walk_ignore 不再剪枝（递归整棵依赖树）", "disflow/gates.py",
     '            dirs[:] = [d for d in dirs if d not in ignore]',
     '            pass',
     "test_config.py"),
    ("stage 写时校验被移除（错别字静默多出一列）", "disflow/engine.py",
     '            if stage is not None and str(stage) not in allowed_stages:',
     '            if False:',
     "test_config.py"),
    ("配置路径跟着状态目录走（不再独立）", "disflow/config.py",
     '    return os.path.join(os.path.abspath(cwd), CONFIG_BASENAME)',
     '    from .vocab import STORAGE as _s\n'
     '    return os.path.join(os.path.abspath(cwd),\n'
     '                        os.path.basename(_s["state"]["dir"]), CONFIG_BASENAME)',
     "test_config.py"),
    ("output_tail 写死不再可配", "disflow/gates.py",
     '            "timeout": timeout, "output_tail": output[-cfg.output_tail:]}',
     '            "timeout": timeout, "output_tail": output[-2000:]}',
     "test_config.py"),
    ("验收超时不再进证据（配置生效与否看不出来）", "disflow/gates.py",
     '    meta = {"cmd": cmd, "expect": expect, "exit": proc.returncode,\n'
     '            "timeout": timeout, "output_tail": output[-cfg.output_tail:]}',
     '    meta = {"cmd": cmd, "expect": expect, "exit": proc.returncode,\n'
     '            "output_tail": output[-cfg.output_tail:]}',
     "test_readme_walkthrough.py"),
    ("作用域门对所有路径都放行（配置的顺序要求消失）", "disflow/gates.py",
     'def _covered(path: str, touch_plan: list[str]) -> bool:\n    for entry in touch_plan:',
     'def _covered(path: str, touch_plan: list[str]) -> bool:\n    return True\n    for entry in touch_plan:',
     "test_config.py"),

    # ── C5：租约人速假红——“故障”与“需人判断”必须分开 ────────────────
    ("租约过期又变回“故障”（退代码 1 + 唯一修法 reap）", "disflow/cli.py",
     '                if overdue is not None:\n'
     '                    holder = (node.get("lease") or {}).get("holder") or "?"\n'
     '                    lease_advisories.append(',
     '                if overdue is not None:\n'
     '                    holder = (node.get("lease") or {}).get("holder") or "?"\n'
     '                    problems.append(',
     "test_cli_readonly.py"),
    ("租约提醒丢掉“过期多久”（事实没了就只剩断言）", "disflow/cli.py",
     '                        f"{name}/{node_id}: 租约已过期 {_human_seconds(overdue)}"',
     '                        f"{name}/{node_id}: 租约已过期"',
     "test_cli_readonly.py"),
    ("租约提醒不再说“done 不要求租约”（最容易被误解的那句）", "disflow/cli.py",
     # `\\n` 是**源码里的两个字符**（字符串字面量内的转义），不是真换行；
     # 写错就会以“未应用”的形式被守卫测试当场抓获（实测踩过）
     '    "           ② 人还在干 → 直接 `disflow done <tree> <node> --decl …`\\n"\n'
     '    "             （**done 不要求租约**——租约防的是并发，不是“收工晚了”）。\\n"',
     '    "           ② 人还在干 → 直接 `disflow done <tree> <node> --decl …`\\n"',
     "test_cli_readonly.py"),
    ("lease_overdue_seconds 永远说没过期（提醒全消失）", "disflow/ready.py",
     '    return overdue if overdue > 0 else None',
     '    return None',
     "test_cli_readonly.py"),

    # ── batch_append：零调用方的死路径（曾被整文件屏蔽藏住）────────────────
    ("batch_append 返回旧的/错的偏移（曾是真 NameError）", "disflow/store.py",
     '    return {"appended": appended, "projection_offset": len(events) + appended}',
     '    return {"appended": appended, "projection_offset": 0}',
     "test_store.py"),
    ("batch_append 不再做整批试算（非法批次会落一半）", "disflow/store.py",
     '    candidate = stamp_projection(project(events + pending), len(events) + len(pending))',
     '    candidate = stamp_projection(events[-1] if events else {}, 0)',
     "test_store.py"),

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
     '                acceptance=acceptance, '
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

    # ── metrics.py：量化层（纯函数、as-of 截断、未估不进分母）────────────────
    #   ⚠️ metrics 是**纯函数、零时钟**：数值全靠手搓事件序列钉死。以下针脚逐个打掉
    #      「as-of 截断 / 未估排除 / 非法点数拒绝 / 起点缺失报告」四道守门，每条都必须让
    #      `test_metrics.py`（或 `test_deadline.py`）变红——否则读数是真空的。
    ("metrics._done_as_of 不再按 now_ts 截断（未来完工也算数）", "disflow/metrics.py",
     "    return ts <= cutoff_ts and _day(ts) <= cutoff_day",
     "    return True",
     "test_metrics.py"),
    ("metrics 未估节点被当成 0 进分母（velocity/eta 被凭空抬高）", "disflow/metrics.py",
     '    return {node["id"]: float(node["points"])\n'
     '            for node in tasks if node.get("points") is not None}',
     '    return {node["id"]: float(node["points"] or 0)\n'
     '            for node in tasks}',
     "test_metrics.py"),
    ("validate_points 不再拒 NaN/\u00b1inf（污染之后全部求和）", "disflow/model.py",
     "    if not math.isfinite(value):",
     "    if False:",
     "test_metrics.py"),
    ("parse_deadline 放弃规范形校验（20250131 被静默收下）", "disflow/metrics.py",
     "    if parsed.isoformat() != text:",
     "    if False:",
     "test_deadline.py"),
    ("metrics 无起点时不再报 NO_START_REASON（静默 None）", "disflow/metrics.py",
     '''    if seen and slug not in seen:
'''
     '''        return (f"未知 slug：{slug!r}；该事件流里出现过的 slug："
'''
     '''                f"{', '.join(sorted(seen))}")
'''
     "    return NO_START_REASON",
     '''    if seen and slug not in seen:
'''
     '''        return (f"未知 slug：{slug!r}；该事件流里出现过的 slug："
'''
     '''                f"{', '.join(sorted(seen))}")
'''
     "    return None",
     "test_metrics.py"),

    # ── worktree.py：每节点隔离（reap 纯计划、判据 fail-closed）────────────────
    #   ⚠️ 本模块历史上所有阻断级缺陷都落在**删除执行**这条线；下面每条针脚都必须让
    #      `test_worktree.py` 变红，证明那些危险状态真的被测试守住（尤其 reap 无副作用）。
    ("worktree 分支删除退化为 -D 强删（未合并提交不可达）", "disflow/worktree.py",
     '    return ["git", "-C", repo, "branch", "-d", branch]',
     '    return ["git", "-C", repo, "branch", "-D", branch]',
     "test_worktree.py"),
    ("worktree 不再把 ignored 文件当拒绝理由（静默丢失）", "disflow/worktree.py",
     '    for lost in ignored:\n        reasons.append(f"ignored:{lost}")',
     '    for lost in ignored:\n        pass',
     "test_worktree.py"),
    ("worktree safe_to_drop 不再 fail-closed（恒 True）", "disflow/worktree.py",
     "    safe = not reasons",
     "    safe = True",
     "test_worktree.py"),
    ("worktree residual 绕过 _plan_entry 单一漏斗（判据被架空）", "disflow/worktree.py",
     '        residual_plan.append(_plan_entry(\n'
     "            storage, tree, nid, repo,\n"
     '            path=path_for(storage, tree, nid), branch=res["branch"],\n'
     '            status=res["status"], remove_cmd=None))',
     '        residual_plan.append({\n'
     '            "node": nid, "path": path_for(storage, tree, nid),\n'
     '            "branch": res["branch"], "status": res["status"],\n'
     '            "head": res.get("tip"), "reasons": [], "safe_to_drop": True,\n'
     '            "remove_cmd": None,\n'
     '            "branch_cmd": _branch_delete_cmd(repo, res["branch"])})',
     "test_worktree.py"),
    ("worktree _inspect 对不可判定探针静默放行（不留 unreadable-*）", "disflow/worktree.py",
     '        if reached is None:\n            reasons.append(f"unreadable-refs:{head[:12]}")',
     "        if reached is None:\n            pass",
     "test_worktree.py"),
    ("worktree reap 混入执行型副作用（真删分支，不再是纯计划）", "disflow/worktree.py",
     "    entries = leaks_plan + residual_plan",
     '    for _entry in leaks_plan + residual_plan:\n'
     '        subprocess.run(["git", "-C", repo, "branch", "-D", _entry["branch"]],\n'
     "                       capture_output=True, text=True, check=False)\n"
       "    entries = leaks_plan + residual_plan",
       "test_worktree.py"),

    # ── 关树之后的边界（closed 是跳树完成信号，FR-23）───────────────────
    #   ⚠️ 本组守的是一个**已修的真缺陷**：修复前 `expand` 对已关闭的树照收，于是
    #      「已完成」的上游能继续长出未完成的任务，而 `awaits` 消费方已经开工了。
    ("expand 不再拦已关闭的树（跳树信号可被静默弄假）", "disflow/engine.py",
     '            if t.get("closed"):',
     '            if t.get("closed") and False:',
     "test_reopen.py"),
    ("reopen 不真的撤销 closed（则 expand 一直拒、逃生口是假的）", "disflow/state.py",
     '    tree["closed"] = False',
     '    tree["closed"] = tree.get("closed", False)',
     "test_reopen.py"),
    ("reopen 不清那条已承认的历史违规（则 doctor 永远消不掉一条红）", "disflow/state.py",
     '            seen.pop(slug, None)                # 且承认并清掉由它引起的那条违规\n',
     '',
     "test_reopen.py"),
    ("expand 事件不带 holder（则谁挂的无从追溯）", "disflow/engine.py",
     '                           prepare=lambda t: {"children": _with_default_stage(\n'
     '                               children, self._tree_default_stage(t)), "holder": holder})',
     '                           prepare=lambda t: {"children": _with_default_stage(\n'
     '                               children, self._tree_default_stage(t))})',
     "test_reopen.py"),

    # ── FR-15 提问纪律：brief 三样之一 + 让「我要问」报得出来 ───────────────
    #   ⚠️ 提醒：光加 `question_policy` 字段是**不可执行的装饰**——若 `status_in` 里
    #      没有 `blocked`，worker 被要求提问却报不出来，出路还是自己拍板。
    ("question_policy 用整体替换而非合并（纪律静默变窄）", "disflow/dispatch.py",
     '    base = dict(cfg.question_policy)\n'
     '    raw = (node.get("context") or {}).get("question_policy")\n'
     '    if isinstance(raw, dict):\n'
     '        for key in ("ask_host", "decide_alone", "how"):\n'
     '            if key in raw:\n'
     '                base[key] = raw[key]\n'
     '    return base',
     '    raw = (node.get("context") or {}).get("question_policy")\n'
     '    return dict(raw) if isinstance(raw, dict) else dict(cfg.question_policy)',
     "test_question_policy.py"),
    ("结果契约里去掉 blocked（则提问通道不存在）", "disflow/dispatch.py",
     '"status_in": ["ok", "failed", "blocked"],',
     '"status_in": ["ok", "failed"],',
     "test_question_policy.py"),
    ("brief 不写 question_policy（FR-15 三样缺一）", "disflow/dispatch.py",
     '    payload["question_policy"] = resolve_question_policy(nodes[node_id], cfg)\n',
     '',
     "test_question_policy.py"),
    ("配置里 question_policy 拼错了不报（静默丢掉，纪律没生效而不自知）",
     "disflow/config.py",
     '    unknown = sorted(set(value) - allowed)\n    if unknown:',
     '    unknown = []\n    if unknown:',
     "test_question_policy.py"),
]


def _run_tests(pattern: str, root: pathlib.Path = ROOT) -> int:
    for cache in root.rglob("__pycache__"):
        # 清字节码：陈旧 .pyc 会静默跑旧代码，会让变异结果错得看不出来（实测踩过）
        try:
            shutil.rmtree(cache)
        except OSError:
            continue                     # 清不掉就跳过这一处，不中断整轮
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests",
         "-p", pattern],
        cwd=root, capture_output=True, text=True).returncode


def _repo_copy() -> pathlib.Path:
    """仓的**独立临时副本**（`disflow/` + `tests/` + `scripts/` + `README.md`）。

    为何要它：`--jobs N>1` 下多条变异**同时**注入。若都打在真工作树上，A 写变异时 B
    正在读同一份源码——测试会读到别人注入的改动，结果不可信，且有残留风险。
    每条变异在一份自己的副本上注入+跑，互不共享可踩（继承
    `tests/test_mutation_harness.py::_repo_copy` 的隔离思路）。

    `README.md` 必须带上：`test_readme_walkthrough.py` 会读仓根的 `README.md`，
    副本里缺它会让该测试**报错**、被误判成“漏抓”。
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="disflow-mc-"))
    repo = tmp / "repo"
    for name in ("disflow", "tests", "scripts"):
        shutil.copytree(ROOT / name, repo / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(ROOT / "README.md", repo / "README.md")
    return repo


def _check(mutation: Mutation, root: pathlib.Path | None = None) -> tuple[bool, int, int]:
    """返回 (是否抓到, 注入后退出码, 还原后退出码)。无论如何都会还原。

    `root` 为注入与跑测试的仓根；默认真仓根（串行、逐字节不变）。`--jobs N>1` 传入
    该条变异自己的**副本**根，注入因此不碰真工作树、也不碰别的变异的副本。

    `PinNotFound` 与“漏抓”是**两件事**：
      - 针脚找不到 = 代码变了、针脚没跟上 ⇒ **harness 报错**（exit 2）；
      - 漏抓 = 注入生效了但测试没红 ⇒ **真覆盖缺口**（exit 1）。
    混在一起会把“过期的针脚”说成“这个行为没有被测试守住”——凭空造出一个不存在的结论。
    """
    base = ROOT if root is None else root
    name, relpath, old, new, pattern = mutation
    path = base / relpath
    original = path.read_text(encoding="utf-8")
    if old not in original:
        # 针脚找不到 = **harness 自己的错**（代码改了、针脚没跟上），不是“测试没守住”。
        # 两者必须分开报：把过期的针脚说成“这个行为没有被测试守住”是**编造**。
        raise PinNotFound(
            f"针脚找不到（{relpath} 的代码变了？）：\n      old = {old!r}")
    try:
        _RESTORER.arm(path, original)
        path.write_text(original.replace(old, new, 1), encoding="utf-8")
        injected = _run_tests(pattern, base)
    finally:
        _RESTORER.disarm(path)
        path.write_text(original, encoding="utf-8")
    restored = _run_tests(pattern, base)
    return (injected != 0 and restored == 0), injected, restored


def _check_in_copy(mutation: Mutation) -> tuple[bool, int, int]:
    """在**本条变异专属的仓副本**上注入+跑，跑完删副本（并发隔离的落点）。"""
    repo = _repo_copy()
    try:
        return _check(mutation, root=repo)
    finally:
        shutil.rmtree(repo.parent, ignore_errors=True)


def _summarize(selected: list[Mutation],
               unresolved: list[str], bad: list[str]) -> int:
    """收尾报告与退出码。串行/并行共用同一份，保证**汇总数字与退出码一致**。"""
    total = len(selected)
    if unresolved:
        # 先报 harness 的错：它使本轮结果不可信，谈不上“捕获率”
        print(f"\n⛔ {len(unresolved)}/{total} 条针脚**未应用**（不是漏抓）：")
        for name in unresolved:
            print(f"  - {name}")
        print("  ⇒ 这些变异根本没注入，本轮结果不可信；请把针脚对齐当前代码。")
        return 2
    print(f"\n捕获率：{total - len(bad)}/{total}")
    if bad:
        print("漏抓（这些行为没有被测试守住）：")
        for name in bad:
            print(f"  - {name}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="变异测试：验证测试套件有牙")
    parser.add_argument("-k", default="", help="只跑名字含该子串的变异")
    parser.add_argument("--list", action="store_true", help="只列变异，不执行")
    parser.add_argument(
        "--jobs", type=int, default=1, metavar="N",
        help="并发跑变异；N>1 时每条变异在**独立仓副本**上注入+跑（默认 1=串行，"
             "行为与旧版逐字节一致）")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs 必须 >= 1")

    selected: list[Mutation] = [m for m in MUTATIONS if args.k in m[0]]
    if args.list:
        for mutation in selected:
            print(mutation[0])
        return 0

    unresolved: list[str] = []
    bad: list[str] = []

    if args.jobs == 1:
        # 串行：与原版逐字节一致（同一循环、同一输出、同一退出码语义）
        for mutation in selected:
            name = mutation[0]
            try:
                caught, injected, restored = _check(mutation)
            except PinNotFound as exc:
                print(f"  ⛔ {name}：针脚未应用")
                print(f"     {exc}")
                unresolved.append(name)
                continue
            print(f"  {'✅' if caught else '❌'} {name}"
                  f"（注入 exit={injected} / 还原 exit={restored}）")
            if not caught:
                bad.append(name)
        return _summarize(selected, unresolved, bad)

    # 并行：每条变异提交给线程池，在**自己的仓副本**上跑（`_check_in_copy`）。
    # 输出顺序随完成先后而变；结果由主线程串行打印（无交错），汇总与退出码与串行一致。
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(_check_in_copy, mutation): mutation[0]
                   for mutation in selected}
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                caught, injected, restored = future.result()
            except PinNotFound as exc:
                print(f"  ⛔ {name}：针脚未应用")
                print(f"     {exc}")
                unresolved.append(name)
                continue
            print(f"  {'✅' if caught else '❌'} {name}"
                  f"（注入 exit={injected} / 还原 exit={restored}）")
            if not caught:
                bad.append(name)
    return _summarize(selected, unresolved, bad)


if __name__ == "__main__":
    raise SystemExit(main())
