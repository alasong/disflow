#!/usr/bin/env python3
"""disflow 自举门的**独立探针**：把「done 的门跟着节点的 worktree 走」这条判据固化下来。

本文件是**写在被测代码之前的独立判据**：它不 import `disflow` 的内部实现，只把
`python -m disflow.cli` 当黑盒跑一遍，再从 `<state_dir>/state.json` 读门的**原始读数**
（`acceptance.exit` / `introduced` / `vacuous`）。被测实现被改坏时它必须变红——「绿不是
证据、能红才是」（与 `scripts/independent_checks.py` 同一立场的第二条独立入口）。

四个情形各自测的机制：

  1. **顺序错（start 在 apply 之前）⇒ done 必须 fail-closed 拒收**。
     `start` 把开工基线记在仓根；之后才建 worktree ⇒ `done` 时门的目录已变。若实现不比对
     「`start` 记录的工作区」与「现在解析出的工作区」，门就会在错的目录上跑（空洞通过）。
     本情形钉住那条 **fail-closed** 分支，且节点必须**留在 running**（没被静默标记完成）。

  2. **apply 先、start 后，在 worktree 里改并提交 ⇒ 门必须看得见**。
     提交型 worker 干完就 `git commit`，此时 `git status` 为空；门若只读工作区，`introduced`
     会漏掉已提交改动（`design_first` / 规模门随之空洞通过）。本情形断言 `introduced` **含**该文件。

  3. **apply 先、start 后，删掉验收要求存在的文件并提交 ⇒ done 必须因「验收」拒收**。
     只看「退出码非 0」测不到「门跟随 worktree」：门若在仓根跑，被删文件在仓根还在、验收反而
     通过。本情形断言拒收理由**明确是验收**（`验收未通过：exit=1`），从而证明验收命令确实
     **在 worktree 里**跑过。

  4. **worktree 在 done 之前被删 ⇒ 必须拒收、绝不退回主工作区**。
     现状解析不出 worktree 时，若从现状反推「没有 worktree」就会跳过整段检查、门退回仓根
     （又一次空洞通过）。本情形钉住「以 `start`**记录**为准」的对称化 fail-closed。

用法：

    python3 scripts/disflow_selfgate_probe.py [<disflow 仓根>]

第一个位置参数缺省时取 `os.path.abspath(os.getcwd())`——**绝不用相对路径**：本探针把仓根塞进
子进程的 `PYTHONPATH`，而子进程的 cwd 是临时仓；相对路径会按子进程 cwd 解析、`import disflow`
静默失效（宿主踩过这个坑）。退出码：`0` = 四情形全过；`1` = 有未过（含用法错误）。

隔离：每个情形自建 `tempfile.mkdtemp` 临时仓 + 仓**外** state dir，失败不中断后续情形，
且逐条打印**原始读数**供人判断是不是「真空的绿」。绝不写入本仓。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

# 仓根（绝对路径）：`main` 里设定，作为子进程 `PYTHONPATH`——相对路径会静默失效。
REPO = ""
TREE = "t"
NODE = "a"


# ── 底层：git / CLI / 临时资源 ─────────────────────────────────────────────


def _git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {args} 失败（cwd={repo}）：{proc.stderr.strip()}")
    return proc


def _cli(repo: str, state: str, *args: str) -> subprocess.CompletedProcess[str]:
    """把 `disflow` CLI 当黑盒跑。子进程 cwd=临时仓、state dir 在仓外。

    `PYTHONPATH` 必须是**绝对**仓根：子进程 cwd 是临时仓，相对路径会被按它解析，
    `import disflow` 静默失效（宿主踩过的坑）。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO
    env["DISFLOW_STATE_DIR"] = state
    return subprocess.run([sys.executable, "-m", "disflow.cli", *args],
                          cwd=repo, env=env, capture_output=True, text=True)


