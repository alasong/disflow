"""disflow · 每节点 git worktree（**派生资源，不是状态**）。

**为什么要有它**：租约只保证「同一节点只有一个写者」，但挡不住两件物理层面的混乱——
① 声明式 `touch_plan` 冲突检测依赖声明**准确**，节点实际写到声明外的地方它就失效；
② 并发 worker 共享同一个 git index / 工作区，未提交改动互相踩。
每节点一个独立 worktree 才能从物理上挡住这两件事。

**硬约束（本模块的全部设计理由）**：
1. **派生资源，不落状态**：worktree 路径是 `(state_dir, tree, node)` 的纯函数；
   创建/删除**不产生任何事件**、不新增存储字段、不改节点状态。存在性只靠只读的
   `status` / `reconcile` 与现实对账（现实 = 文件系统 + `git worktree list`）。
   一旦把 worktree 落成状态，就多了一个会漂移的第二真相源。
2. **「只推不跑」的分界是可逆 vs 不可逆**：本模块只**执行**可逆且幂等的动作——`apply`
   （创建 / 再挂载 worktree）真跑 git，因为创建可逆、跑两次结果一样、错了能删了重来。
   **不可逆的动作（删除）一律不执行**：`reap` 是**纯计划**，只给出路径、判据、理由、证据、
   以及宿主该跑的命令，**永不**执行任何 git 变更；删除由宿主读完计划后自己跑那两条打印出来
   的命令。分界不是「一律不执行」，而是**可逆 vs 不可逆**。
3. **路径绝不在 repo 内**：worktree 若落在仓内，会污染 `git status`，进而污染证据门
   （`gates` 靠 `git status` 取改动集）。故路径一律从 `store.state_dir(storage)` 派生，
   且在带 repo 的函数里**响亮拒绝**落在 repo 内的路径。**仓内是脆弱配置**：哪怕该目录
   被 `.gitignore` 忽略，一次 `git clean -xdf` 也会把状态目录连同全部 worktree 一起删掉
   （忽略 ≠ 安全）——所以本模块**不**为「仓内但被 ignore」开口子，只给可操作的错误提示。
   判定**一律以 `repo` 为基准**解析相对路径，绝不依赖进程 cwd；带 repo 的**文件系统 IO**
   也一律经 `_repo_path` 归一到 repo（否则相对 state_dir 下换个 cwd 整棵树会「消失」）。
4. **删除是唯一不可逆动作，引擎不替人删数据**：删除 worktree 会连带丢失三类不可从 git 层
   挡住的资产——未提交的**已跟踪**改动、被 `.gitignore` 忽略的文件（`git status --porcelain`
   不报，`git worktree remove` 不带 `--force` 也照删）、以及**未合并或 detached HEAD** 上不
   可从别的 ref 到达的提交。**本模块历史上所有阻断级缺陷都落在执行删除这条线上**（强删丢未
   提交工作、`branch -D` 让提交不可达、不查返回码、`apply` 全局 prune 毁掉别人的登记、
   **残留分支路径直接绕过守卫**……），而纯读面（`plan`/`reconcile`/`status`/`commands`/
   `registered`）连续多轮零阻断。故**把「删」从引擎手里拿掉**：`reap` 对 leaks 与 residual
   走**同一套判据**（`_inspect` + 守卫），逐条给出 `reasons`、`safe_to_drop`、宿主该跑的
   `remove_cmd` / `branch_cmd`（分支一律用 `-d` 安全删），危险条目另给 `discard_warnings`
   说清会丢什么。**没有任何一条路径能绕过这套判据**。宿主确要强删时走 **`reap --force`**：
   `remove_cmd` 改带 `--force`，并将将被丢弃的项逐条记进 `discarded`——破例可以，但必须
   **显式、留痕**，且仍由宿主跑命令（引擎仍不执行删除）。

   **判据 fail-closed by construction**：`safe_to_drop` 仅当 `reasons` **为空**才为 True；放行
   需要「无任何未决理由」，探针的任何**不可判定**（ref 名歧义、`for-each-ref` 失败、读不出
   工作区状态）都落进 `reasons`，方向只能是**更严**。因为没有执行，也就**没有后置校验与
   自愈**——引擎压根不动手，自愈 (`_heal_if_unreachable`) 连同一切执行分支一并删除。过拒面
   （有意保守）见 `_commit_on_other_ref`。

git 调用的形状（`capture_output` / `text` / `cwd` / `check=False`）刻意对齐
`disflow/gates.py` 里的既有 helper，不另造风格。
"""
from __future__ import annotations

import os
import subprocess

from . import store
from .vocab import TERMINAL

__all__ = [
    "WorktreeError", "WorktreeProbeError",
    "root", "path_for", "branch_for", "commands",
    "registered", "status", "plan", "reconcile", "reap", "apply",
]


class WorktreeError(RuntimeError):
    """worktree 模块的**运行时失败**基类：探针读数不可信、真跑 git 失败。

    统一成一类是为了让组装层（`cli._worktree_call`）只需归一一次 —— 否则 `apply` 里裸抛的
    `RuntimeError` 会漏出 `main` 的 `except`，用户看到的是抛栈而不是一行人话。
    """


