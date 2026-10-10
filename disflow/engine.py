"""disflow · 引擎（动词面）。

所有写操作走同一条路径：**取锁 → 读投影 → 校验 → append 事件（内部重算投影）**。
读操作只读投影。引擎不 spawn、不常驻。
"""
from __future__ import annotations

import os

from . import gates, graph, store
from .dispatch import dispatch_plan
from .dispatch import drive as run_drive
from .dispatch import handoff_brief, result_contract, validate_result
from .ready import blocked_by, is_stuck, progress, ready_tasks, running_tasks
from .spec import SPEC
from .state import project
from .store import now_iso


class EngineError(Exception):
    """引擎级错误（用法 / 依赖 / 状态非法）。"""


class GateReject(EngineError):
    """门拒收：带缺项清单 + 契约片段。"""

    def __init__(self, missing: list[str], contract: dict):
        super().__init__("门拒收：" + "；".join(missing))
        self.missing = missing
        self.contract = contract


class Engine:
    def __init__(self, spec: dict | None = None, cwd: str | None = None):
        self.spec = spec or SPEC
        self.cwd = cwd or os.getcwd()

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
               guard=None, guard_state=None, existing: bool = True) -> dict:
        """唯一写点：锁内 读 → 校验 → append。`prepare(tree)` 返回事件的 extra 字段。

        `guard(tree)` 只看本树；`guard_state(tree, state)` 还能看到全局（跨树信号）。
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

    def expand(self, tree: str, parent: str, children: list[dict]) -> dict:
        return self._write("node.expand", tree=tree, node=parent,
                           guard=lambda t: self._need(t, parent),
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

    def relate(self, tree: str, node: str, other: str) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            self._need(t, other)
        return self._write("node.relate", tree=tree, node=node, guard=guard,
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

    def start(self, tree: str, node: str, holder: str = "") -> dict:
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
                           prepare=lambda _: {"lease": {"holder": holder or "unknown",
                                                        "at": now_iso()},
                                              "baseline": gates.git_changed_files(self.cwd) or []})

    def collect(self, tree: str, node: str, result: dict) -> dict:
        def guard(t: dict) -> None:
            self._need(t, node)
            missing = validate_result(result_contract(tree, t, node), result)
            if missing:
                raise EngineError("结果契约不符：" + "；".join(missing))
        return self._write("node.collect", tree=tree, node=node, guard=guard,
                           prepare=lambda _: {"result": result})

    def done(self, tree: str, node: str, declarations: dict | None = None) -> dict:
        def prepare(t: dict) -> dict:
            item = self._need(t, node)
            ok, why, evidence = gates.evidence_meta(item.get("acceptance"), self.cwd)
            baseline = set(item.get("baseline") or [])
            changed = evidence.get("changed_files")
            # 只归因「开工后被引入」的改动，忽略开工前就存在的脏文件（他人改动）
            introduced = None if changed is None else [f for f in changed if f not in baseline]
            scope = gates.scope_check(item.get("touch_plan") or [], introduced)
            size = gates.diff_size(self.cwd, introduced)
            budget = gates.resolve_budget(item)
            evidence["baseline"] = sorted(baseline)
            evidence["introduced"] = introduced
            evidence["scope"] = scope
            evidence["size"] = size
            evidence["budget"] = budget
            contract = {"node": node, "acceptance": item.get("acceptance"),
                        "touch_plan": item.get("touch_plan"), "size_budget": budget,
                        "expect": "验收命令通过；改动落在 touch_plan 内；改动行数不超预算"}
            missing: list[str] = []
            if not ok:
                missing.append(f"验收未通过：{why}")
            if scope["checked"] and scope["violations"]:
                missing.append(f"改动超出 touch_plan：{scope['violations']}")
            if (size is not None and size["total"] > budget
                    and not (declarations or {}).get("size_waiver")):
                missing.append(f"规模超预算：{size['total']} 行 > {budget} 行"
                               f"（可声明 size_waiver 破例）")
            if missing:
                raise GateReject(missing, contract)
            return {"evidence": evidence, "declarations": declarations or {}}
        return self._write("node.done", tree=tree, node=node, prepare=prepare)

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

    def close(self, tree: str) -> dict:
        """树级终止条件门：有 done 的收益验证与经验沉淀，或根声明 `not_applicable: <理由>`。"""
        def prepare(t: dict) -> dict:
            ok, missing = gates.tree_closure_check(t)
            if not ok:
                raise GateReject(missing, {
                    "tree": tree,
                    "expect": "有 done 的 evaluate 与 reflect/standardize，"
                              "或 root.context.closure='not_applicable: <理由>'"})
            return {}
        return self._write("tree.close", tree=tree, prepare=prepare)

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