def _write(path: str, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def _make_repo(tag: str) -> tuple[str, str, str]:
    """临时仓 + **仓外** state dir（worktree 会响亮拒绝仓内路径）。返回 (root, repo, state)。"""
    root = tempfile.mkdtemp(prefix=f"disflow-selfgate-{tag}-")
    repo = os.path.join(root, "repo")
    state = os.path.join(root, "state")
    os.makedirs(repo)
    os.makedirs(state)
    if os.path.realpath(state).startswith(os.path.realpath(repo) + os.sep):
        raise RuntimeError(f"state dir 落在临时仓内（worktree 会拒收）：{state}")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "probe@selfgate")
    _git(repo, "config", "user.name", "selfgate-probe")
    _write(os.path.join(repo, "README.md"), "readme\n")
    _write(os.path.join(repo, "OLD.txt"), "keep\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return root, repo, state


def _ok(proc: subprocess.CompletedProcess[str], what: str) -> None:
    if proc.returncode != 0:
        raise RuntimeError(f"{what} 失败（rc={proc.returncode}）："
                           f"{(proc.stderr or proc.stdout).strip()}")


def _seed(repo: str, state: str, decl: dict[str, Any]) -> None:
    """建树 + 挂一个 task 节点（`decl` 覆盖 acceptance / touch_plan 等）。"""
    _ok(_cli(repo, state, "init", TREE, "--goal", "selfgate probe"), "init")
    child = {"id": NODE, "kind": "task", "goal": "probe node", **decl}
    _ok(_cli(repo, state, "expand", TREE, "root", "--children", json.dumps([child])),
        "expand")


def _worktree_path(state: str) -> str:
    return os.path.join(state, "worktrees", TREE, NODE)


def _projection(state: str) -> dict[str, Any]:
    with open(os.path.join(state, "state.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {}


def _node_item(state: str) -> dict[str, Any]:
    trees = _projection(state).get("trees") or {}
    tree = trees.get(TREE) if isinstance(trees, dict) else None
    nodes = tree.get("nodes") if isinstance(tree, dict) else None
    item = nodes.get(NODE) if isinstance(nodes, dict) else None
    return item if isinstance(item, dict) else {}


def _evidence(state: str) -> dict[str, Any]:
    ev = _node_item(state).get("evidence")
    return ev if isinstance(ev, dict) else {}


def _status(state: str) -> str:
    return str(_node_item(state).get("status") or "")


def _stderr_lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in proc.stderr.splitlines() if line.strip()]


# ── 四个情形（每个独立建仓、独立捕获异常、打印原始读数）────────────────────


def scen_start_before_apply() -> dict[str, Any]:
    """情形 1：start 在 apply 之前 ⇒ done 必须 fail-closed 拒收，节点留在 running。"""
    root, repo, state = _make_repo("order")
    try:
        _seed(repo, state, {"acceptance": {"cmd": "test -f OLD.txt", "expect": "exit0"},
                            "touch_plan": ["OLD.txt"]})
        _ok(_cli(repo, state, "start", TREE, NODE, "--holder", "probe"), "start")
        _ok(_cli(repo, state, "worktree", "apply", TREE, "--nodes", NODE),
            "worktree apply")
        done = _cli(repo, state, "done", TREE, NODE)
        reasons: list[str] = []
        if done.returncode == 0:
            reasons.append("done 竟然通过（应 fail-closed 拒收）")
        if "开工基线" not in done.stderr and "基线不在门的目录里" not in done.stderr:
            reasons.append("拒收理由未提「开工基线不在门的目录里」")
        if _status(state) != "running":
            reasons.append(f"节点状态={_status(state)!r}（应留在 running）")
        return {"case": "1 · start 在 apply 之前 → done fail-closed",
                "passed": not reasons, "why": reasons,
                "observed": {"done.rc": done.returncode,
                             "node.status": _status(state),
                             "done.stderr": _stderr_lines(done)}}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scen_sees_committed_change() -> dict[str, Any]:
    """情形 2：apply 先、start 后，worktree 里改并提交 ⇒ introduced 必须含该文件。"""
    root, repo, state = _make_repo("sees")
    try:
        _seed(repo, state, {"acceptance": {"cmd": "test -f README.md", "expect": "exit0"},
                            "touch_plan": ["README.md"]})
        _ok(_cli(repo, state, "worktree", "apply", TREE, "--nodes", NODE),
            "worktree apply")
        _ok(_cli(repo, state, "start", TREE, NODE, "--holder", "probe"), "start")
        wt = _worktree_path(state)
        if not os.path.isdir(wt):
            raise RuntimeError(f"worktree 没建出来：{wt}")
        _write(os.path.join(wt, "README.md"), "changed\n")
        _git(wt, "add", "-A")
        _git(wt, "-c", "user.name=probe", "-c", "user.email=probe@selfgate",
             "commit", "-qm", "change README")
        done = _cli(repo, state, "done", TREE, NODE)
        ev = _evidence(state)
        introduced = ev.get("introduced")
        vacuous = ev.get("vacuous")
        raw_acceptance = ev.get("acceptance")
        acceptance = raw_acceptance if isinstance(raw_acceptance, dict) else {}
        reasons: list[str] = []
        if done.returncode != 0:
            reasons.append(f"done rc={done.returncode}（应通过）：{done.stderr.strip()}")
        if not (isinstance(introduced, list) and "README.md" in introduced):
            reasons.append(f"introduced={introduced!r}（应含 'README.md'，证明门看见已提交改动）")
        # `size_budget` 在这里空洞是**正常**的：改动已提交，工作区与 HEAD 相同 ⇒ 行数读数 0。
        # 承重判据是 `design_first` 不能再空洞——它空洞就说明门没看见改动。
        if isinstance(vacuous, dict) and "design_first" in vacuous:
            reasons.append(f"vacuous 含 design_first={vacuous['design_first']!r}"
                           "（门没看见改动 → 空洞通过）")
        return {"case": "2 · worktree 里改并提交 → 门看得见",
                "passed": not reasons, "why": reasons,
                "observed": {"done.rc": done.returncode,
                             "acceptance.exit": acceptance.get("exit"),
                             "introduced": introduced,
                             "vacuous": vacuous}}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scen_acceptance_rejects() -> dict[str, Any]:
    """情形 3：删掉验收要求存在的文件并提交 ⇒ done 必须**因验收**拒收（证明门在 worktree 里跑）。"""
    root, repo, state = _make_repo("accept")
    try:
        _seed(repo, state, {"acceptance": {"cmd": "test -f OLD.txt", "expect": "exit0"},
                            "touch_plan": ["OLD.txt"]})
        _ok(_cli(repo, state, "worktree", "apply", TREE, "--nodes", NODE),
            "worktree apply")
        _ok(_cli(repo, state, "start", TREE, NODE, "--holder", "probe"), "start")
        wt = _worktree_path(state)
        target = os.path.join(wt, "OLD.txt")
        if not os.path.isfile(target):
            raise RuntimeError(f"worktree 里没有 OLD.txt：{target}")
        os.remove(target)
        _git(wt, "add", "-A")
        _git(wt, "-c", "user.name=probe", "-c", "user.email=probe@selfgate",
             "commit", "-qm", "delete OLD.txt")
        done = _cli(repo, state, "done", TREE, NODE)
        # 门的验收读数嵌在拒收理由里（`验收未通过：exit=1（期望 0）`）——从中取出原始 exit。
        match = re.search(r"验收未通过：exit=(-?\d+)", done.stderr)
        exit_seen = int(match.group(1)) if match else None
        reasons: list[str] = []
        if done.returncode == 0:
            reasons.append("done 竟然通过（应因验收拒收）")
        if "验收未通过" not in done.stderr:
            reasons.append("拒收理由不是「验收」——测不到「门跟随 worktree」")
        if exit_seen == 0:
            reasons.append("验收 exit=0（门大概在仓根跑、没看见被删的文件）")
        if _status(state) != "running":
            reasons.append(f"节点状态={_status(state)!r}（应留在 running）")
        return {"case": "3 · 删掉验收要求的文件并提交 → 因验收拒收",
                "passed": not reasons, "why": reasons,
                "observed": {"done.rc": done.returncode,
                             "acceptance.exit": exit_seen,
                             "node.status": _status(state),
                             "done.stderr": _stderr_lines(done)}}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scen_worktree_deleted() -> dict[str, Any]:
    """情形 4：worktree 在 done 之前被删 ⇒ 必须拒收，绝不退回主工作区空洞通过。"""
    root, repo, state = _make_repo("vanished")
    try:
        _seed(repo, state, {"acceptance": {"cmd": "test -f OLD.txt", "expect": "exit0"},
                            "touch_plan": ["OLD.txt"]})
        _ok(_cli(repo, state, "worktree", "apply", TREE, "--nodes", NODE),
            "worktree apply")
        _ok(_cli(repo, state, "start", TREE, NODE, "--holder", "probe"), "start")
        wt = _worktree_path(state)
        if not os.path.isdir(wt):
            raise RuntimeError(f"worktree 没建出来：{wt}")
        shutil.rmtree(wt)                     # 模拟 reap / 手工删
        done = _cli(repo, state, "done", TREE, NODE)
        reasons: list[str] = []
        if done.returncode == 0:
            reasons.append("done 竟然通过（worktree 没了还退回主工作区空洞通过）")
        if "开工工作区已不存在" not in done.stderr:
            reasons.append("拒收理由未提「开工工作区已不存在」")
        if _status(state) != "running":
            reasons.append(f"节点状态={_status(state)!r}（应留在 running）")
        return {"case": "4 · worktree 在 done 之前被删 → 拒收",
                "passed": not reasons, "why": reasons,
                "observed": {"done.rc": done.returncode,
                             "node.status": _status(state),
                             "done.stderr": _stderr_lines(done)}}
    finally:
        shutil.rmtree(root, ignore_errors=True)


SCENARIOS: list[tuple[str, Callable[[], dict[str, Any]]]] = [
    ("1", scen_start_before_apply),
    ("2", scen_sees_committed_change),
    ("3", scen_acceptance_rejects),
    ("4", scen_worktree_deleted),
]


# ── 入口 ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    global REPO
    args = list(sys.argv[1:] if argv is None else argv)
    # 位置参数缺省 = 当前工作目录的**绝对**路径；相对路径会按子进程 cwd 解析、静默失效。
    REPO = os.path.abspath(args[0] if args else os.getcwd())
    if not os.path.isdir(os.path.join(REPO, "disflow")):
        print(f"✗ 不是 disflow 仓（缺 {os.path.join(REPO, 'disflow')}）：{REPO}",
              file=sys.stderr)
        return 1

    print(f"disflow 自举门独立探针 · 仓根 {REPO}")
    results: list[dict[str, Any]] = []
    for tag, fn in SCENARIOS:
        try:
            result = fn()
        except Exception as exc:                      # noqa: BLE001 —— 失败不中断后续情形
            result = {"case": tag, "passed": False,
                      "why": [f"情形本身抛异常：{type(exc).__name__}: {exc}"],
                      "observed": {}}
        results.append(result)
        mark = "PASS" if result["passed"] else "** FAIL **"
        print(f"  {mark}  [{tag}] {result['case']}")
        for reason in result.get("why") or []:
            print(f"        为什么算未过：{reason}")
        print(f"        原始读数：{json.dumps(result['observed'], ensure_ascii=False)}")

    failed = [r for r in results if not r["passed"]]
    if failed:
        print(f"✗ {len(failed)}/{len(results)} 个情形未过")
        return 1
    print(f"✓ {len(results)}/{len(results)} 个情形全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