class WorktreeProbeError(WorktreeError):
    """`git worktree list` 探针失败：读数不可信。

    与「确实没有 worktree」区分开——空集会被下游读成「一个都没有」，从而对在跑节点
    误报 missing、对 orphans 失明。探针失败时 `reconcile` **直接抛**本异常（不再返回
    `reliable=False` + 空三分类——空表会被外部调用方读成 all-clear），`reap` / `apply`
    也随之拒绝行动。想宽容展示的调用方必须**显式** `except`。
    """


# failed 与 done/skipped 同列**可回收**：failed 节点已停、没有 worker 在写它，
# 其 worktree 只是派生残余；不回收就永久占盘（且既不算 leak 又不报 orphan）。
# 若需保留现场，走 `retry` 显式重开，而不是把残留 hide 在三分类之外。
# 与 `core.TERMINAL` 的差别是**有意**的：`TERMINAL` 描述「不可再转出的状态」，
# 这里描述「worktree 可安全回收的状态」。
RECLAIMABLE: tuple[str, ...] = tuple(TERMINAL) + ("failed",)


# ── 纯派生：路径 / 分支 / 命令 ────────────────────────────────────────────


def root(storage: dict) -> str:
    """worktree 根：`<state_dir>/worktrees`。从状态目录派生（约束 1、3）。"""
    return os.path.join(store.state_dir(storage), "worktrees")


def _assert_strictly_under(base: str, path: str, *, tree: str, node: str) -> None:
    """断言 `path` **严格**位于 `base` 之下（相等不算），否则抛 `ValueError`。

    用 `abspath`+`commonpath` 做**词法**归一：`..` 会在此展开，故 `<root>/t/..`（= 根本身）
    与 `<root>/t/../..…`（逃出根）都被判出。比对两侧都先 `abspath`，故与进程 cwd 无关
    （相对 state_dir 下两个 cwd 结论一致）；符号链接不在此展开——本道只保证**词法**上不逃逸，
    真仓内/符号链接型越界由带 repo 的 `_reject_if_inside` / `_norm(realpath)` 兜。
    """
    abs_base = os.path.abspath(base)
    abs_path = os.path.abspath(path)
    try:
        common = os.path.commonpath([abs_base, abs_path])
    except ValueError:                    # 不同盘符（Windows）等：不可判定 → 一律拒绝
        common = None
    if abs_path == abs_base or common != abs_base:
        raise ValueError(
            f"worktree 路径逃出根目录：tree={tree!r} node={node!r} 算出 {path!r}"
            f"（根 {base!r}）——名字含路径分隔符/`..`，请用非路径形名字")


def path_for(storage: dict, tree: str, node: str) -> str:
    """节点 worktree 落点：`<root>/<tree>/<node>`。纯函数，无 IO、无副作用。

    `state_dir` 相对时返回相对路径。带 repo 的函数做**文件系统 IO** 前一律经
    `_repo_path` 以 repo 为基准解析成绝对（`os.path.isdir` 等按进程 cwd 解析，
    否则相对 state_dir 下换个 cwd 整棵树会「消失」）；git 子进程一律带 `cwd=repo`，
    相对路径由 git 按 repo 解析。故返回值可安全跨 cwd 传递。

    **fail-closed（第二道防线）**：算完路径后断言它**严格位于 `root(storage)` 之下**，
    不满足即抛 `ValueError`，**绝不返回**逃逸路径。为什么留两道：写时（`model.validate_path_name`
    在 `Engine.init_tree` / `validate_node_spec`）管「坏名字进不了事实源」；本道管「就算事实源里
    已有坏名字（老状态目录可能残留），算路径这一步也不放行」。只靠写时校验不够——**旧数据
    已经存在**；只靠本道也不够——它会等到算路径才拒绝，且下游别处可能绕过 `path_for` 自拼路径。
    两者是不同失效模式，不可互相替代。
    """
    base = root(storage)
    path = os.path.join(base, tree, node)
    _assert_strictly_under(base, path, tree=tree, node=node)
    return path


def branch_for(tree: str, node: str) -> str:
    """节点 worktree 的分支名：`disflow/<tree>/<node>`。纯函数。"""
    return f"disflow/{tree}/{node}"


def commands(storage: dict, tree: str, node: str, repo: str,
             base: str = "HEAD", *, force: bool = False) -> dict:
    """给定节点，返回 `{"add": argv, "remove": argv}`（**argv 列表，不是 shell 字符串**）。

    只计算，不执行。`base` 是 `add` 的起点（默认 `HEAD`）。
    `remove` **默认不带 `--force`**：脏 worktree 由 git 自己响亮拒绝，绝不静默丢弃未提交
    工作。只有显式 `force=True`（给宿主打印强删命令时）才加 `--force`；`reap` 的计划一律用默认
    （安全删）。
    落到 repo 内的路径直接抛错（约束 3）——判定以 `repo` 为基准，不依赖进程 cwd。
    """
    path = path_for(storage, tree, node)
    _reject_if_inside(path, repo)
    remove = ["git", "worktree", "remove"]
    if force:
        remove.append("--force")
    remove.append(path)
    return {
        "add": ["git", "worktree", "add", "-b", branch_for(tree, node), path, base],
        "remove": remove,
    }


