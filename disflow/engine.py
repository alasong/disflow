"""disflow · 引擎（动词面）。

所有写操作走同一条路径：**取锁 → 读投影 → 校验 → append 事件（内部重算投影）**。
读操作只读投影。引擎不 spawn、不常驻。
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from . import config, gates, graph, store
from .config import Config
from .core import RUNNING
from .dispatch import dispatch_plan
from .dispatch import drive as run_drive
from .dispatch import handoff_brief, result_contract, validate_result
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
        return self._write("tree.init", tree=tree, existing=False,
                           prepare=lambda _: {"goal": goal})

    def set_goal(self, tree: str, goal: str) -> dict:
        return self._write("tree.goal", tree=tree, prepare=lambda _: {"goal": goal})

    # ── 节点与图 ──────────────────────────────────────────────────────────

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

    def expand(self, tree: str, parent: str, children: list[dict]) -> dict:
        def guard(t: dict) -> None:
            self._need(t, parent)
            self._validate_vocabulary(children)
        return self._write("node.expand", tree=tree, node=parent, guard=guard,
                           prepare=lambda _: {"children": children})

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
        return self._write("node.start", tree=tree, node=node, guard_state=guard,
                           prepare=lambda _: {"lease": lease,
                                              "baseline": gates.snapshot(self.cwd, self.cfg)},
                           dry_run=dry_run)

    def collect(self, tree: str, node: str, result: dict) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            missing = validate_result(result_contract(tree, t, node), result)
            if missing:
                raise EngineError("结果契约不符：" + "；".join(missing))
        return self._write("node.collect", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"result": result})

    def done(self, tree: str, node: str, declarations: dict | None = None,
             dry_run: bool = False) -> dict:
        def prepare(t: dict) -> dict:
            item = self._need(t, node)
            ok, why, evidence = gates.evidence_meta(item.get("acceptance"), self.cwd, self.cfg)
            baseline = item.get("baseline") or {}
            # 只归因「开工后**内容真的变了**」的改动：既忽略开工前的脏文件，
            # 也逮得住「同文件被前一节点弄脏、本节点又改」这种差集看不见的情况
            introduced = gates.newly_changed(self.cwd, baseline, self.cfg)
            scope = gates.scope_check(item.get("touch_plan") or [], introduced)
            size = gates.diff_size(self.cwd, introduced, self.cfg)
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

    def rescope(self, tree: str, node: str, touch: list[str], reason: str,
                dry_run: bool = False) -> dict:
        """改节点的写面声明（**发现型改动**：干活时才知道要多碰一个文件）。

       为什么不能随便改：`design_first` 的全部力量来自「**计划先于改动**」。若能把计划
       改成“我刚改过的东西”，门就白设了。所以判据是：

        - 只能把**本节点尚未碰过**的路径纳进来（用内容指纹判，与 `introduced` 同一原语）；
        - 已经碰过才想补声明 → 必须走 `done --decl '{"scope_waiver": "<理由>"}'` 破例，
          那会留下**违规记录**（显式、可审计），而不是把计划偷偷改宽。

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
            touched = set(gates.newly_changed(self.cwd, item.get("baseline"), self.cfg) or [])
            dirty = sorted(set(added) & touched)
            if dirty:
                raise EngineError(
                    f"这些路径本节点**已经改过**，不能再声明进 touch_plan：{dirty}"
                    "—— 计划先于改动才叫计划；已动手的请用"
                    " done --decl '{\"scope_waiver\": \"<理由>\"}' 显式破例（会留违规记录）")
        return self._write("node.rescope", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"touch_plan": touch, "reason": reason},
                           dry_run=dry_run)

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
        return handoff_brief(tree, self.tree(tree), node)

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
