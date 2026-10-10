"""disflow · 引擎（动词面）。

所有写操作走同一条路径：**取锁 → 读投影 → 校验 → append 事件（内部重算投影）**。
读操作只读投影。引擎不 spawn、不常驻。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from . import config, gates, graph, store, worktree
from .config import Config
from .core import RUNNING
from .dispatch import dispatch_plan, handoff_brief, result_contract, validate_result
from .dispatch import drive as run_drive
from .metrics import parse_deadline
from .model import validate_path_name
from .ready import (
    blocked_by,
    is_stuck,
    lease_expired,
    progress,
    ready_tasks,
    running_tasks,
)
from .spec import SPEC
from .state import project


def _make_lease(holder: str, ttl: int) -> dict:
    """带有效期的工作租约（`reap` 据此判过期）。"""
    now = datetime.now(timezone.utc)
    return {
        "holder": holder or "unknown",
        "at": now.isoformat(timespec="microseconds"),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(timespec="microseconds"),
    }


# 收尾态：`stage` 不可改（与「终态不可重开」一致）。
# `failed` 也在此列——`core.TERMINAL` 只含 done/skipped，但 failed 已是**停下的**节点
# （见 `worktree.RECLAIMABLE` = TERMINAL + ("failed",)），在收尾记录上悄悄改列同样是
# 改写历史；要改先把节点放回活态（failed 走 `retry`），别在残局上空手改视图。
_STAGE_FROZEN: tuple[str, ...] = ("done", "skipped", "failed")


# 提问纪律（`host-next` 的 brief 片段里写明）：worker 的提问天然走**宿主通道**
# （pi 里即 `contact_supervisor`），而该通道不进 disflow 的事件流 ⇒ 树上看不到这次提问、
# 追溯缺一块。故**两条都要做**：走宿主通道提问，**且**在 `collect` 的结果里把 `status` 置
# `blocked`（事件流留痕）。只做前者 = disflow 树上看不见；只做后者 = 宿主收不到。
QUESTION_PROTOCOL = (
    "worker 有疑问时**两条都要做**：① 走宿主通道提问（pi 里即 contact_supervisor，"
    "reason=\"need_decision\"）——问题才会到宿主手里；② **同时**在 `collect --result` 里把问题"
    "写进 summary 且 `status` 置 \"blocked\"——disflow 事件流才留得下痕（缺②则树上查不到这次"
    "提问）。两条都不做 = 自己拍板改范围，禁止。"
)


def _deferred_text(deferred: list[dict]) -> str:
    """把 `drive` 的 `deferred` 条目渲染成一行可读的**点名**（节点 ← 在跑的谁 + 重叠路径）。

    数据**与 `drive` 同源**（同一份 `deferred`、同一个 `why` 字符串，见 `dispatch.drive`），此处
    只负责拼成人读的话——**绝不**在 `host_next` 里重算写面冲突（两面各算一遍正是漂移的温床）。
    """
    return "；".join(f"{entry['node']}（{entry['why']}）" for entry in deferred)


def _dispatch_state(result: dict) -> tuple[str, str]:
    """无就绪节点时的**显式**说明（不许静默返回空）：全部完成 / 卡住 / 等写面 / 在跑 / 有失败。

    就绪波次非空 → `ready`。返回 `(state, message)`，`state ∈ {ready, waiting, complete, stuck,
    running, failed}`，`message` 是人能读的一句话。判据取自 `drive` 的同一份读数（`waves` /
    `deferred` / `stuck` / `running` / `progress`），两面不分叉。

    `waiting`：`ready_wave` 为空**是因为**所有就绪候选都被在跑节点的写面挡下（`deferred` 非空），
    ——**是在跑**，不是 `stuck`（`stuck` 的语义是「没就绪、没在跑、但还有 pending」；把「被在跑
    节点挡住」混进 `stuck` 会把两种完全不同的病因说成同一件事）。故单列一个取值，且**绝不**在
    这一格报 `ready`——`ready` + 空 `plan` 自相矛盾，读者会以为没活干。
    """
    waves = result["waves"]
    deferred = result.get("deferred") or []
    if waves:
        total = sum(len(wave) for wave in waves)
        message = (f"就绪 {total} 个（{len(waves)} 波，首波 {len(waves[0])} 个）："
                   "下面每个节点的 brief 即 worker 的 task")
        if deferred:
            message += f"；另有被在跑节点挡下（本轮不派）：{_deferred_text(deferred)}"
        return "ready", message
    if deferred:
        return "waiting", ("就绪节点全被在跑节点的写面挡下（本轮不派，等它们收尾）："
                           + _deferred_text(deferred))
    progress = result["progress"]
    if progress["done"] == progress["tasks"]:
        if progress["tasks"] == 0:
            return "complete", "树内无任务节点，无就绪节点"
        return "complete", (f"全部完成：{progress['done']}/{progress['tasks']} 节点已终态，"
                            "无就绪节点")
    if result["stuck"]:
        return "stuck", (f"卡住（stuck）：还有 pending 节点，但既无就绪也无运行——"
                         f"被阻塞 {len(result['blocked'])} 个：{result['blocked']}")
    if result["running"]:
        return "running", (f"无就绪节点，但有 {len(result['running'])} 个运行中——"
                           f"等 {', '.join(result['running'])} 收尾")
    return "failed", "无就绪节点：有 failed 节点待 retry / skip（failed 不算 stuck）"


class EngineError(Exception):
    """引擎级错误（用法 / 依赖 / 状态非法）。"""


class GateReject(EngineError):
    """门拒收：带缺项清单 + 契约片段。"""

    def __init__(self, missing: list[str], contract: dict):
        super().__init__("门拒收：" + "；".join(missing))
        self.missing = missing
        self.contract = contract


class Engine:
    def __init__(self, spec: dict | None = None, cwd: str | None = None,
                 cfg: Config | None = None):
        self.spec = spec or SPEC
        self.cwd = cwd or os.getcwd()
        # 配置从 `<cwd>/disflow.json` 读（可用 `$DISFLOW_CONFIG` 指别处）；缺文件 = 全用内建默认。
        # 显式传入 `cfg` 用于测试与“别处已解析好”的场景。
        self.cfg = cfg if cfg is not None else config.load(self.cwd)

    # ── 读 ────────────────────────────────────────────────────────────────

    def load(self) -> dict:
        return store.read_json(store.projection_path(self.spec))

    def trees(self) -> list[str]:
        return sorted(self.load().get("trees", {}))

    def tree(self, name: str, state: dict | None = None) -> dict:
        state = self.load() if state is None else state
        tree = state.get("trees", {}).get(name)
        if tree is None:
            raise EngineError(f"树不存在：{name}（现有：{self.trees()}）")
        return tree

    @staticmethod
    def _closed(state: dict) -> set[str]:
        """已关闭的树集合（跨树完成信号 `awaits` 的判据）。"""
        return {name for name, tree in (state.get("trees") or {}).items()
                if tree.get("closed")}

    def closed_trees(self) -> set[str]:
        return self._closed(self.load())

    @staticmethod
    def _need(tree: dict, node_id: str) -> dict:
        node = tree["nodes"].get(node_id)
        if node is None:
            raise EngineError(f"节点不存在：{tree.get('root')}/{node_id}")
        return node

    # ── 写 ────────────────────────────────────────────────────────────────

    def _write(self, kind: str, *, tree: str, node: str = "", prepare=None,
               guard=None, guard_state=None, existing: bool = True,
               dry_run: bool = False) -> dict:
        """唯一写点：锁内 读 → 校验 → append。`prepare(tree)` 返回事件的 extra 字段。

        `guard(tree)` 只看本树；`guard_state(tree, state)` 还能看到全局（跨树信号）。
        `dry_run=True`：**跑完全部门与 prepare，但不追加事件**（预演，FR-13）；
        门不过依旧抛 `GateReject` —— 预演的意义就是把拒收提前暴露。
        """
        with store.lock(self.spec):
            state = self.load()
            current = state.get("trees", {}).get(tree)
            if existing and current is None:
                raise EngineError(f"树不存在：{tree}")
            if not existing and current is not None:
                raise EngineError(f"树已存在：{tree}")
            if current is not None:
                if guard is not None:
                    guard(current)
                if guard_state is not None:
                    guard_state(current, state)
            extra = prepare(current) if prepare is not None else {}
            if dry_run:
                return {"dry_run": True, "kind": kind, "tree": tree, "node": node,
                        "extra": extra or {}, "state": state}
            store.append_event(self.spec, kind, project=project, slug=tree,
                               node=node, extra=extra or {})
        return self.load()

    # ── 树 ────────────────────────────────────────────────────────────────

    def init_tree(self, tree: str, goal: str) -> dict:
        # 写时闸门：树名会构成 worktree 路径段，路径形名字一律拒收（不落事件）。
        validate_path_name(tree, what="树名")
        return self._write("tree.init", tree=tree, existing=False,
                           prepare=lambda _: {"goal": goal})

    def set_goal(self, tree: str, goal: str) -> dict:
        return self._write("tree.goal", tree=tree, prepare=lambda _: {"goal": goal})

    def deadline(self, tree: str, value: str | None) -> dict:
        """设树级 `deadline`（迭代边界）：日期串归一到 `YYYY-MM-DD`，`None`/`""`/`"-"` 清空。

        值在写时经 `parse_deadline` 归一——非法日期**响亮报错、不落事件**，
        与其它写动词同款契约（静默丢弃会让逾期永远发现不了）。
        """
        return self._write("tree.deadline", tree=tree,
                           prepare=lambda _: {"deadline": parse_deadline(value)})

    # ── 节点与图 ──────────────────────────────────────────────────────────

    def _validate_stage(self, node_id: str, stage: object) -> None:
        """`stage` 取值校验——**写时**，用解析后的配置（闭集）。

        判据与 `_validate_vocabulary` 对 `stage` 的那一条**同款同形**：取值必须在
        `cfg.stages` 内，否则响亮拒收并报出允许的取值。`set_stage`（改列）与 `expand`
        （建节点）必须用同一套判据，否则错别字能绕过改列那道口子（建时拦得住、改时放进来）。
        """
        if str(stage) not in self.cfg.stages:
            raise EngineError(
                f"{node_id}: stage 取值非法：{stage!r}（允许：{list(self.cfg.stages)}）"
                "——自定义看板列请写进 disflow.json 的 \"stages\"")

    def _validate_vocabulary(self, children: list[dict]) -> None:
        """`stage` / `role` 取值校验——**写时**，用解析后的配置。

        为何只在写时、不在 reducer 里校验：reducer 一依赖配置，事件流就**不可重放**了
        ——换台机器或改了 `stages`，重放旧事件会直接失败。所以分两处：
          - **写时**（这里）：挡住错别字，报出允许的取值；
          - **读时**（`views.py`）：对未知 stage **容忍**（`order += sorted(...)`）——
            事件流可能来自更新的版本 / 不同的配置，视图不能崩。
        之前两边都没有：`stage: "audti"` 会静默多出一个看板列。
        """
        for child in children:
            node_id = child.get("id", "?")
            stage = child.get("stage")
            if stage is not None and str(stage) not in self.cfg.stages:
                raise EngineError(
                    f"{node_id}: stage 取值非法：{stage!r}（允许：{list(self.cfg.stages)}）"
                    "——自定义看板列请写进 disflow.json 的 \"stages\"")
            role = child.get("role")
            if role is not None and str(role) not in self.cfg.roles:
                raise EngineError(
                    f"{node_id}: role 取值非法：{role!r}（允许：{list(self.cfg.roles)}）"
                    "——自定义负责方请写进 disflow.json 的 \"roles\"")

    def expand(self, tree: str, parent: str, children: list[dict], *, holder: str = "") -> dict:
        """在 `parent` 下挂子节点。

        `holder` 是「谁挂的」——`start` 早就有 `--holder` 可追溯「谁在干」，而 `expand`
        一直没有，于是树长了任务却查不到是谁挂的。补上（可选，不填就为空字符串）。

        **已关闭的树拒收**：`closed` 是跳树完成信号（`awaits` 读它）。允许在关闭后继续挂
        任务 = 让该信号失真而不自知（消费方会基于一个未真正完成的上游开工）。要加就先
        显式重开（`disflow reopen`）——报错文本直接给出该命令，不让人猜。
        """
        def guard(t: dict) -> None:
            self._need(t, parent)
            if t.get("closed"):
                raise EngineError(
                    f"{tree} 已关闭（tree.close）：在关闭后挂任务会让 awaits 的完成信号失真——\n"
                    f"  要接着干就先显式重开：disflow reopen {tree} --reason '<为何重开>'")
            self._validate_vocabulary(children)
        return self._write("node.expand", tree=tree, node=parent, guard=guard,
                           prepare=lambda _: {"children": children, "holder": holder})

    def require(self, tree: str, node: str, dep: str) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            self._need(t, dep)
            if node == dep:
                raise EngineError(f"{node} 不能依赖自身")
            if graph.would_cycle(t["nodes"], node, dep):
                raise EngineError(f"加边即成环：{node} requires {dep}")
        return self._write("node.require", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"dep": dep})

    def unrequire(self, tree: str, node: str, dep: str) -> dict:
        """删 `requires` 边 —— 建错依赖时的合法出口（否则只能 `skip` 整个节点）。

        与 `require` **故意不对称**：加边要求两端都存在（不许造悬空边）；
        删边不要求 `dep` 节点存在——否则悬空边反而清不掉（`doctor` 报得出、没人修得了）。

        不带 `--dry-run`：本动词只做**存在性**校验，不跑门。
        厂内规则：跑门的写动词（`start`/`done`/`close`/`drive`）才配 `--dry-run`。
        """
        def guard(t: dict) -> None:
            item = self._need(t, node)
            current = list(item.get("requires") or [])
            if dep not in current:
                raise EngineError(f"{node} 没有 requires {dep}（现有：{current}）")
        return self._write("node.unrequire", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"dep": dep})

    def relate(self, tree: str, node: str, other: str) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            self._need(t, other)
        return self._write("node.relate", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"other": other})

    def unrelate(self, tree: str, node: str, other: str) -> dict:
        """删 `relates` 边 —— 与 `unrequire` 同形状、同理由。

        `relate` 能建不能删是**同一个缺口家族**：`unrequire` 修之前，建错依赖只能 `skip`
        整个节点。信息边不参与就绪判定，所以优先级低——但“建错了改不了”仍然只能靠 `skip`。

        与 `relate` **故意不对称**：加边要求两端存在；删边只要求边存在。
        不带 `--dry-run`（只做存在性校验，不跑门）。
        """
        def guard(t: dict) -> None:
            item = self._need(t, node)
            current = list(item.get("relates") or [])
            if other not in current:
                raise EngineError(f"{node} 没有 relates {other}（现有：{current}）")
        return self._write("node.unrelate", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"other": other})

    def produce(self, tree: str, node: str, name: str) -> dict:
        return self._write("asset.produce", tree=tree, node=node,
                           guard=lambda t: self._need(t, node),
                           prepare=lambda _: {"name": name})

    def consume(self, tree: str, node: str, name: str) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            if name not in t["assets"]:
                raise EngineError(f"consumes 指向未登记产出：{name}"
                                  f"（已登记：{sorted(t['assets'])}）")
        return self._write("asset.consume", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"name": name})

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def _gate_cwd(self, tree: str, node: str) -> str:
        """门的**唯一目录源**：该节点有**真** worktree → 其绝对路径；否则 `self.cwd`。

        为何需要它：`disflow worktree apply <tree>` 把节点工作区建在
        `<state_dir>/worktrees/<tree>/<node>`（见 `worktree.root` / `path_for`），而门的所有 IO
        （验收命令、`git status`、内容指纹）原本都吃 `self.cwd`（进程 cwd）。用 worktree 干活时
        两者不相交 ⇒ 门在错的目录上跑、改动看不见 ⇒ **空洞通过（fail-open）**：worker 删了
        要求存在的文件并提交，门仍报 `introduced=[]`、`changed_files=[]`、退出码 0。

        worktree 是**派生资源**（不落状态），故此处只做现实对账：`worktree.status` 的 `real`
        为真才算数。树/节点不存在、探针失败、路径落在 repo 内（脆弱配置）一律退回 `self.cwd`
        —— 与「没有 worktree」同路，**不抛**（门的目录解析不能比门本身更容易炸）。
        """
        try:
            present = worktree.status(self.spec, tree, node, self.cwd)
        except (KeyError, worktree.WorktreeError, ValueError):
            return self.cwd
        if not present.get("real"):
            return self.cwd
        path = str(present["path"])
        return path if os.path.isabs(path) else os.path.join(self.cwd, path)

    def _check_reported_paths(self, tree: str, node: str, result: dict) -> None:
        """`collect` 的低优先防串味：自报 `changed_files` 必须落在本节点 worktree 下。

        真相由门自算（`introduced`），这条只拦明显的写错目录：绝对路径要落在本节点
        worktree 下，相对路径**按 worktree 解析**。无 worktree 时跳过。
        """
        cwd = self._gate_cwd(tree, node)
        if cwd == self.cwd:
            return
        root = os.path.realpath(cwd)
        files = result.get("changed_files")
        if not isinstance(files, list):
            return
        for entry in files:
            if not isinstance(entry, str) or not entry.strip():
                continue
            target = entry if os.path.isabs(entry) else os.path.join(root, entry)
            resolved = os.path.realpath(target)
            if resolved != root and not resolved.startswith(root + os.sep):
                raise EngineError(
                    f"{node} 自报的 changed_files 越出本节点 worktree：{entry!r}"
                    f"（worktree={root}）——只报该目录下的路径；门的读数以 worktree 为准")

    def start(self, tree: str, node: str, holder: str = "",
              ttl: int | None = None, dry_run: bool = False) -> dict:
        lease = _make_lease(holder, self.cfg.lease_ttl if ttl is None else ttl)

        def guard(t: dict, state: dict) -> None:
            item = self._need(t, node)
            if item.get("kind") != "task":
                raise EngineError(f"{node} 是容器，不派发")
            if item.get("status") != "pending":
                raise EngineError(f"{node} 状态为 {item.get('status')}，不可 start")
            blockers = blocked_by(t["nodes"], node, self._closed(state))
            if blockers:
                raise EngineError(f"{node} 未就绪，被阻塞：{blockers}")

        def prepare(_: dict) -> dict:
            cwd = self._gate_cwd(tree, node)
            # `workspace` 记下开工现场：`done` 据此把门跑在同一目录上，并在「起点与终点不是
            # 同一个目录」时 fail-closed 拒收。`head_sha` **一律**记录（worktree 与原地派发
            # 都一样）——worker 可能把活干完就 `git commit`，只看 `git status` 会看不见已提交
            # 的改动 ⇒ 门空洞通过。
            return {"lease": lease,
                    "baseline": gates.snapshot(cwd, self.cfg),
                    "workspace": {"cwd": cwd,
                                  "head_sha": gates.head_sha(cwd, self.cfg),
                                  "worktree": cwd != self.cwd}}
        return self._write("node.start", tree=tree, node=node, guard_state=guard,
                           prepare=prepare, dry_run=dry_run)

    def collect(self, tree: str, node: str, result: dict) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            missing = validate_result(result_contract(tree, t, node), result)
            if missing:
                raise EngineError("结果契约不符：" + "；".join(missing))
            self._check_reported_paths(tree, node, result)
        return self._write("node.collect", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"result": result})

    def done(self, tree: str, node: str, declarations: dict | None = None,
             dry_run: bool = False, no_result: str = "") -> dict:
        """跑门 + 落 `node.done`。

        `no_result` 是**破例理由**（对应 CLI 的 `--no-result`）：节点确实没有 worker 结果时
        显式放行，理由落进证据（`evidence.no_result`）——破例可见，绝不静默。引擎层**不**
        因为缺 collect 而拒收（那是 CLI 层的宿主循环契约，见 `cli.cmd_done`）：引擎是机制，
        无 collect 的 `done` 是合法组合；但每次 `node.done` 都记账 `evidence.result_collected`，
        树的读者永远能看出「这个节点没有 worker 结果」。
        """
        def prepare(t: dict) -> dict:
            item = self._need(t, node)
            collected = item.get("collected")
            reason = str(no_result or "").strip()
            if reason and collected is not None:
                raise EngineError(
                    f"{node} 已有 collect 结果，--no-result 无意义（只在该节点无结果时才需要破例）")
            # 门的**所有** IO 统一走同一个目录（有真 worktree 就在 worktree 里，否则 self.cwd）。
            cwd = self._gate_cwd(tree, node)
            workspace = item.get("workspace") or {}
            now_worktree = cwd != self.cwd
            # fail-closed（第一判据，**对称**）：以 `start` 记录的 `workspace` 为**权威**，与
            # 「现在解析出的工作区」比对，任一方向不一致 ⇒ 响亮拒收而不放行。
            # 判据必须取自**记录**而非从现状反推：worktree 在 `done` 之前被删时 `_gate_cwd`
            # 退回 `self.cwd`，从现状反推会得出「没有 worktree」从而整段跳过——门退回主工作区、
            # base_sha=None，又一次空洞通过（fail-open）。无 `workspace` 的旧事件不校验。
            if workspace:
                recorded_worktree = bool(workspace.get("worktree"))
                recorded_cwd = str(workspace.get("cwd") or "")

                def baseline_elsewhere() -> EngineError:
                    return EngineError(
                        f"{node} 的开工基线取自 {recorded_cwd!r}，而本节点现有 worktree "
                        f"{cwd!r}——基线不在门的目录里，门会空洞通过。\n"
                        f"  补救：disflow reopen {tree} --reason '<为何重开>' 后重新 start"
                        f"（先 disflow retry {tree} {node} 把该节点放回 pending）。")

                if recorded_worktree and not now_worktree:
                    raise EngineError(
                        f"{node} 的开工工作区已不存在：`start` 记下 worktree=True，而此刻解析不到"
                        f"真 worktree（门的目录退回主工作区 {self.cwd!r}）——门会在主工作区上跑，"
                        f"看不见该节点 worktree 里的改动，从而空洞通过。\n"
                        f"  补救：重建该节点 worktree（disflow worktree apply {tree}）后重新 start；"
                        f"或 disflow reopen {tree} --reason '<为何重开>' 后 retry + start。")
                if not recorded_worktree and now_worktree:
                    raise baseline_elsewhere()
                if recorded_worktree and now_worktree and (
                        not recorded_cwd
                        # 两侧都经 realpath 归一：`self.cwd` 与 state_dir 的符号链接前缀可能
                        # 不同形（/private/var/… 与 /var/…），裸字符串比对会**误拒**（假红）。
                        or os.path.realpath(recorded_cwd) != os.path.realpath(cwd)):
                    raise baseline_elsewhere()
            # **一律**把 `start` 记下的 head_sha 交给门：原地派发的 worker 同样可能自己
            # `git commit`，此时只读 `git status --porcelain` 是空的 ⇒ introduced/changed_files
            # 为空 ⇒ 空洞通过（fail-open）。worktree 与原地两条路必须走同一条判据。
            # （旧事件没有 `workspace` 时取到 None，退回只读 `git status`，不炸。）
            base_sha = workspace.get("head_sha")
            ok, why, evidence = gates.evidence_meta(item.get("acceptance"), cwd,
                                                   self.cfg, base_sha=base_sha)
            baseline = item.get("baseline") or {}
            # 只归因「开工后**内容真的变了**」的改动：既忽略开工前的脏文件，
            # 也逮得住「同文件被前一节点弄脏、本节点又改」这种差集看不见的情况
            introduced = gates.newly_changed(cwd, baseline, self.cfg, base_sha=base_sha)
            scope = gates.scope_check(item.get("touch_plan") or [], introduced)
            # 规模读数同样吃 `start` 记的 head_sha：worker 已提交时 `git diff HEAD` 恒为 0，
            # 不复用 base_sha ⇒ introduced 非空而 size.total=0、vacuous=[size_budget]（宿主实测）。
            size = gates.diff_size(cwd, introduced, self.cfg, base_sha=base_sha)
            budget = gates.resolve_budget(item, self.cfg)
            evidence["baseline"] = sorted(baseline)
            evidence["introduced"] = introduced
            evidence["baseline_method"] = "content-sha256"
            evidence["scope"] = scope
            evidence["size"] = size
            evidence["budget"] = budget
            # 空洞通过的门（没东西可验）：记下来，否则“门全过”会被读成“门都验了”
            evidence["vacuous"] = gates.vacuous_reasons(
                acceptance=item.get("acceptance"), touch_plan=item.get("touch_plan") or [],
                introduced=introduced, size=size)
            # worker 结果的记账（与门的通过/拒收**无关**）：CLI 用它强制宿主循环契约
            # （无 collect ⇒ 拒收），树的读者用它看出「这个节点没 worker 结果」。
            evidence["result_collected"] = collected is not None
            if reason:
                evidence["no_result"] = reason
            # 破例必须**记入证据**（显式、可审计），而不是默默放行
            for key in ("scope_waiver", "size_waiver"):
                waiver = (declarations or {}).get(key)
                if waiver:
                    evidence[key] = waiver
            contract = {"node": node, "acceptance": item.get("acceptance"),
                        "touch_plan": item.get("touch_plan"), "size_budget": budget,
                        "expect": "验收命令通过；改动落在 touch_plan 内；改动行数不超预算"}
            missing: list[str] = []
            if not ok:
                missing.append(f"验收未通过：{why}")
            required = (item.get("acceptance") or {}).get("require_declarations") or []
            absent = [name for name in required
                      if not str((declarations or {}).get(name) or "").strip()]
            if absent:
                missing.append(f"缺必填声明：{absent}")
            if scope["checked"] and scope["violations"] and \
                    not (declarations or {}).get("scope_waiver"):
                missing.append(f"改动超出 touch_plan：{scope['violations']}（归属错了就 rescope，"
                               f"已动手则声明 scope_waiver 破例）")
            if (size is not None and size["total"] > budget
                    and not (declarations or {}).get("size_waiver")):
                missing.append(f"规模超预算：{size['total']} 行 > {budget} 行"
                               f"（可声明 size_waiver 破例）")
            if missing:
                raise GateReject(missing, contract)
            return {"evidence": evidence, "declarations": declarations or {}}
        return self._write("node.done", tree=tree, node=node, prepare=prepare,
                           dry_run=dry_run)

    def fail(self, tree: str, node: str, reason: str) -> dict:
        return self._write("node.fail", tree=tree, node=node,
                           guard=lambda t: self._need(t, node),
                           prepare=lambda _: {"reason": reason})

    def retry(self, tree: str, node: str) -> dict:
        return self._write("node.retry", tree=tree, node=node,
                           guard=lambda t: self._need(t, node))

    def skip(self, tree: str, node: str, reason: str = "") -> dict:
        return self._write("node.skip", tree=tree, node=node,
                           guard=lambda t: self._need(t, node),
                           prepare=lambda _: {"reason": reason})

    def reap(self, tree: str) -> list[str]:
        """回收**过期**租约（显式事件，不改 `ready()` 的纯度）。返回被回收的节点 id。

        worker 崩溃留下的 `running` 节点会一直占着节点；`reap` 把它放回 pending，
        并记下回收原因（可审计）。“过期”依据租约自带的 `expires_at`。
        """
        now_ts = time.time()
        target = self.tree(tree)
        stale = sorted(
            node_id for node_id, node in target["nodes"].items()
            if node.get("status") == "running"
            and lease_expired(node.get("lease"), now_ts))
        for node_id in stale:
            holder = (target["nodes"][node_id].get("lease") or {}).get("holder", "")
            extra = {"reason": f"租约超时（holder={holder}）"}
            self._write("node.reclaim", tree=tree, node=node_id,
                        prepare=lambda _t, payload=extra: dict(payload))
        return stale

    def import_node(self, tree: str, node: str, status: str, note: str = "") -> dict:
        """导入既有状态（不可经验收门直接构造 done）——显式标记 imported。"""
        return self._write("node.import", tree=tree, node=node,
                           guard=lambda t: self._need(t, node),
                           prepare=lambda _: {"status": status, "note": note})

    def annotate(self, tree: str, node: str, context: dict) -> dict:
        """合并节点 `context`（供后代继承；树根用它声明 `closure`）。"""
        return self._write("node.context", tree=tree, node=node,
                           guard=lambda t: self._need(t, node),
                           prepare=lambda _: {"context": context})

    def set_stage(self, tree: str, node: str, stage: str, *,
                  reason: str = "", holder: str = "") -> dict:
        """改某节点的看板列（`stage`）——与 `rescope` 同形状的「建好之后能改」补口。

        **为何需要**：`stage` 只在建节点（`expand`）与 mgt 导入时写一次，34 个动词里没有
        改它的。而设计 §4 要求「看板 = 按 stage 分组/过滤**同一棵树**的投影」「三种视图，
        一份事实源」——当节点的事实列与看板列不符而**没有合法路径**改，人就只能去改
        `state.json`，而它是投影、会被 `rebuild` 覆盖 ⇒ **违反「事件才是真相」**。形状与
        当年 `touch_plan` 一样（建树后不可改，后由 `rescope` 补上）。

        **三条裁剪**（缺一就退化成新的机制缺口）：

        - **写时闭集校验**：取值必须在配置的 `stages` 内——与 `expand` 对 `stage`/`role`
          同一套 `_validate_stage`。非法值**响亮拒收**，绝不静默忽略、也绝不静默改成默认列
          （静默忽略正是本动词要消灭的那个“看板列与事实不符”）。
        - **落事件 + 记理由 + 记谁改的**：`node.stage` 是新 kind；理由与 holder 累进
          `stage_log`。投影只赋值、不校验（校验在写时，保事件流可重放）。
        - **收尾态拒收**：`done`/`skipped`/`failed`（`_STAGE_FROZEN`）不可改列——与
          「终态不可重开」一致：收尾记录是历史，改列等于改写历史；要改先把节点放回活态
          （failed 走 `retry`）。
        - **不碰调度**：`stage` 是**视图列**，不是调度边。本动词只写 `stage` 一个字段，
          `ready`/`drive`/波次/`is_stuck` 逐字节不变（`tests/test_stage_change.py` 钉住）。

        不带 `--dry-run`：守卫只做闭集/状态校验，不跑门（与 `require`/`unrequire`/`consume`
        同款；厂内规则见 `docs/design.md` §7.3）。
        """
        if not str(reason or "").strip():
            raise EngineError("stage 必须给 --reason：为何改列（记入 stage_log，可审计）")

        def guard(t: dict) -> None:
            item = self._need(t, node)
            status = str(item.get("status", "pending"))
            if status in _STAGE_FROZEN:
                raise EngineError(
                    f"{node} 状态为 {status}，不可改 stage：终态不可重开"
                    f"（收尾态 {_STAGE_FROZEN} 是历史记录，改列等于改写历史）。\n"
                    f"  要改先 reopen：failed 可 `disflow retry {tree} {node}` 放回 pending 再改；"
                    "done/skipped 无重开路径，确需调整请另起节点。")
            self._validate_stage(node, stage)
        return self._write("node.stage", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"stage": str(stage), "reason": reason,
                                              "holder": holder})

    def rescope(self, tree: str, node: str, touch: list[str], reason: str,
                dry_run: bool = False) -> dict:
        """改节点的写面声明（**发现型改动**：干活时才知道要多碰一个文件）。

       为什么不能随便改：`design_first` 的全部力量来自「**计划先于改动**」。若能把计划
       改成“我刚改过的东西”，门就白设了。所以判据是：

        - 只能把**本节点尚未碰过**的路径纳进来（用内容指纹判，与 `introduced` 同一原语）；
        - 已经碰过才想补声明 → 必须走 `done --decl '{"scope_waiver": "<理由>"}'` 破例，
          那会留下**违规记录**（显式、可审计），而不是把计划偷偷改宽。

       守卫的判据与 `done` 的其它门**同一目录源 + 同一基线**：目录取 `_gate_cwd`（该节点
       有真 worktree 就是 worktree），基线取 `start` 记的 `baseline` + `head_sha`。任一处
       仍吃 `self.cwd`，worktree 派发时守卫就在主工作区上看到干净 ⇒ 放行「改完再补计划」。
       "

       对应对称性：`rescope` ≈ `size_budget` 的 `node.context` 覆盖（改声明）；
       `scope_waiver` ≈ `size_waiver`（破例）。
       """
        if not str(reason or "").strip():
            raise EngineError("rescope 必须给 --reason：为什么现在才要加范围")
        if not isinstance(touch, list) or not touch or \
                not all(isinstance(path, str) and path.strip() for path in touch):
            raise EngineError('--touch 必须是非空字符串列表，如 \'["src/a.py"]\'')

        def guard(t: dict) -> None:
            item = self._need(t, node)
            if item.get("status") != RUNNING:
                raise EngineError(
                    f"{node} 状态为 {item.get('status')}，只有 {RUNNING} 的节点可 rescope")
            old = set(item.get("touch_plan") or [])
            added = [path for path in touch if path not in old]
            if not added:
                raise EngineError(f"新声明未增加任何路径（现有 {sorted(old)}），不必 rescope")
            # 门的**唯一目录源**（不是 `self.cwd`：worktree 派发时改动落在
            # `<state>/worktrees/<tree>/<node>`，用进程 cwd 的守卫在主工作区上看到干净 ⇒
            # 放行「先改后补计划」，`scope` 门变成装饰）。
            cwd = self._gate_cwd(tree, node)
            # 基线同源：`start` 记的 `head_sha` 是「开工时 HEAD」——不并它，提交型 worker
            # 的 `git status` 是空的、守卫看不见已提交的越界改动（与 `done` 的失败形状相同）。
            base_sha = (item.get("workspace") or {}).get("head_sha")
            touched = set(gates.newly_changed(cwd, item.get("baseline"), self.cfg,
                                              base_sha=base_sha) or [])
            dirty = sorted(set(added) & touched)
            if dirty:
                raise EngineError(
                    f"这些路径本节点**已经改过**，不能再声明进 touch_plan：{dirty}"
                    "—— 计划先于改动才叫计划；已动手的请用"
                    " done --decl '{\"scope_waiver\": \"<理由>\"}' 显式破例（会留违规记录）")
        return self._write("node.rescope", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"touch_plan": touch, "reason": reason},
                           dry_run=dry_run)

    def reopen(self, tree: str, *, reason: str = "") -> dict:
        """显式撤销 `tree.close`（与 `close` 同一形状：幂等——重复调不会报错）。

        为何要有这个动词：`close` 之后想接着干就只有两条路——要么造一个新树（丢掉原树
        的历史与 `awaits` 信号），要么静默失效（删事件/改投影，不可接受）。`reopen` 是
        第三条：留下 `tree.reopen` 事件，`reopen_log` 记下重开理由。

        `reason` 可选（不填就是无名重开）——与 `skip --reason` 的宽容度保持一致；真正的
        审计价值在「至少有一次显式事件」，而不是在强逼字符串。
        """
        def prepare(_: dict) -> dict:
            return {"reason": reason}
        return self._write("tree.reopen", tree=tree, prepare=prepare)

    def close(self, tree: str, dry_run: bool = False) -> dict:
        """树级门（两个）：`tree_closure`（终止条件）+ `tree_coverage`（需求已登记 + 可跑判据）。"""
        def prepare(t: dict) -> dict:
            ok, missing = gates.tree_closure_check(t)
            if not ok:
                raise GateReject(missing, {
                    "tree": tree,
                    "expect": "有 done 的 evaluate 与 reflect/standardize，"
                              "或 root.context.closure='not_applicable: <理由>'"})
            ok, missing = gates.tree_coverage_check(t)
            if not ok:
                raise GateReject(missing, {
                    "tree": tree,
                    "expect": "每个叶子任务有 goal 与可跑判据（acceptance.cmd 或 waiver）、"
                              "每个非根容器有子节点，或 root.context.coverage='not_applicable: <理由>'"})
            return {}
        return self._write("tree.close", tree=tree, prepare=prepare, dry_run=dry_run)

    # ── 推进面（只读）──────────────────────────────────────────────────────

    def drive(self, tree: str) -> dict:
        state = self.load()
        return run_drive(tree, self.tree(tree, state), self._closed(state))

    def plan(self, tree: str) -> list[dict]:
        state = self.load()
        return dispatch_plan(tree, self.tree(tree, state), self._closed(state))

    def brief(self, tree: str, node: str) -> dict:
        payload = handoff_brief(tree, self.tree(tree), node, self.cfg, repo=self.cwd)
        # worktree 路径是**绝对**的宿主现场（派生资源）——`brief` 是 worker 的契约，
        # 必须在那里告诉它「你的工作目录是 path，不是仓根」（见 dispatch.handoff_brief）。
        return payload

    def host_next(self, tree: str) -> dict:
        """宿主「下一步」：把一轮派活的六步压成一次**只读**调用。

        `drive → brief → start → 派活 → collect → done` 每轮六步，而宿主的 subagent 工具
        「一步就能派活」——于是 disflow 被绕开（宿主实测：3 小时 4 次派发、disflow 事件参与 0 次）。
        本动词一次给出「该派谁 + 每个 worker 的完整 task + 收尾要跑的命令」，宿主一次调用即可派活。

        **纯读**：只读投影 + 只读的 worktree 对账，**不落任何事件、不改状态、不起常驻进程**
        （宿主只是拿它去派活）。无就绪节点时以 `state` / `state_message` **显式**报
        `complete`（全部完成）/ `stuck`（卡住）/ `waiting`（就绪候选全被在跑节点的写面挡下），
        不静默返回空（见 `_dispatch_state`）。

        **绝不静默丢**：与在跑节点写面重叠的就绪候选**不**进 `plan`（宿主不会派它与在跑者抢
        同一文件），但会**原样透传** `drive` 的 `deferred`（同源，不在此重算），并把它写进
        `state_message`——两条路都点名「被谁占用 + 重叠哪些路径」。`deferred` 仅在非空时出现，
        与 `drive` 同款 ⇒ 无在跑节点时输出与改动前**逐字节一致**。
        """
        state = self.load()
        target = self.tree(tree, state)
        result = run_drive(tree, target, self._closed(state))
        plan = []
        for wave_index, wave in enumerate(result["waves"], 1):
            for node_id in wave:
                plan.append(self._host_entry(tree, target, node_id, wave_index))
        wave_state, wave_message = _dispatch_state(result)
        payload = {
            "tree": tree,
            "goal": target.get("goal", ""),
            "root": target.get("root"),
            "state": wave_state,
            "state_message": wave_message,
            "progress": result["progress"],
            "running": result["running"],
            "waves": result["waves"],
            "ready_wave": result["ready_wave"],
            "blocked": result["blocked"],
            "stuck": result["stuck"],
            "plan": plan,
        }
        # `deferred` **与 drive 同源**：直接透传 `drive` 的条目，绝不在此重算写面冲突。
        # 与 `drive` 同款「仅在非空时出现」⇒ 无在跑节点时输出与改动前**逐字节一致**。
        if result.get("deferred"):
            payload["deferred"] = result["deferred"]
        return payload

    def _host_entry(self, tree: str, target: dict, node_id: str, wave: int) -> dict:
        """一个就绪节点的派活条目：完整 brief（= worker 的 task）+ start/collect/done 命令。

        `brief` 直接用 `handoff_brief`（与 `disflow brief` 同一份，含必填声明 / 提问纪律 /
        结果契约 / 工作目录），再补上 `question_protocol` —— 那条约定指出：提问走宿主通道
        **且**在 `collect` 里标 `blocked`，事件流才留得下痕。
        """
        node = target["nodes"][node_id]
        brief = handoff_brief(tree, target, node_id, self.cfg, repo=self.cwd)
        brief["question_protocol"] = QUESTION_PROTOCOL
        return {
            "node": node_id,
            "wave": wave,
            "stage": node.get("stage"),
            "role": node.get("role"),
            "goal": node.get("goal", ""),
            "brief": brief,
            "start_cmd": f"disflow start {tree} {node_id} --holder <HOST>",
            "collect_cmd": (f"disflow collect {tree} {node_id} --result "
                            "'{\"status\": \"ok\", \"summary\": \"<一句话>\", "
                            "\"changed_files\": [\"<路径>\"]}'"),
            "done_cmd": f"disflow done {tree} {node_id} --decl '{{\"evidence\": …}}'",
        }

    def status(self, tree: str) -> dict:
        state = self.load()
        target = self.tree(tree, state)
        nodes = target["nodes"]
        closed = self._closed(state)
        return {
            "tree": tree,
            "goal": target.get("goal", ""),
            "closed": bool(target.get("closed")),
            "progress": progress(nodes),
            "ready": ready_tasks(nodes, closed),
            "running": running_tasks(nodes),
            "stuck": is_stuck(nodes, closed),
            "nodes": nodes,
        }