# ── 只读对账 ─────────────────────────────────────────────────────────────


def registered(repo: str) -> set[str]:
    """`git worktree list --porcelain` 登记的全部 worktree 路径集合。

    探针失败（非 git 仓 / git 报错）→ 抛 `WorktreeProbeError`，**不**返回空集：
    空集会把「探针坏了」伪装成「一个 worktree 都没有」，下游据此误报 missing。
    """
    proc = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise WorktreeProbeError(
            f"git worktree list 失败（repo={repo}, rc={proc.returncode}）："
            f"{proc.stderr.strip() or proc.stdout.strip()}")
    paths: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            paths.add(line[len("worktree "):].strip())
    return paths


def status(storage: dict, tree: str, node: str, repo: str, *,
           registered_paths: set[str] | None = None) -> dict:
    """一个节点 worktree 的现实读数：
    `{node, path, exists, registered, real, dirty, ignored}`。

    `real` = 目录存在 **且**已注册 **且**是个真 worktree（有 `.git`）；只有它为真才算
    「已就绪」。`registered_paths` 允许调用方复用一次探针结果，避免重复 `git worktree
    list`（传了就不再自行探测）。
    `dirty` / `ignored` 只在**真 worktree** 上读：`dirty` 是**已跟踪**改动的布尔，
    **不可读时为 `None`**（与「干净」的 `False` 区分开——目录不在 / 不是真 worktree 时
    静默 `False` 会被读成「干净」）；`ignored` 是被 `.gitignore` 忽略的路径列表
    （`git status --porcelain` 不报它们，得靠 `--ignored=matching` 显式取）。
    tree/node 不在状态里时**响亮报错**，而不是返回一个「不存在」的安静结果——后者会把
    「节点名写错」伪装成「还没建」。
    """
    _nodes(storage, tree, node)                      # 校验存在性（不存在即抛 KeyError）
    path = path_for(storage, tree, node)
    _reject_if_inside(path, repo)
    abs_path = _repo_path(storage, tree, node, repo)
    reg = registered(repo) if registered_paths is None else registered_paths
    exists = os.path.isdir(abs_path)
    is_registered = _norm(path, repo) in {_norm(p, repo) for p in reg}
    real = exists and is_registered and _is_real_worktree(abs_path)
    dirty: bool | None = None
    ignored: list[str] = []
    if real:
        dirty, ignored = _read_worktree_state(abs_path)
    return {"node": node, "path": path, "exists": exists,
            "registered": is_registered, "real": real, "dirty": dirty,
            "ignored": ignored}


def plan(storage: dict, tree: str, repo: str, *, nodes: list[str] | None = None) -> dict:
    """对每个**活** task 节点（pending/running）给出 `{node, status, add_cmd, branch}`；**零副作用**。

    `nodes` 让调用方限定到一个波次（`dispatch.pack_waves` 的产物）；给了就只算这些
    （不在该树里 → 响亮报错）。本函数只读投影与 git，绝不改动文件系统。
    """
    all_nodes = _nodes(storage, tree)                # 校验树存在性
    if nodes is None:
        ids = sorted(nid for nid, item in all_nodes.items() if _is_live_task(item))
    else:
        unknown = [nid for nid in nodes if nid not in all_nodes]
        if unknown:
            raise KeyError(f"节点不存在：{unknown}（树 {tree}）")
        ids = sorted(nid for nid in nodes if _is_live_task(all_nodes[nid]))
    entries = []
    for nid in ids:
        entries.append({
            "node": nid,
            "status": status(storage, tree, nid, repo),
            "add_cmd": commands(storage, tree, nid, repo)["add"],
            "branch": branch_for(tree, nid),
        })
    return {"tree": tree, "root": root(storage), "plan": entries}


def reconcile(storage: dict, tree: str, repo: str) -> dict:
    """三分类现实核对（只读）：

    - `leaks`：节点**可回收**（done/skipped/failed）但 worktree 还在 → 该收口没删。
      元素是 `{node, status, dirty, ignored}`（读数随行，供 `reap` 决定是否拒绝强删）。
    - `missing`：节点在**跑**（running）却没有 worktree → 该建没建。元素是节点 id。
    - `orphans`：登记在 `root()` 下、却对不上任何**已知** tree/node 的 worktree
      （已知 = 投影里全部树的全部节点）。元素是路径。
    - `residual`：该节点的 `disflow/<tree>/<node>` 分支还存在，但**既无 worktree 也无登记**
      → 上次收口只删了目录/登记，分支成了不可见残留。元素是 `{node, branch, tip, status}`。
      不报它，`retry` 后的 `apply` re-attach 会把上次的旧状态（旧 HEAD）**静默还给 worker**，
      给出错误基线（FIX3-D2）。`reap` 的计划据此显式列出处理命令。

    **探针失败时直接抛 `WorktreeProbeError`**，不返回 `reliable=False` + 空三分类：
    空表会被外部调用方读成「对账干净」，正是最危险的假阴性。要宽容展示就显式 `except`。
    """
    all_nodes = _nodes(storage, tree)                # 校验树存在性
    reg = registered(repo)                        # 探针失败即抛（FIX2-B5）

    leaks: list[dict] = []
    missing: list[str] = []
    residual: list[dict] = []
    for nid in sorted(all_nodes):
        item = all_nodes[nid]
        if item.get("kind") != "task":
            continue
        node_status = item.get("status")
        present = status(storage, tree, nid, repo, registered_paths=reg)
        live = present["real"]
        if node_status in RECLAIMABLE and (present["exists"] or present["registered"]):
            leaks.append({"node": nid, "status": node_status,
                          "dirty": present["dirty"], "ignored": present["ignored"]})
        elif node_status == "running" and not live:
            missing.append(nid)
        if not present["exists"] and not present["registered"]:
            branch = branch_for(tree, nid)
            tip = _branch_tip(repo, branch)
            if tip is not None:
                residual.append({"node": nid, "branch": branch, "tip": tip,
                                 "status": node_status})

    known = {_norm(path_for(storage, other, nid), repo)
             for other, t in _all_trees(storage).items()
             for nid in (t.get("nodes") or {})}
    under_root = {p for p in reg if _under(p, root(storage), base=repo)}
    orphans = sorted(p for p in under_root if _norm(p, repo) not in known)
    return {"leaks": leaks, "missing": missing, "orphans": orphans,
            "residual": residual}


