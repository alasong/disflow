#!/usr/bin/env python3
"""disflow 仓内独立验证入口：`--lane a|b|all`，退出码 0/1/2。

**为什么要有这个文件**：本会话三次验证都靠临时写在 /tmp 的 harness，跑完即丢、无法回归。
把它们固化成仓内一条可重复命令，才能在实现改坏时**变红**——这是「绿不是证据、能红才是」
的落点。

**为什么每条 case 独立、失败不中断**：本会话实测过一次「脚手架吞错误让断言拿空值比较」，
一次红若中断后续，整份绿报告就是真空。故每个 case 自建临时仓、单独捕获异常，
且**打印它实际观测到的原始值**（不是 PASS/FAIL 两个字）——靠这个原始值，本会话抓到过
4 处「判据写错把通过读成失败」。

**为什么 state dir 必须仓外**：`worktree` 按设计**响亮拒绝**落在 repo 内的路径
（见 `worktree._reject_if_inside`）。state dir 若在仓内，`worktree.apply` 会直接抛，
测试会以错误原因变红。故一律用仓外的 `tempfile.mkdtemp`。

**为什么路径不依赖进程 cwd**：本会话实测过一处 cwd 相关的假象；本文件全部路径都基于
`__file__`（仓根）或 `tempfile` 的绝对路径推导，never 依赖 os.getcwd()。

原料是主控手写的三个 harness，**含主控自己的错**，合入时逐条审过：
  - `disflow_gap3_adversarial.py` 写于 `reap` 降为纯计划**之前**，它调
    `worktree.reap(..., apply=True)` 并读 `r["removed"]` / `r["failed"]` / `r["discarded"]`
    ——这些 API 在 HEAD 已不存在（`reap` 是无 apply/force 的纯计划）。本文件把对抗用例
    改写到纯计划契约：判据从「reap 拒绝执行」改为「计划条目标 `safe_to_drop=False` +
    逐条给出 `discard_warnings`」。见各用例的 `# 修正:` 注释。
  - `disflow_gap3_verifyA.py` 里 `deadline_reason` 那条判据曾被误写成「树没配截止日时
    应非空」；正确判据是「非法存储值被拒时才给 reason」，见 `_a9` / `_a9c`。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ── case 框架：独立、失败不中断、打印原始观测值 ──────────────────────────────


CASES: list[tuple[str, str, Callable[[], None]]] = []
RESULTS: list[dict] = []
_CURRENT: dict[str, str] = {"lane": "", "name": ""}


def case(lane: str, name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def decorate(fn: Callable[[], None]) -> Callable[[], None]:
        CASES.append((lane, name, fn))
        return fn
    return decorate


def check(ok: bool, detail: str, *, name: str | None = None) -> None:
    """记录并打印一条判据。`detail` 必须是**原始观测值**，供人判断是不是「真空的绿」。"""
    title = name or _CURRENT["name"]
    lane = _CURRENT["lane"]
    mark = "PASS" if ok else "**FAIL**"
    print(f"  {mark}  [{lane}] {title}\n        {detail}")
    RESULTS.append({"lane": lane, "name": title, "ok": bool(ok), "detail": detail})


# ── 环境与临时资源 ────────────────────────────────────────────────────────


def git(repo: str, *args: str, cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", cwd or repo, *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} 失败（repo={repo}）：{proc.stderr.strip()}")
    return proc


def _is_exactly_false(value: object) -> bool:
    """严格判「恰好是布尔 ``False``」：键缺失（``None``）、``0``、空串都不算。

    为什么不直接写 ``value is False``：``no-identity-operator-on-literals`` 会报。
    而这条判据的**要点就是严格性**——``not value`` 会把「``executed`` 键整个不见了」
    也读成通过，那正是本脚本要抓的回归。故先用 ``isinstance`` 钉住「确实是布尔」，
    再取反：语义与 ``is False`` 等价，且不退化。
    """
    return isinstance(value, bool) and not value


def _fresh_state_dir() -> str:
    """仓外临时 state dir，并写进 `DISFLOW_STATE_DIR`（worktree 拒绝仓内路径）。"""
    state = tempfile.mkdtemp(prefix="disflow-checks-state-")
    if os.path.realpath(state).startswith(os.path.realpath(REPO_ROOT) + os.sep):
        raise RuntimeError(f"临时 state dir 落在仓内（会触发 worktree 拒绝）：{state}")
    os.environ["DISFLOW_STATE_DIR"] = state
    return state


def _fresh_repo(prefix: str = "disflow-checks-repo-", *, ignore: str | None = None) -> str:
    repo = tempfile.mkdtemp(prefix=prefix)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("hi\n")
    if ignore is not None:
        with open(os.path.join(repo, ".gitignore"), "w") as fh:
            fh.write(ignore)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def _commit_in(wt: str, name: str = "work.txt", body: str = "important") -> str:
    with open(os.path.join(wt, name), "w") as fh:
        fh.write(body)
    git(wt, "add", "-A", cwd=wt)
    git(wt, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "node work", cwd=wt)
    return git(wt, "rev-parse", "HEAD", cwd=wt).stdout.strip()


def _reachable(repo: str, sha: str) -> bool:
    """sha 从 heads/remotes 任一 ref 可达（数据是否还在 git 层）。"""
    return (git(repo, "cat-file", "-e", sha, check=False).returncode == 0
            and bool(git(repo, "for-each-ref", "--contains", sha, "refs/heads", "refs/remotes",
                         check=False).stdout.strip()))


# ══════════════════════════════════════════════════════════════════════════
# lane a —— 量化 / 迭代的不变量（原 disflow_gap3_verifyA.py）
# ══════════════════════════════════════════════════════════════════════════


def _iso(y: int, m: int, d: int) -> str:
    return datetime(y, m, d, tzinfo=timezone.utc).isoformat()


def _ep(y: int, m: int, d: int) -> float:
    return datetime(y, m, d, tzinfo=timezone.utc).timestamp()


def _ev(kind: str, ts: str, slug: str = "t", node: str | None = None) -> dict:
    event = {"kind": kind, "slug": slug, "ts": ts}
    if node:
        event["node"] = node
    return event


def _nd(i: str, status: str = "pending", points: float | None = None, kind: str = "task") -> dict:
    return {"id": i, "kind": kind, "status": status, "points": points}


@case("a", "A1 非法点数全部拒绝（NaN/±inf/bool/str/0/负数）")
def _a1() -> None:
    from disflow.model import validate_points
    bad: dict[str, object] = {"NaN": float("nan"), "+inf": float("inf"),
                              "-inf": float("-inf"), "bool True": True, "bool False": False,
                              "str '3'": "3", "0": 0, "-1": -1, "负数 -0.5": -0.5}
    observed: dict[str, str] = {}
    for label, value in bad.items():
        try:
            observed[label] = f"接受 → {validate_points(value)!r}"
        except Exception as exc:  # noqa: BLE001 - 判据就是「必须抛」
            observed[label] = f"拒绝({type(exc).__name__})"
    check(all("拒绝" in s for s in observed.values()), json.dumps(observed, ensure_ascii=False))


@case("a", "A1b 合法点数归一为 float")
def _a1b() -> None:
    from disflow.model import validate_points
    got = {v: validate_points(v) for v in (3, 1.5, 0.5)}
    check(got == {3: 3.0, 1.5: 1.5, 0.5: 0.5}, f"{got}")


@case("a", "A1c None 合法（未估）")
def _a1c() -> None:
    from disflow.model import validate_points
    check(validate_points(None) is None, f"{validate_points(None)!r}")


@case("a", "A2 未估节点不进分母（不读成『不用干』）")
def _a2() -> None:
    from disflow.metrics import summary
    tree = {"slug": "t", "deadline": None,
            "nodes": {"a": _nd("a", "done", 3.0), "b": _nd("b", "pending"),
                      "c": _nd("c", "pending")}}
    events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 1), node="a")]
    s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 11))
    ok = (s["points_total"] == 3.0 and s["points_done"] == 3.0
          and s["points_unestimated"] == 2 and s["points_left"] == 0.0)
    check(ok, f"total={s['points_total']} done={s['points_done']} left={s['points_left']} "
              f"未估={s['points_unestimated']}")


@case("a", "A3 container 不量化")
def _a3() -> None:
    from disflow.metrics import summary
    tree = {"slug": "t", "deadline": None,
            "nodes": {"g": _nd("g", "pending", 99.0, kind="container"), "a": _nd("a", "pending", 2.0)}}
    s = summary(tree, [_ev("tree.init", _iso(2026, 1, 1))], slug="t", now_ts=_ep(2026, 1, 2))
    check(s["points_total"] == 2.0, f"total={s['points_total']}（container 99 应被忽略）")


@case("a", "A4 as-of 一致：summary.points_left == burndown 末行 remaining")
def _a4() -> None:
    from disflow.metrics import burndown, summary
    tree = {"slug": "t", "deadline": "2026-02-01",
            "nodes": {"a": _nd("a", "done", 5.0), "b": _nd("b", "pending", 5.0)}}
    events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 10), node="a")]
    for label, now in (("截点在完工日之前", _ep(2026, 1, 5)), ("截点在完工日之后", _ep(2026, 1, 20))):
        su = summary(tree, events, slug="t", now_ts=now)
        bu = burndown(tree, events, slug="t", now_ts=now)
        check(su["points_left"] == bu["series"][-1]["remaining"],
              f"{label}：summary.points_left={su['points_left']} "
              f"burndown.末行.remaining={bu['series'][-1]['remaining']} done={su['points_done']}",
              name=f"A4 {label}")


@case("a", "A5 parse_deadline 合法/空值归一")
def _a5() -> None:
    from disflow.metrics import parse_deadline
    got = {"'2026-01-01'": parse_deadline("2026-01-01"),
           " 前后空白 ": parse_deadline(" 2026-01-01 "),
           "None": parse_deadline(None), "''": parse_deadline(""), "'-'": parse_deadline("-")}
    check(got == {"'2026-01-01'": "2026-01-01", " 前后空白 ": "2026-01-01",
                  "None": None, "''": None, "'-'": None},
          json.dumps(got, ensure_ascii=False))


@case("a", "A5b parse_deadline 非规范日期拒绝")
def _a5b() -> None:
    from disflow.metrics import parse_deadline
    observed: dict[str, str] = {}
    for label, value in {"'2026-1-1'": "2026-1-1", "'2026-02-30'": "2026-02-30",
                         "'01/01/2026'": "01/01/2026"}.items():
        try:
            observed[label] = f"接受 → {parse_deadline(value)!r}"
        except Exception as exc:  # noqa: BLE001
            observed[label] = f"拒绝({type(exc).__name__})"
    check(all("拒绝" in s for s in observed.values()), json.dumps(observed, ensure_ascii=False))


@case("a", "A6 deadline 逾期不拒任何动词（它不是闸门）")
def _a6() -> None:
    from disflow.engine import Engine
    _fresh_state_dir()
    repo = _fresh_repo()
    eng = Engine(cwd=repo)
    eng.init_tree("t", "g")
    eng.deadline("t", "2020-01-01")              # 早于今天 → 必然逾期
    acc = {"cmd": "true", "expect": "exit0"}
    eng.expand("t", "root", [{"id": "a", "acceptance": dict(acc)},
                             {"id": "b", "points": 2, "acceptance": dict(acc)}])
    steps: dict[str, str] = {}
    actions = (("start", lambda: eng.start("t", "a")),
               ("skip", lambda: eng.skip("t", "a", "no need")),
               ("start b", lambda: eng.start("t", "b")),
               ("done b", lambda: eng.done("t", "b", {"note": "x"})))
    for label, fn in actions:
        try:
            fn()
            steps[label] = "放行"
        except Exception as exc:  # noqa: BLE001
            steps[label] = f"拒绝 {type(exc).__name__}: {exc}"
    check(all(v == "放行" for v in steps.values()), json.dumps(steps, ensure_ascii=False))


@case("a", "A6b 逾期被显式报告（不静默）")
def _a6b() -> None:
    from disflow import store
    from disflow.engine import Engine
    from disflow.metrics import summary
    _fresh_state_dir()
    repo = _fresh_repo()
    eng = Engine(cwd=repo)
    eng.init_tree("t", "g")
    eng.deadline("t", "2020-01-01")
    eng.expand("t", "root", [{"id": "a", "acceptance": {"cmd": "true", "expect": "exit0"}}])
    s = summary(eng.tree("t"), store.load_events(eng.spec), slug="t", now_ts=_ep(2026, 6, 1))
    ok = s["deadline_overdue_days"] > 0 and s["deadline"] == "2020-01-01"
    check(ok, f"deadline={s['deadline']} overdue={s['deadline_overdue_days']} "
              f"remaining={s['deadline_remaining_days']}")


@case("a", "A7 纯函数：两次调用同值且不改入参")
def _a7() -> None:
    import copy

    from disflow.metrics import summary
    tree = {"slug": "t", "deadline": "2026-03-01",
            "nodes": {"a": _nd("a", "done", 3.0), "b": _nd("b", "pending")}}
    events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("node.done", _iso(2026, 1, 5), node="a")]
    tree_before, events_before = copy.deepcopy(tree), copy.deepcopy(events)
    r1 = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
    r2 = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
    ok = r1 == r2 and tree == tree_before and events == events_before
    check(ok, f"同值={r1 == r2} tree未变={tree == tree_before} events未变={events == events_before}")


@case("a", "A8 tree_start_ts 的 slug 边界")
def _a8() -> None:
    from disflow.metrics import tree_start_ts
    events = [_ev("tree.init", _iso(2026, 1, 1))]
    none_slug: Any = None  # 契约是 str，但边界必须实测 None（运行期容忍、不能崩）
    got = {"nope": tree_start_ts(events, slug="nope"), "空": tree_start_ts(events, slug=""),
           "空白": tree_start_ts(events, slug="   "), "None": tree_start_ts(events, slug=none_slug)}
    check(all(v is None for v in got.values()), json.dumps(got, ensure_ascii=False))


@case("a", "A8b 无 tree.init 时给显式理由（不静默）")
def _a8b() -> None:
    from disflow.metrics import NO_START_REASON, summary
    tree = {"slug": "t", "deadline": "2026-03-01",
            "nodes": {"a": _nd("a", "done", 3.0), "b": _nd("b", "pending")}}
    s = summary(tree, [], slug="t", now_ts=_ep(2026, 1, 10))
    check(s["start_reason"] == NO_START_REASON and s["start_ts"] is None,
          f"start_reason={s['start_reason']!r} start_ts={s['start_ts']}")


@case("a", "A9 deadline 单一入口：只认 tree['deadline']（事件不构成第二事实源）")
def _a9() -> None:
    # 修正（原物料此处判据写反）：树没配截止日是**正常态、不歧义**，`deadline_reason`
    # 此时应为 None；reason 的用武之地是「树里存了非法值被拒」（见 A9c）。
    from disflow.metrics import summary
    tree = {"slug": "t", "deadline": None, "nodes": {"a": _nd("a", "pending", 1.0)}}
    events = [_ev("tree.init", _iso(2026, 1, 1)), _ev("tree.deadline", _iso(2026, 1, 1))]
    s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
    check(s["deadline"] is None and s["deadline_reason"] is None,
          f"deadline={s['deadline']!r} reason={s['deadline_reason']!r}"
          "（有 tree.deadline 事件但 tree 无字段 → 事件未成事实源）")


@case("a", "A9c 树里存了非法截止日 → summary+burndown 都显式给理由")
def _a9c() -> None:
    from disflow.metrics import burndown, summary
    events = [_ev("tree.init", _iso(2026, 1, 1))]
    for badval in ("2026-13-01", "不是日期", "2026/01/01"):
        tree = {"slug": "t", "deadline": badval, "nodes": {"a": _nd("a", "pending", 1.0)}}
        s = summary(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
        b = burndown(tree, events, slug="t", now_ts=_ep(2026, 1, 10))
        ok = (s["deadline"] is None and bool(s["deadline_reason"])
              and b["ideal"] is None and bool(b["ideal_reason"]))
        check(ok, f"{badval!r}：summary.reason={str(s['deadline_reason'])[:60]!r} "
                  f"burndown.ideal_reason={b['ideal_reason']!r}",
              name=f"A9c 非法截止日 {badval!r} 被拒且不静默")


# ══════════════════════════════════════════════════════════════════════════
# lane b —— worktree 类性质 + 端到端对抗
# ══════════════════════════════════════════════════════════════════════════

# 危险状态 → 是否「默认必须拒绝」。residual-* = 目录/登记已不在、只剩分支的残留路径。
_DANGER = {"clean": False, "dirty": True, "ignored": True, "unmerged": True,
           "detached": True, "residual-unmerged": True, "residual-clean": False}


def _seed_node(state: str, node: str = "a") -> tuple[dict, str]:
    """建一个临时 repo + 一棵 t 树，把节点 a 造成 `state` 描述的现场。返回 (spec, repo)。"""
    from disflow import worktree
    from disflow.engine import Engine

    _fresh_state_dir()
    repo = _fresh_repo(prefix=f"disflow-checks-{state}-", ignore="*.log\n" if state == "ignored" else None)
    eng = Engine(cwd=repo)
    eng.init_tree("t", "g")
    eng.expand("t", "root", [{"id": node, "acceptance": {"cmd": "true", "expect": "exit0"}}])
    spec = eng.spec
    eng.start("t", node)
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", node)
    if state == "dirty":
        with open(os.path.join(wt, "README.md"), "a") as fh:  # 改节点自己工作区里的已跟踪文件
            fh.write("改一行\n")
    elif state == "ignored":
        with open(os.path.join(wt, "debug.log"), "w") as fh:
            fh.write("x\n")
    elif state == "unmerged":
        _commit_in(wt, "f.txt", "c")
    elif state == "detached":
        git(wt, "checkout", "-q", "--detach", cwd=wt)
        _commit_in(wt, "f.txt", "c")
    elif state == "residual-unmerged":
        _commit_in(wt, "f.txt", "c")
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], check=True)
    elif state == "residual-clean":
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], check=True)
    eng.skip("t", node, "x")
    return spec, repo


def _fingerprint(spec: dict, repo: str) -> dict:
    """现实指纹：state 目录文件 + repo 工作区文件 + 全部 refs + worktree 登记 + status。"""
    from disflow import store
    fp: dict[str, str] = {}
    base = store.state_dir(spec)
    for root, _dirs, files in os.walk(base):
        for f in files:
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    fp["state:" + os.path.relpath(p, base)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                fp["state:" + os.path.relpath(p, base)] = "<unreadable>"
    for root, _dirs, files in os.walk(repo):
        if ".git" in root.split(os.sep):
            continue
        for f in files:
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    fp["repo:" + os.path.relpath(p, repo)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                fp["repo:" + os.path.relpath(p, repo)] = "<unreadable>"
    fp["refs"] = git(repo, "for-each-ref", "--format=%(refname) %(objectname)").stdout
    fp["worktrees"] = git(repo, "worktree", "list", "--porcelain").stdout
    fp["status"] = git(repo, "status", "--porcelain", "-uall").stdout
    return fp


@case("b", "① reap 在任何状态下都逐字不变更（含 refs / 登记 / 文件 / 状态目录）")
def _b1() -> None:
    from disflow import worktree
    for state in _DANGER:
        spec, repo = _seed_node(state)
        before = _fingerprint(spec, repo)
        res = worktree.reap(spec, "t", repo)
        after = _fingerprint(spec, repo)
        diff = sorted(set(before) ^ set(after)) or [k for k in before if before[k] != after.get(k)]
        check(before == after and _is_exactly_false(res.get("executed")),
              f"{state}：未变={before == after} executed={res.get('executed')}"
              + ("" if before == after else f" 差异={diff}"),
              name=f"① {state}：reap 逐字不变更")


@case("b", "② 每种危险状态都必须 fail-closed")
def _b2() -> None:
    from disflow import worktree
    for state, dangerous in _DANGER.items():
        spec, repo = _seed_node(state)
        res = worktree.reap(spec, "t", repo)
        entries = res["leaks"] + res["residual"]
        if not entries:
            check(False, f"{state}：无条目，无法判定", name=f"② {state}")
            continue
        safe_ok = all((not e["safe_to_drop"]) for e in entries) if dangerous else all(
            e["safe_to_drop"] for e in entries)
        warnings = [w for e in entries for w in (e.get("discard_warnings") or [])]
        check(safe_ok and (bool(warnings) == dangerous),
              f"{state}：safe={[e['safe_to_drop'] for e in entries]} "
              f"reasons={[e['reasons'] for e in entries]} warnings={warnings[:2]}",
              name=f"② {state}：safe_to_drop={'False' if dangerous else 'True'} + "
                   f"警告{'非空' if dangerous else '空'}")


@case("b", "③ 残留未合并分支走同一漏斗且被拒")
def _b3() -> None:
    from disflow import worktree
    spec, repo = _seed_node("residual-unmerged")
    res = worktree.reap(spec, "t", repo)
    residual = res["residual"]
    check(bool(residual) and "unmerged-commit" in " ".join(residual[0]["reasons"])
          and not residual[0]["safe_to_drop"],
          f"residual={bool(residual)} safe_to_drop={residual[0]['safe_to_drop'] if residual else None} "
          f"reasons={residual[0]['reasons'] if residual else None}")


@case("b", "③b 残留分支的 branch_cmd 用安全删 `-d`（非 -D）且 remove_cmd 为 None")
def _b3b() -> None:
    from disflow import worktree
    spec, repo = _seed_node("residual-unmerged")
    res = worktree.reap(spec, "t", repo)
    residual = res["residual"]
    sha = git(repo, "rev-parse", "disflow/t/a").stdout.strip()[:12]
    if not residual:
        check(False, "无 residual 条目")
        return
    entry = residual[0]
    check(entry["remove_cmd"] is None and "-d" in entry["branch_cmd"]
          and "-D" not in entry["branch_cmd"] and sha in " ".join(entry["reasons"]),
          f"remove_cmd={entry['remove_cmd']} branch_cmd={entry['branch_cmd']} "
          f"reasons={entry['reasons']}")


@case("b", "④ 探针坏 → 抛 WorktreeProbeError（不返回假 all-clear）")
def _b4() -> None:
    from disflow import worktree
    spec, _repo = _seed_node("clean")
    notgit = tempfile.mkdtemp(prefix="disflow-checks-notgit-")
    try:
        worktree.reap(spec, "t", notgit)
        check(False, "未抛，返回了结果")
    except worktree.WorktreeProbeError as exc:
        check(True, f"{type(exc).__name__}: {str(exc)[:80]}")
    except Exception as exc:  # noqa: BLE001
        check(True, f"抛了 {type(exc).__name__}（可接受，fail-closed）")


@case("b", "⑤ apply 仍执行 + re-attach 后 HEAD 回到旧提交")
def _b5() -> None:
    from disflow import worktree
    from disflow.engine import Engine

    _fresh_state_dir()
    repo = _fresh_repo()
    eng = Engine(cwd=repo)
    eng.init_tree("t", "g")
    eng.expand("t", "root", [{"id": "a", "acceptance": {"cmd": "true", "expect": "exit0"}}])
    spec = eng.spec
    eng.start("t", "a")                          # 保持 running（活节点），不是终态
    worktree.apply(spec, "t", repo)
    wt0 = worktree.path_for(spec, "t", "a")
    _commit_in(wt0, "f.txt", "c")
    subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt0], check=True)
    sha = git(repo, "rev-parse", "disflow/t/a").stdout.strip()
    res = worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    head_back = git(wt, "rev-parse", "HEAD").stdout.strip() if os.path.isdir(wt) else ""
    check(res["reattached"] == ["a"] and head_back == sha,
          f"reattached={res['reattached']} created={res['created']} HEAD回旧提交={head_back == sha}")


# ── 端到端对抗（原 disflow_gap3_adversarial.py；改写为 reap 纯计划契约）─────────


def _adv_case(node: str = "a", *, ignore: str | None = None) -> tuple[Any, dict, str]:
    """独立临时 state dir + 临时真 git 仓 + 播一棵 t 树，节点 a 可跑。返回 (eng, spec, repo)。"""
    from disflow.engine import Engine
    _fresh_state_dir()
    repo = _fresh_repo(prefix="disflow-checks-adv-", ignore=ignore)
    eng = Engine(cwd=repo)
    eng.init_tree("t", "g")
    acc = {"cmd": f'{sys.executable} -c "print(1)"', "expect": "contains:1"}
    eng.expand("t", "root", [{"id": node, "acceptance": dict(acc)}])
    return eng, eng.spec, repo


def _finish(eng, node: str = "a") -> None:
    eng.start("t", node)
    eng.skip("t", node, "done for test")


def _leak_of(res: dict, node: str) -> dict | None:
    return next((e for e in res["leaks"] if e["node"] == node), None)


def _entry_of(res: dict, node: str) -> dict | None:
    """leaks 与 residual 走同一漏斗，按节点取到那条计划（哪个分类由现场决定）。"""
    return _leak_of(res, node) or next((e for e in res["residual"] if e["node"] == node), None)


@case("b", "adv1 未合并提交：计划拒绝 + 提交仍可达")
def _adv1() -> None:
    from disflow import worktree
    eng, spec, repo = _adv_case()
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    sha = _commit_in(wt)
    _finish(eng)
    res = worktree.reap(spec, "t", repo)
    entry = _leak_of(res, "a")
    ok = (entry is not None and not entry["safe_to_drop"]
          and any(r.startswith("unmerged-commit") for r in entry["reasons"])
          and bool(entry.get("discard_warnings")) and _is_exactly_false(res["executed"])
          and _reachable(repo, sha))
    check(ok, f"entry={entry and {'safe': entry['safe_to_drop'], 'reasons': entry['reasons']}} "
              f"executed={res['executed']} reachable={_reachable(repo, sha)}")


@case("b", "adv2 ignored 文件：计划拒绝 + 文件还在")
def _adv2() -> None:
    from disflow import worktree
    eng, spec, repo = _adv_case(ignore="*.log\n.env\n")
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    with open(os.path.join(wt, "debug.log"), "w") as fh:
        fh.write("secret")
    _finish(eng)
    res = worktree.reap(spec, "t", repo)
    entry = _leak_of(res, "a")
    survived = os.path.exists(os.path.join(wt, "debug.log"))
    ok = (entry is not None and not entry["safe_to_drop"]
          and any(r.startswith("ignored:") for r in entry["reasons"]) and survived)
    check(ok, f"entry={entry and {'safe': entry['safe_to_drop'], 'reasons': entry['reasons']}} "
              f"survived={survived}")


@case("b", "adv3 目录已丢+分支有提交：计划拒绝 + 提交仍可达")
def _adv3() -> None:
    from disflow import worktree
    eng, spec, repo = _adv_case()
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    sha = _commit_in(wt)
    shutil.rmtree(wt)                            # 模拟目录被手工删掉（登记仍在 → 落 leaks 漏斗）
    _finish(eng)
    res = worktree.reap(spec, "t", repo)
    entry = _entry_of(res, "a")
    ok = (entry is not None and not entry["safe_to_drop"]
          and any(r.startswith("unmerged-commit") for r in entry["reasons"])
          and _reachable(repo, sha))
    check(ok, f"entry={entry and {'safe': entry['safe_to_drop'], 'reasons': entry['reasons']}} "
              f"reachable={_reachable(repo, sha)}")


@case("b", "adv4 detached 提交：计划拒绝 + 工作区未被销毁")
def _adv4() -> None:
    from disflow import worktree
    eng, spec, repo = _adv_case()
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    git(wt, "checkout", "-q", "--detach", cwd=wt)
    sha = _commit_in(wt, "detached.txt")
    readable = git(wt, "cat-file", "-e", sha, cwd=wt, check=False).returncode == 0
    _finish(eng)
    res = worktree.reap(spec, "t", repo)
    entry = _leak_of(res, "a")
    ok = (entry is not None and not entry["safe_to_drop"]
          and os.path.isdir(wt) and readable)
    check(ok, f"entry={entry and entry['safe_to_drop']} wt_dir_still_there={os.path.isdir(wt)} "
              f"commit_readable_in_wt={readable}")


@case("b", "adv5 apply 不 prune 别人的登记")
def _adv5() -> None:
    from disflow import worktree
    _eng, spec, repo = _adv_case()
    other = os.path.join(os.path.dirname(repo), "disflow-checks-other")
    subprocess.run(["git", "worktree", "add", "-q", "-b", "other/x", other], cwd=repo, check=True)
    shutil.rmtree(other)                         # 别人的 worktree「暂时不在」（卷卸载）
    before = git(repo, "worktree", "list", "--porcelain").stdout
    worktree.apply(spec, "t", repo)
    after = git(repo, "worktree", "list", "--porcelain").stdout
    check("disflow-checks-other" in before and "disflow-checks-other" in after,
          f"before_has_other={'disflow-checks-other' in before} "
          f"after_has_other={'disflow-checks-other' in after}")


@case("b", "adv6 apply(不存在的树) 抛错且零副作用")
def _adv6() -> None:
    from disflow import worktree
    _eng, spec, repo = _adv_case()
    other = os.path.join(os.path.dirname(repo), "disflow-checks-other2")
    subprocess.run(["git", "worktree", "add", "-q", "-b", "other/y", other], cwd=repo, check=True)
    shutil.rmtree(other)
    before = git(repo, "worktree", "list", "--porcelain").stdout
    try:
        worktree.apply(spec, "NOPE", repo)
        raised = False
    except KeyError:
        raised = True
    after = git(repo, "worktree", "list", "--porcelain").stdout
    check(raised and before == after,
          f"raised={raised} side_effects={'无' if before == after else '有'}")


@case("b", "adv7 残留分支 re-attach：不卡死且拿回上次提交")
def _adv7() -> None:
    from disflow import worktree
    eng, spec, repo = _adv_case()
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    sha = _commit_in(wt)
    _finish(eng)
    worktree.reap(spec, "t", repo)               # 纯计划：分支被拒留 → 残留分支
    branch_left = bool(git(repo, "branch", "--list", "disflow/t/a").stdout.strip())
    eng.retry("t", "a")
    r2 = worktree.apply(spec, "t", repo)
    head_ok = (git(wt, "rev-parse", "HEAD", cwd=wt, check=False).stdout.strip() == sha
               if os.path.isdir(wt) else False)
    check(branch_left and not r2.get("broken") and head_ok,
          f"branch_left={branch_left} reattached={r2.get('reattached')} "
          f"broken={r2.get('broken')} HEAD==old={head_ok}")


@case("b", "adv8 危险条目的 discard_warnings 逐条说清会丢什么")
def _adv8() -> None:
    # 修正（原物料此处用 reap(..., force=True) 读 r['discarded']，该 API 已随「删从引擎
    # 拿掉」一并消失）。等价判据：危险条目的 discard_warnings 必须逐条覆盖 dirty / ignored /
    # unmerged-commit 三类会丢的东西——强删前宿主读的就是它。
    from disflow import worktree
    eng, spec, repo = _adv_case(ignore="*.log\n")
    worktree.apply(spec, "t", repo)
    wt = worktree.path_for(spec, "t", "a")
    with open(os.path.join(wt, "debug.log"), "w") as fh:
        fh.write("secret")
    _commit_in(wt, "tracked.txt")
    _commit_in(wt, "more.txt", "c2")             # 让分支 tip 是未合并提交
    with open(os.path.join(wt, "README.md"), "a") as fh:  # 提交后再改脏已跟踪文件
        fh.write("uncommitted\n")
    _finish(eng)
    res = worktree.reap(spec, "t", repo)
    entry = _leak_of(res, "a")
    warnings = " ".join(entry.get("discard_warnings") or []) if entry else ""
    ok = (entry is not None and not entry["safe_to_drop"]
          and "未提交的已跟踪改动" in warnings and "被忽略的文件" in warnings
          and "永久丢失" in warnings)
    check(ok, f"reasons={entry['reasons'] if entry else None} warnings={entry.get('discard_warnings') if entry else None}")


# ── 运行器 ────────────────────────────────────────────────────────────────


def _preflight() -> str | None:
    """环境不满足 → 返回说明（退出码 2）；满足 → None。"""
    if shutil.which("git") is None:
        return "找不到 git（worktree / 端到端对抗无法建立临时仓）"
    proc = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if proc.returncode != 0:
        return f"git 不可用：{proc.stderr.strip()}"
    try:
        import disflow  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return f"无法 import disflow（仓根={REPO_ROOT}）：{type(exc).__name__}: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="disflow 仓内独立验证（量化不变量 + worktree 类性质/对抗）")
    parser.add_argument("--lane", choices=("a", "b", "all"), default="all",
                        help="a=量化/迭代不变量；b=worktree 类性质+端到端对抗；all=两者")
    parser.add_argument("--verbose", action="store_true", help="打印每个 case 的异常回溯")
    args = parser.parse_args(argv)

    problem = _preflight()
    if problem is not None:
        print(f"环境不满足（退出码 2）：{problem}")
        return 2

    lanes = {"a", "b"} if args.lane == "all" else {args.lane}
    last_lane = None
    for lane, name, fn in CASES:
        if lane not in lanes:
            continue
        if lane != last_lane:
            header = "lane a · 量化/迭代不变量" if lane == "a" else "lane b · worktree 类性质+对抗"
            print(f"\n═══ {header} ═══")
            last_lane = lane
        _CURRENT["lane"], _CURRENT["name"] = lane, name
        before = len(RESULTS)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - case 独立：一条红不吞掉后面
            if args.verbose:
                import traceback
                traceback.print_exc()
            if len(RESULTS) == before:
                check(False, f"case 抛出 {type(exc).__name__}: {exc}")
            else:
                check(False, f"case 部分完成后抛出 {type(exc).__name__}: {exc}",
                      name=f"{name}（后续抛出）")

    subset = [r for r in RESULTS if r["lane"] in lanes]
    bad = [r for r in subset if not r["ok"]]
    print(f"\n独立验证 {len(subset)} 条 · 通过 {len(subset) - len(bad)} · 失败 {len(bad)}")
    for r in bad:
        print(f"  FAIL [{r['lane']}] {r['name']}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