def reap(storage: dict, tree: str, repo: str, *, force: bool = False) -> dict:
    """**纯计划**：给 leaks 与 residual 的判据、理由、证据，以及**宿主该跑的命令**。
    **永不执行任何 git 变更**——没有 `apply`；`force` 只改**打印出来的命令**与记账，仍不执行。

    为什么删不在这里做（本模块的收尾判决）：**删除是唯一不可逆动作**，而本模块历史上每一个
    阻断级缺陷都落在执行删除这条线上（强删丢未提交工作 / `branch -D` 让提交不可达 / 不查
    返回码 / `apply` 全局 prune 毁掉别人的登记 / **残留分支的删除路径绕过守卫与记账漏斗**），
    纯读面则连续多轮零阻断。所以**把「删」从引擎手里拿掉**，交给宿主读完这份计划后自己跑那两条
    打印出来的命令；引擎不替人删数据。

    只对**可回收**节点（done/skipped/failed）给删除计划，绝不碰在跑的（那是正在干活的隔离区）。

    - **同一套判据（闭洞）**：leaks 与 residual **走同一个漏斗** `_plan_entry` → `_inspect`，
      故残留分支必须过守卫，**不存在第二条绕过判据的路径**。每条包含 `node` / `path` /
      `branch` / `head` / `reasons` / `safe_to_drop` / `remove_cmd` / `branch_cmd`。
    - `reasons` 与 `_inspect`/守卫**同一套词表**（`dirty:…` / `ignored:…` /
      `unmerged-commit:<sha>` / `unreadable-…`）。`safe_to_drop` **仅当 `reasons` 为空**才为
      True；任何脏改动、ignored 文件、未合并/detached 提交，或任何**探针不可判定**，都必须是
      False（fail-closed）。
    - `remove_cmd` 是**安全删**（`git worktree remove`，不带 `--force`，git 自己会拒绝不干净的
      工作区）；`branch_cmd` 是 **`-d`（安全删）**——未合并分支 git 会响亮拒绝，**不用 `-D`**。
      残留分支没有 worktree 可删，`remove_cmd` 为 `None`。宿主确要强删就自行加 `--force`/`-D`，
      但先读 `discard_warnings`。
    - `safe_to_drop` 为 False 的条目**额外**给 `discard_warnings`：逐条说清强删后会丢什么
      （未提交改动 / ignored 路径 / 具体提交 sha / 不可判定项）。
    - 树不存在 / 探针失败（`reconcile` 抛 `WorktreeProbeError`）即抛，不返回假 all-clear。
    `force=True`（CLI 的 `worktree reap --force`）是**显式破例**：`remove_cmd` 改带 `--force`，
    并额外给 `force: True` 与 `discarded`（逐条记下将被丢弃的项）。`force=False`（默认）时
    不新增任何字段——输出与不加 `--force` **逐字节一致**。无论传不传，引擎都**不执行**删除。

    返回 `{tree, leaks, residual, reliable, executed: False}`；`reliable` 表示所有条目的现场
    探针**均可判定**（无任何 `unreadable-…` 理由）。
    """
    report = reconcile(storage, tree, repo)          # 探针失败即抛（FIX2-B5）
    leak_nodes = {leak["node"] for leak in report["leaks"]}

    leaks_plan: list[dict] = []
    for leak in report["leaks"]:
        nid = leak["node"]
        leaks_plan.append(_plan_entry(
            storage, tree, nid, repo,
            path=path_for(storage, tree, nid), branch=branch_for(tree, nid),
            status=leak["status"],
            remove_cmd=commands(storage, tree, nid, repo, force=force)["remove"]))

    # 残留分支：分支存在但目录与登记都没。只对**可回收**节点给计划（在跑的节点不能扔掉它的
    # 基线）；同一节点的 leak 已连带处理其分支，不重复。
    residual_plan: list[dict] = []
    for res in report["residual"]:
        nid = res["node"]
        if nid in leak_nodes or res["status"] not in RECLAIMABLE:
            continue
        residual_plan.append(_plan_entry(
            storage, tree, nid, repo,
            path=path_for(storage, tree, nid), branch=res["branch"],
            status=res["status"], remove_cmd=None))

    entries = leaks_plan + residual_plan
    reliable = not any(r.startswith("unreadable")
                       for entry in entries for r in entry["reasons"])
    report_out = {"tree": tree, "leaks": leaks_plan, "residual": residual_plan,
                  "reliable": reliable, "executed": False}
    if force:
        # 显式 `--force` 是**破例**：`remove_cmd` 已带 `--force`，故必须把「会丢什么」逐条记账。
        # 破例可以，但必须显式、留痕，绝不静默通过（本仓破例纪律）。不传 `force` 时**不加**
        # 任何字段——计划与命令与加 `--force` 之前**逐字节一致**。
        report_out["force"] = True
        report_out["discarded"] = _discarded_ledger(entries)
    return report_out


def _discarded_ledger(entries: list[dict]) -> list[dict]:
    """显式 `--force` 下的**记账**：逐条列出强删会丢弃什么（`{node, losses}`）。

    干净条目（`reasons` 为空）没有可丢的东西，不入账；危险条目的 `discard_warnings` 就是丢什么
    （已由 `_inspect` 的同一套判据给出）。放在计划里而非执行结果里——删除仍由宿主跑命令，
    引擎**不执行**。
    """
    ledger: list[dict] = []
    for entry in entries:
        losses = entry.get("discard_warnings") or []
        if losses:
            ledger.append({"node": entry["node"], "losses": list(losses)})
    return ledger


def apply(storage: dict, tree: str, repo: str, *, nodes: list[str] | None = None) -> dict:
    """对 `plan` 里**尚不存在**的条目跑 `add` 命令；**幂等**（跑两次结果一样）。

    跳过判据不是「目录存在」，而是「存在 **且**已注册 **且**是个真 worktree」（`status.real`）。

    - **残留分支 re-attach（FIX2-B2）**：该节点无 worktree、但 `disflow/<tree>/<node>` 分支
      已存在时，用 `git worktree add <path> <branch>`（**不带 `-b`**）从这个分支恢复工作区，
      把上次未合并的提交**还给 worker**；进 `reattached`，调用方可见「这次是接着上次的」。
      只加闸门不 re-attach，会把「丢数据」换成「永久卡死」。
    - 其余半创建状态（目录在但未注册 / 注册了但缺 `.git`）**响亮归入 `broken`**，不静默跳过。
    - **先校验后动手**：树/节点不存在即抛，**任何副作用都不早于参数校验**（FIX2-B6）——否则
      `apply(不存在的树)` 会把整仓 worktree 登记清掉。
    - **不做全局 prune**：只清理 `root(storage)` 命名空间下、目录已不在的登记残留（逐条），
      绝不碰别人的 worktree（FIX2-B6）。
    - 显式点名（`nodes=[...]`）却被「非活节点」过滤掉的节点，单独报进 `not_applicable`
      （FIX2-B9），不与「已建好」混淆。
    返回 `{tree, created, reattached, skipped, broken, not_applicable, pruned}`。
    git 建失败即**响亮上抛**（不静默吞掉半个结果）。
    """
    report = plan(storage, tree, repo, nodes=nodes)  # 校验树/节点存在性（副作用前，FIX2-B6）
    planned_ids = {entry["node"] for entry in report["plan"]}
    not_applicable = (sorted(nid for nid in nodes if nid not in planned_ids)
                      if nodes is not None else [])

    # 只清自己命名空间下的悬空登记（目录已不在），绝不全局 prune。
    pruned = _prune_own(storage, repo)
    reg = registered(repo)
    reg_norm = {_norm(p, repo) for p in reg}
    created: list[str] = []
    reattached: list[str] = []
    skipped: list[str] = []
    broken: list[dict] = []
    for entry in report["plan"]:
        nid = entry["node"]
        path = path_for(storage, tree, nid)
        abs_path = _repo_path(storage, tree, nid, repo)
        branch = branch_for(tree, nid)
        exists = os.path.isdir(abs_path)
        is_reg = _norm(path, repo) in reg_norm
        if exists and is_reg and _is_real_worktree(abs_path):
            skipped.append(nid)
            continue
        branch_exists = _branch_exists(repo, branch)
        # 残留分支、无 worktree（目录不在 / 登记已清）→ re-attach，把上次的提交还给 worker。
        if branch_exists and not exists and not is_reg:
            cmd = ["git", "worktree", "add", path, branch]
            proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, check=False)
            if proc.returncode != 0:
                raise WorktreeError(
                    f"git worktree add（re-attach）失败（{nid}）："
                    f"{proc.stderr.strip() or proc.stdout.strip()}")
            reattached.append(nid)
            continue
        if exists or is_reg or branch_exists:
            broken.append({
                "node": nid, "path": path, "exists": exists,
                "registered": is_reg, "branch_exists": branch_exists,
                "reason": _broken_reason(exists, is_reg, branch_exists)})
            continue
        proc = subprocess.run(entry["add_cmd"], cwd=repo,
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise WorktreeError(
                f"git worktree add 失败（{nid}）：{proc.stderr.strip() or proc.stdout.strip()}")
        created.append(nid)
    return {"tree": tree, "created": created, "reattached": reattached,
            "skipped": skipped, "broken": broken,
            "not_applicable": not_applicable, "pruned": pruned}


# ── 内部：现场读数与丢失判据 ──────────────────────────────────────────────


def _inspect(storage: dict, tree: str, node: str, repo: str) -> dict:
    """真正删除前重读该 worktree 会丢什么
    （`{dirty, ignored, head, commit_safe, reasons}`）。

    `reasons` 是**明确会静默丢失或无法判定**的项：已跟踪脏改动 / 被 ignore 的文件 /
    无法从任何**别的** ref 到达的提交（未合并 或 detached HEAD）/ 任一读不出的探针。
    任一非空即触发拒绝。

    **fail-closed by construction**：`reasons` 只会随探针失效而**变多、变严**——探针不可判定
    一律在 `reasons` 里留一条 `unreadable-*`，绝不让「读不到」静默变成「没有理由 = 可以删」。
    `commit_safe` 是提交可达性判定：`True`=确证别处可达、`False`=确证只在本节点分支上
    （会丢）、`None`=不可判定（当会丢）。
    """
    abs_path = _repo_path(storage, tree, node, repo)
    dirty: bool | None = None
    ignored: list[str] = []
    reasons: list[str] = []
    if os.path.isdir(abs_path):
        dirty, ignored = _read_worktree_state(abs_path)
        if dirty is None:
            reasons.append("unreadable-status:无法读取工作区状态")
    if dirty:
        reasons.append("dirty:未提交的改动（已修改或未跟踪）")
    for lost in ignored:
        reasons.append(f"ignored:{lost}")
    head, head_readable = _worktree_head(storage, tree, node, repo)
    commit_safe: bool | None = None
    if not head_readable:
        reasons.append("unreadable-head:无法读取 HEAD")
    elif head:
        reached = _commit_on_other_ref(repo, head, branch_for(tree, node))
        if reached is None:
            reasons.append(f"unreadable-refs:{head[:12]}")
        elif reached:
            commit_safe = True
        else:
            reasons.append(f"unmerged-commit:{head[:12]}")
            commit_safe = False
    return {"dirty": dirty, "ignored": ignored, "head": head,
            "commit_safe": commit_safe, "reasons": reasons}


def _read_worktree_state(path: str) -> tuple[bool | None, list[str]]:
    """读一个真 worktree 的 `(dirty, ignored)`。

    `--ignored=matching` 才会列出被 `.gitignore` 忽略的路径（默认 `git status --porcelain`
    不报它们）——`*.log` / `__pycache__` / 失败 dump 恰是最常见的未提交资产。
    `!! ` 前缀行是 ignored，其余（`??`/` M` 等）算 dirty。探针失败 → `(None, [])`（不可读，
    不谎报「干净」）。调用方（`_inspect`）把 `dirty is None` 当**显式 reason** → 默认拒绝
    （fail-closed），不静默当成「没有理由」放行。
    """
    proc = subprocess.run(["git", "-C", path, "status", "--porcelain", "--ignored=matching"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None, []
    dirty = False
    ignored: list[str] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        if line.startswith("!! "):
            ignored.append(line[3:].strip())
        else:
            dirty = True
    return dirty, ignored


def _worktree_head(storage: dict, tree: str, node: str, repo: str) -> tuple[str | None, bool]:
    """该 worktree 当前 HEAD 的 sha；目录不在时退回分支 ref 的 sha。

    返回 `(sha, readable)`：`readable=False` 表示探针坏了（**不可判定**），`_inspect` 必须
    把它当「会丢」处理（fail-closed）；目录不在且分支**确实**不存在时返回 `(None, True)`
    （确实没有提交可丢）。
    """
    abs_path = _repo_path(storage, tree, node, repo)
    branch = branch_for(tree, node)
    if os.path.isdir(abs_path):
        proc = subprocess.run(["git", "-C", abs_path, "rev-parse", "--verify", "HEAD"],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            return None, False
        return (proc.stdout.strip() or None), True
    ref = f"refs/heads/{branch}"
    shown = subprocess.run(["git", "-C", repo, "show-ref", "--verify", "--quiet", ref],
                           capture_output=True, text=True, check=False)
    if shown.returncode == 1:
        return None, True                         # 分支不存在：确实没有提交可丢
    if shown.returncode != 0:
        return None, False                        # 探针坏了：不可判定
    proc = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", ref],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None, False
    return (proc.stdout.strip() or None), True


def _branch_tip(repo: str, branch: str) -> str | None:
    """分支 ref 的 tip sha；分支不存在 → `None`（用于 `residual` 可见性，非安全判据）。"""
    proc = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                           f"refs/heads/{branch}"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _refs_containing(repo: str, sha: str) -> set[str] | None:
    """含 `sha` 的**全名** ref 集合（heads+remotes）；探针失败 → `None`（不可判定）。"""
    proc = subprocess.run(["git", "-C", repo, "for-each-ref",
                           f"--contains={sha}", "--format=%(refname)",
                           "refs/heads", "refs/remotes"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return None
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _is_own_remote_ref(refname: str, own_branch: str) -> bool:
    """`refs/remotes/<remote>/<own_branch>` 是否就是节点自己推上去的那一份（全名精确匹配）。

    remote 段不含 `/`。用**全名**而非短名：短名会被 git 加前缀（`heads/...`）或在远端名
    恰为 `disflow` 时与分支名歧义，令 `discard` 打空 → 守卫 fail-open（FIX3-R1 根因）。
    """
    prefix = "refs/remotes/"
    if not refname.startswith(prefix):
        return False
    remote, _, branch = refname[len(prefix):].partition("/")
    return bool(remote) and branch == own_branch


def _commit_on_other_ref(repo: str, sha: str, own_branch: str) -> bool | None:
    """`sha` 能否从**别的** ref 到达？三态：`True` / `False` / `None`（不可判定）。

    - `True`：确证某个别处 ref（排除该节点自己的 `refs/heads/<own_branch>` 与
      `refs/remotes/*/<own_branch>`）含该提交 → 删除后仍可达，**放行**。
    - `False`：确证只有该节点自己的 ref 含它 → 删除即丢失，**拒绝**。
    - `None`：`for-each-ref` 探针失败 → 不可判定，调用方**必须当拒绝**（FIX3-R2）。

    **fail-closed by construction**：放行必须有正面证明（确证别处可达）；任何不可判定、
    任何让集合意外变大的意外，方向只能是**更严**——绝不因为「减掉自己后剩空」而默认安全。
    排除用**全名精确匹配**（见 `_is_own_remote_ref`），不用短名拼接。

    **过拒面（有意保守）**：只被 tag / `refs/stash` / 别的 worktree 的 detached HEAD 持有的
    提交会被过拒（这些 ref 不在 heads/remotes 里）。特别地，节点分支 **push 到 origin 之后
    仍会被拒**（`origin/<own_branch>` 被排除）——「已 push、待评审、未合并」的节点会落在
    `discard_warnings` 里，由人判断是否绕开。**提醒**：别因为嫌烦就一律强删，那会把这道闸门
    变成装饰。
    """
    refs = _refs_containing(repo, sha)
    if refs is None:
        return None
    own = {f"refs/heads/{own_branch}"}
    others = {r for r in refs
              if r not in own and not _is_own_remote_ref(r, own_branch)}
    return bool(others)


def _plan_entry(storage: dict, tree: str, node: str, repo: str, *,
                path: str, branch: str, status: str,
                remove_cmd: list[str] | None) -> dict:
    """给一个待回收对象（leak 或 residual）产出一条**纯计划**：判据 + 命令 + 风险。

    leaks 与 residual **共用这一个漏斗**——判据一律来自 `_inspect`（同一套理由词表），故任何
    带未合并提交 / 脏改动 / ignored 文件的条目都必须 `safe_to_drop=False`，没有第二条路径。
    `remove_cmd` 为 `None`（residual：没有 worktree 可删）时仍给 `branch_cmd`。
    """
    live = _inspect(storage, tree, node, repo)
    reasons = live["reasons"]
    safe = not reasons
    entry = {
        "node": node, "path": path, "branch": branch, "status": status,
        "head": live["head"], "reasons": reasons, "safe_to_drop": safe,
        "remove_cmd": remove_cmd, "branch_cmd": _branch_delete_cmd(repo, branch),
    }
    if not safe:
        entry["discard_warnings"] = _discard_warnings(live)
    return entry


def _discard_warnings(live: dict) -> list[str]:
    """把 `_inspect` 的理由翻成「宿主强删这条会丢什么」的人话（逐条）。"""
    warnings: list[str] = []
    for reason in live["reasons"]:
        kind, _, detail = reason.partition(":")
        if kind == "dirty":
            # 已修改/未跟踪会让 `git worktree remove` 拒绝（实测 rc=128）——这一半成立。
            warnings.append("未提交的改动（已修改或未跟踪的文件）会丢失"
                            "（git 会拒绝，需 `--force` 才能删）")
        elif kind == "ignored":
            # **只说真成立的那一半**：ignored 文件确实会丢，但 git **不**因它拒绝删除
            # （实测：只含 ignored 文件的 worktree，不带 `--force` 的 remove 也 exit 0）。
            # 把它们说成「git 会拒绝、需 --force」是假警报，会让 --force 变成日常操作、
            # 真需要它时没人当回事。
            warnings.append(f"被忽略的文件 {detail} 会丢失"
                            "（git 不因 ignored 文件拒绝删除：这一条无需 `--force`）")
        elif kind == "unmerged-commit":
            warnings.append(f"提交 {detail} 将不可达、永久丢失（分支需 `-D` 才能删）")
        elif kind.startswith("unreadable"):
            warnings.append(f"无法判定、按会丢处理：{reason}")
        else:
            warnings.append(reason)
    return warnings


def _branch_delete_cmd(repo: str, branch: str) -> list[str]:
    """分支删除命令：**一律 `-d`（安全删）**——未合并分支会被 git 响亮拒绝。

    `-D` 强删会让那笔提交不可达，正是本模块要根除的数据丢失路径，故绝不打印；宿主确要丢弃
    就自己改成 `-D`，并自行承担。
    """
    return ["git", "-C", repo, "branch", "-d", branch]


# ── 内部：状态读取与路径校验 ──────────────────────────────────────────────


def _all_trees(storage: dict) -> dict:
    """投影里的全部树（`{name: tree}`）；投影缺失 → 空 dict。"""
    return store.read_json(store.projection_path(storage)).get("trees", {}) or {}


def _nodes(storage: dict, tree: str, node: str | None = None) -> dict:
    """取某树的节点表；`node` 给定则额外校验该节点存在。两者不存在都**响亮报错**。"""
    trees = _all_trees(storage)
    if tree not in trees:
        raise KeyError(f"树不存在：{tree}（现有：{sorted(trees)}）")
    nodes = trees[tree].get("nodes") or {}
    if node is not None and node not in nodes:
        raise KeyError(f"节点不存在：{node}（树 {tree}）")
    return nodes


def _is_live_task(item: dict) -> bool:
    """**活** task 节点：pending/running（可回收状态不算活）。

    与 `ready.live_tasks`（`status not in TERMINAL`）**有意**不同：failed 已停，其 worktree
    由 `reap` 回收，不应对它再发 `add`——否则 `reap` 刚删、`apply` 又建，循环打架。
    """
    return item.get("kind") == "task" and item.get("status") not in RECLAIMABLE


def _is_real_worktree(path: str) -> bool:
    """目录是否是一个真 worktree：linked worktree 在根下有一个 `.git` 文件/目录。"""
    return os.path.isdir(path) and os.path.exists(os.path.join(path, ".git"))


def _broken_reason(exists: bool, is_reg: bool, branch_exists: bool) -> str:
    parts = []
    if exists and not is_reg:
        parts.append("目录存在但未注册为 worktree")
    elif exists and is_reg:
        parts.append("目录存在且已注册但缺少 .git（不是完整 worktree）")
    if branch_exists and not (exists or is_reg):
        parts.append("分支已存在但无对应 worktree（残留分支）")
    return "；".join(parts) or "半创建状态"


def _branch_exists(repo: str, branch: str) -> bool:
    """分支是否已存在（只读探针）。"""
    proc = subprocess.run(["git", "-C", repo, "show-ref", "--verify", "--quiet",
                           f"refs/heads/{branch}"],
                          capture_output=True, text=True, check=False)
    return proc.returncode == 0


def _prune_own(storage: dict, repo: str) -> list[str]:
    """清掉 **`root(storage)` 命名空间下**、目录已不在了的 worktree 登记（逐条），返回被清的路径。

    绝不全局 `git worktree prune`：那会把别人只是「暂时不在」（如卷未挂载）的登记一起清掉，
    卷回来后那棵 worktree 永久坏掉（FIX2-B6）。`git worktree remove` 对目录已不在的登记会
    连登记一起清掉；不 `--force`，故 locked 的登记不动。
    """
    pruned: list[str] = []
    for path in sorted(registered(repo)):
        if not _under(path, root(storage), base=repo):
            continue
        abs_path = path if os.path.isabs(path) else os.path.join(repo, path)
        if os.path.isdir(abs_path):
            continue
        proc = subprocess.run(["git", "-C", repo, "worktree", "remove", path],
                              capture_output=True, text=True, check=False)
        if proc.returncode == 0:
            pruned.append(path)
    return pruned


def _repo_path(storage: dict, tree: str, node: str, repo: str) -> str:
    """`path_for` 的**绝对**形式，供文件系统 IO 用：相对 state_dir 以 repo 为基准解析。

    `os.path.isdir` 等按**进程 cwd** 解析相对路径，两个 cwd 会得出不同结论（一个 cwd 下
    整棵树「消失」）。所有 IO 一律走这里，守卫与读数才真正跨 cwd 一致（FIX2-B10）。
    """
    path = path_for(storage, tree, node)
    return path if os.path.isabs(path) else os.path.join(repo, path)


def _under(path: str, parent: str, *, base: str | None = None) -> bool:
    """path 是否在 parent 目录之下（按 realpath 归一，绕过符号链接）。

    `base` 给定时，相对路径以它为基准解析——守卫因此不依赖进程 cwd。
    """
    p, b = _norm(path, base), _norm(parent, base)
    return p == b or p.startswith(b + os.sep)


def _reject_if_inside(path: str, repo: str) -> None:
    """约束 3：worktree 路径绝不能在 repo 内（否则污染 `git status` 与证据门）。

    相对路径**一律以 repo 为基准**解析，两个不同 cwd 下判定必须一致。
    仓内是脆弱配置（`git clean -x` 会连状态目录一起删），故硬拒并给可操作建议。
    """
    if _under(path, repo, base=repo):
        raise ValueError(
            f"worktree 路径落在 repo 内部：{path}（repo={repo}）"
            "——会污染 git status，且 `git clean -xdf` 会把状态目录连同 worktree 一起删掉；"
            "请把 [state].dir（或 DISFLOW_STATE_DIR）配到仓外")


def _norm(path: str, base: str | None = None) -> str:
    """路径归一（realpath）：`git worktree list` 与 state_dir 可能一个走符号链接、一个不走，
    不归一就会把同一个目录判成两个。`base` 给定时先以它为基准解析相对路径。"""
    if base is not None and not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.realpath(path)
