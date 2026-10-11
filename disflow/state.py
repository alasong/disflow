"""disflow · 投影 reducer（事件流 → 状态）。

事件是唯一真相；本模块把事件折叠成投影 `{"trees": {name: {goal, root, nodes, assets, deadline}}}`。
未知 kind 一律报错（事件闭集，不静默丢弃）。
"""
from __future__ import annotations

from . import graph
from .model import new_node, next_id, validate_node_spec, validate_points
from .vocab import DEFAULT_STAGE, ENTRY_STAGE

ROOT_ID = "root"


def empty() -> dict:
    return {"trees": {}}


def project(events: list[dict]) -> dict:
    state = empty()
    for event in events:
        apply_event(state, event)
    return state


def expands_after_close(events: list[dict]) -> list[dict]:
    """找「树已关闭后又挂任务」的历史事件（每棵违规树一条，取最早的违规 expand）。

    为何要这道检查：`closed` 是**跳树完成信号**（另一棵树的 `awaits` 读它）。树一旦关闭，
    它的完成就被消费方读走了，此时再挂任务会让信号失真——所以 `Engine.expand` 已对已关闭
    的树拒收（2026-10-10）。

    为何不能用**终态**判：「已关闭 **且** 还有未完成任务」是**合法状态**——`close` 本来就不
    要求没有未完成任务（`tree_closure_check` 只看有没有 done 的 evaluate/沉淀），
    「放弃剩余活、就这么关」是正当意图。两种情形在 `state.json` 里长得一模一样，
    只有**事件顺序**能分开：真正的违规是「`node.expand` 晚于该树最后一次 `tree.close`」。

    何以仍要它：`expand` 的拒收只能管**从今往后**；本条管**已经落在事件流里的**违规
    （修复前建的状态目录、或手改事件）。

    **`tree.reopen` 会把违规清掉**——这不是掩盖：与 `scope_waiver` 同一形状（显式破例、
    留痕、然后放行）。宿主重开时已经知道了「信号可能已被读走」，而那件事的事实留在事件
    日志里（`tree.reopen` + `reopen_log`），doctor 只报**当下还需要动作**的那些。
    否则这条会变成一条永远消不掉的红，结果是人学会无视 doctor。
    """
    last_close: dict[str, str] = {}
    seen: dict[str, dict] = {}
    for event in events:
        slug = event.get("slug")
        kind = event.get("kind")
        if not slug:
            continue
        if kind == "tree.close":
            last_close[slug] = event.get("ts") or ""
        elif kind == "tree.reopen":
            last_close.pop(slug, None)          # 显式重开：前一条 close 不再算数
            seen.pop(slug, None)                # 且承认并清掉由它引起的那条违规
        elif kind == "node.expand" and slug in last_close:
            seen.setdefault(slug, {
                "tree": slug,
                "closed_at": last_close[slug],
                "expanded_at": event.get("ts") or "",
                "node": event.get("node") or "",
                "holder": event.get("holder") or "",
            })
    return list(seen.values())


def apply_event(state: dict, event: dict) -> None:
    kind = str(event.get("kind") or "")
    handler = _HANDLERS.get(kind)
    if handler is None:
        # 报错文本用 `_HANDLERS`（**真正可派发**的集合），不用 `EVENT_KINDS`：
        # 后者是 vocab 里的声明，两者可漂移；测试 `test_event_kinds.py` 把它钉住。
        raise ValueError(f"未知事件 kind：{kind!r}（可派发：{sorted(_HANDLERS)}）")
    handler(state, event)


def _tree(state: dict, name: str) -> dict:
    # 不存 `slug`：它恒等于外层 key（可推导值，不该持久化进投影）；指标层改为显式接收
    # slug/预过滤 events。`deadline` 留在这里——它是真声明（`tree.deadline` 事件写的），
    # 不是可推导值。
    return state["trees"].setdefault(
        name, {"goal": "", "root": None, "nodes": {}, "assets": {},
               "deadline": None, "boards": []})


def _node(state: dict, tree_name: str, node_id: str) -> dict:
    tree = _tree(state, tree_name)
    node = tree["nodes"].get(node_id)
    if node is None:
        raise ValueError(f"节点不存在：{tree_name}/{node_id}")
    return node


def _on_tree_init(state: dict, event: dict) -> None:
    tree = _tree(state, event["slug"])
    tree["goal"] = event.get("goal", "")
    # 板定义**由写时携带**（`Engine.init_tree` 从 cfg.boards 播种）：板随配置/树变，
    # 而 reducer 不能读配置（一读就不可重放）。旧事件流不带它 ⇒ 投影为空 ⇒ 读路径回落 cfg。
    if event.get("boards"):
        tree["boards"] = list(event["boards"])
    if tree["root"] is None:
        tree["root"] = ROOT_ID
        # `stage` 由**写时**给出（`Engine.init_tree` 写 `cfg.entry_stage`）：板定义可配后，
        # 入口板随 boards.json 变，而 reducer 不能读配置（一读事件流就不可重放）。
        # 回落 `ENTRY_STAGE` 只服务**早期事件流**（那时的 `tree.init` 不带 stage 字段）——
        # 它的值冻结为当时的内建入口板，改它等于改历史的重放结果。
        tree["nodes"][ROOT_ID] = new_node(
            ROOT_ID, parent=None, kind="container",
            stage=event.get("stage") or ENTRY_STAGE, role="pm",
            goal=tree["goal"])


def _on_tree_boards(state: dict, event: dict) -> None:
    """改该树的板定义（`tree.boards` 事件）——投影只赋值、**不校验**（校验在写时）。

    与 `tree.deadline` 同一分界：reducer 一依赖配置/校验，事件流就不可重放了。
    """
    tree = _tree(state, event["slug"])
    tree["boards"] = list(event.get("boards") or [])


def _on_tree_goal(state: dict, event: dict) -> None:
    tree = _tree(state, event["slug"])
    tree["goal"] = event.get("goal", "")
    root = tree["nodes"].get(tree["root"] or ROOT_ID)
    if root is not None:
        root["goal"] = tree["goal"]


def _on_tree_deadline(state: dict, event: dict) -> None:
    """设置树级 `deadline`（迭代边界）：日期串或 None 清空。

    经 `Engine.deadline` 写入的值必然合法（写时 `parse_deadline` 归一，非法即拒、不落盘）。
    但 reducer 只做纯赋值、**不做值校验**——校验必须放在写时，否则重放历史里已有的
    非法值会炸。于是**绕过 Engine 直写事件流**（如 `store.append_event`）仍能把非法值写进去；
    这一点由读路径 `metrics` 用 `deadline_reason` 显式报告（不静默忽略），而不是靠 reducer 拦截。
    """
    tree = _tree(state, event["slug"])
    tree["deadline"] = event.get("deadline") or None


def _on_node_expand(state: dict, event: dict) -> None:
    tree_name = event["slug"]
    parent = _node(state, tree_name, event["node"])
    tree = state["trees"][tree_name]
    pending: list[tuple[dict, str]] = []
    for spec in event.get("children", []):
        validate_node_spec(spec)
        node_id = spec.get("id") or next_id(tree["nodes"])
        if node_id in tree["nodes"]:
            raise ValueError(f"节点 id 已存在：{node_id}")
        tree["nodes"][node_id] = new_node(
            node_id, parent=parent["id"],
            # 同上：默认板由写时补（`Engine.expand` → `_with_default_stage`）；这里的
            # `DEFAULT_STAGE` 只兜**早期事件流**（`node.expand` 里子节点没写 stage）。
            kind=spec.get("kind", "task"), stage=spec.get("stage") or DEFAULT_STAGE,
            role=spec.get("role", "dev"), goal=spec.get("goal", ""),
            context=spec.get("context"), entry=spec.get("entry", ""),
            acceptance=spec.get("acceptance"),
            touch_plan=spec.get("touch_plan"), awaits=spec.get("awaits"),
            requires=spec.get("requires"), relates=spec.get("relates"),
            produces=spec.get("produces"), consumes=spec.get("consumes"),
            points=validate_points(spec.get("points")))
        pending.append((spec, node_id))
    # 两阶段 + 分相：先建齐**所有**节点，再接线。否则同批兄弟之间互相声明（B produces X、
    # A consumes X；或 B requires A）会被误判为「悬空」。分相让同批内的声明顺序无关。
    _link_relates(tree, pending)
    _link_produces(tree, pending)
    _link_consumes(tree, pending)
    _link_requires(tree, pending)


def _declared(spec: dict, key: str) -> list[str]:
    return [str(item) for item in (spec.get(key) or [])]


def _link_relates(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`relates` 是软边（可成环）—— 只查两端存在。"""
    for spec, node_id in pending:
        for other in _declared(spec, "relates"):
            if other not in tree["nodes"]:
                raise ValueError(f"{node_id} relates 悬空：{other}")
            _append_unique(tree["nodes"][node_id], "relates", other)


def _link_produces(tree: dict, pending: list[tuple[dict, str]]) -> None:
    for spec, node_id in pending:
        for name in _declared(spec, "produces"):
            _append_unique(tree["nodes"][node_id], "produces", name)
            tree["assets"][name] = node_id


def _link_consumes(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`consumes` 必须指向已登记资产（与 `consume` 动词同一判据）——故在 produces 之后跑。"""
    for spec, node_id in pending:
        for name in _declared(spec, "consumes"):
            if name not in tree["assets"]:
                raise ValueError(f"{node_id} consumes 未登记资产：{name}")
            _append_unique(tree["nodes"][node_id], "consumes", name)


def _link_requires(tree: dict, pending: list[tuple[dict, str]]) -> None:
    """`requires` 是调度边 —— 与 `require` 动词同一套校验：拒悬空 / 自环 / 成环。"""
    for spec, node_id in pending:
        for dep in _declared(spec, "requires"):
            if dep not in tree["nodes"]:
                raise ValueError(f"{node_id} requires 悬空：{dep}")
            if dep == node_id:
                raise ValueError(f"{node_id} 不能依赖自身")
            if graph.would_cycle(tree["nodes"], node_id, dep):
                raise ValueError(f"加边即成环：{node_id} requires {dep}")
            _append_unique(tree["nodes"][node_id], "requires", dep)


def _append_unique(node: dict, key: str, value: str) -> None:
    bucket = node.setdefault(key, [])
    if value not in bucket:
        bucket.append(value)


def _on_node_require(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "requires", event["dep"])


def _on_node_unrequire(state: dict, event: dict) -> None:
    """删 `requires` 边（建错依赖的合法出口）。

    边不存在即 `ValueError`：`project` 是**语义闸门**——非法事件既不许落盘也不许重放。
    有了这条，被拒的 unrequire 不会在事件流里留下痕（与 `store._persist` 的先试算一致）。
    """
    node = _node(state, event["slug"], event["node"])
    dep = str(event["dep"])
    if dep not in (node.get("requires") or []):
        raise ValueError(f"{event['node']} 没有 requires 边：{dep}")
    node["requires"] = [item for item in node["requires"] if item != dep]


def _on_node_relate(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "relates", event["other"])


def _on_node_unrelate(state: dict, event: dict) -> None:
    """删 `relates` 边。

    为何要有：`relate` 建错了却没有任何删除动词 —— 与 `unrequire` 修之前是**同一个缺口**
    （当时建错依赖只能 `skip` 整个节点）。信息边虽不参与就绪判定，但错了还得能改。

    边不存在即 `ValueError`（`project` 是语义闸门，非法事件既不许落盘也不许重放）。
    """
    node = _node(state, event["slug"], event["node"])
    other = str(event["other"])
    if other not in (node.get("relates") or []):
        raise ValueError(f"{event['node']} 没有 relates 边：{other}")
    node["relates"] = [item for item in node["relates"] if item != other]


def _on_asset_produce(state: dict, event: dict) -> None:
    tree = state["trees"][event["slug"]]
    _append_unique(_node(state, event["slug"], event["node"]), "produces", event["name"])
    tree["assets"][event["name"]] = event["node"]


def _on_asset_consume(state: dict, event: dict) -> None:
    _append_unique(_node(state, event["slug"], event["node"]), "consumes", event["name"])


def _on_node_start(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "running"
    node["lease"] = event.get("lease")
    raw = event.get("baseline") or {}
    # 基线统一为 {路径: 内容指纹}；旧事件（路径列表）归一为 None 指纹 ⇒ 一律判为已改（保守）
    node["baseline"] = raw if isinstance(raw, dict) else dict.fromkeys(raw)
    # 开工现场（cwd / head_sha / worktree）：`done` 据此把门跑在同一个目录上，
    # 并在两者不一致时 fail-closed 拒收。旧事件没有它就为 None（无法核对，走宽容）。
    node["workspace"] = event.get("workspace")


def _on_node_collect(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["collected"] = event.get("result")


def _on_node_done(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "done"
    node["lease"] = None
    node["evidence"] = event.get("evidence")


def _on_node_fail(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "failed"
    node["lease"] = None
    node["failure"] = event.get("reason")


def _on_node_retry(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "pending"
    node["lease"] = None


def _on_node_skip(state: dict, event: dict) -> None:
    node = _node(state, event["slug"], event["node"])
    node["status"] = "skipped"
    node["lease"] = None
    node["skip_reason"] = event.get("reason")


def _on_node_import(state: dict, event: dict) -> None:
    """导入既有状态（如从 mgt）——标记 imported，**不声称经过本引擎的门**。"""
    node = _node(state, event["slug"], event["node"])
    node["status"] = event.get("status", "pending")
    node["imported"] = True
    node["evidence"] = {"imported": True, "note": event.get("note", "")}


def _on_node_context(state: dict, event: dict) -> None:
    """合并节点 context（供后代继承 / 树根声明 closure）。"""
    node = _node(state, event["slug"], event["node"])
    node.setdefault("context", {}).update(event.get("context") or {})


def _on_node_rescope(state: dict, event: dict) -> None:
    """改节点的写面声明：`touch_plan` 换成新值，并把**变更历史**留在 `rescope_log`。

    历史必须留：否则“计划先于改动”这个前提交代不清，事后无法审计
    （谁在什么时候把范围改宽了、理由是什么）。
    """
    node = _node(state, event["slug"], event["node"])
    old = list(node.get("touch_plan") or [])
    node["touch_plan"] = list(event.get("touch_plan") or [])
    node.setdefault("rescope_log", []).append({
        "from": old, "to": list(node["touch_plan"]),
        "reason": event.get("reason") or "",
    })


def _on_node_stage(state: dict, event: dict) -> None:
    """改节点的看板列（`stage`）：换值 + 把**变更历史**留在 `stage_log`。

    历史必须留（与 `rescope_log` / `reopen_log` 同一理由）：看板列是给人看的投影，
    事后要能回答「这列是谁、什么时候、为什么改的」——否则改列成了无痕的历史改写。
    `holder`（谁改的）也一并记：`rescope` 没有这条，是因为它没有 holder 入参；
    stage 有，就不能把「谁改的」丢掉。

    reducer 只赋值、**不校验**取值：校验在写时（`Engine._validate_stage`），否则重放历史里
    已有的（来自别版本/别配置的）列值会炸——与 `_on_tree_deadline` 同一分界。
    """
    node = _node(state, event["slug"], event["node"])
    old = node.get("stage")
    new = event.get("stage")
    node["stage"] = new
    node.setdefault("stage_log", []).append({
        "from": old, "to": new,
        "reason": event.get("reason") or "",
        "holder": event.get("holder") or "",
    })


def _on_node_acceptance(state: dict, event: dict) -> None:
    """补/改节点的**验收判据**（`acceptance`）：换值 + 把变更历史留在 `acceptance_log`。

    为何需要（对称性判据）：`stage` 有 `set_stage`、写面有 `rescope`、树级有 `context`，
    唯独**节点验收判据**没有「建好之后能改」的路径 —— 而 `expand` 时忘写 `acceptance`
    是常见疏漏（本仓 `board-web` 实例：整个交付完成后才发现节点无判据、`done` 被拦，
    而 34 个动词里没有合法补口）。无补口的后果与当年 `touch_plan` 一样：人只能去改
    `state.json`，而它是投影、会被 `rebuild` 覆盖 ⇒ 违反「事件才是真相」。

    历史必须留（与 `stage_log` / `rescope_log` / `reopen_log` 同一理由）：判据是门的输入，
    事后要能回答「这门是谁、什么时候、为什么这么定的」。
    reducer 只赋值、**不校验**（校验在写时）：重放历史时不许因为旧值非法而炸。
    """
    node = _node(state, event["slug"], event["node"])
    old = node.get("acceptance")
    new = event.get("acceptance")
    node["acceptance"] = new
    node.setdefault("acceptance_log", []).append({
        "from": old, "to": new,
        "reason": event.get("reason") or "",
        "holder": event.get("holder") or "",
    })


def _on_tree_close(state: dict, event: dict) -> None:
    state["trees"][event["slug"]]["closed"] = True


def _on_tree_reopen(state: dict, event: dict) -> None:
    """撤销 `tree.close`：**显式、留痕**。

    为何必须显式：`closed` 是**跳树完成信号**（另一棵树的 `awaits` 读它）。若「挂任务」能
    悄悄让该信号失效，消费方就会基于一个未真正完成的上游开工——所以「重开」得是一次
    可审计的动作。历史（重开过几次、为何）进 `reopen_log`，与 `rescope_log` 同一形状。
    """
    tree = state["trees"][event["slug"]]
    if tree.get("closed"):                 # 只在真的从「已关闭」变回「未关闭」时记一笔
        tree.setdefault("reopen_log", []).append({
            "reason": event.get("reason") or "",
            "at": event.get("ts") or "",
        })
    tree["closed"] = False


def _on_node_reclaim(state: dict, event: dict) -> None:
    """回收过期租约：回到 pending，保留回收原因（可审计）。"""
    node = _node(state, event["slug"], event["node"])
    node["status"] = "pending"
    node["lease"] = None
    node["reclaimed"] = event.get("reason", "")


_HANDLERS = {
    "tree.init": _on_tree_init,
    "tree.goal": _on_tree_goal,
    "tree.deadline": _on_tree_deadline,
    "tree.boards": _on_tree_boards,
    "node.expand": _on_node_expand,
    "node.require": _on_node_require,
    "node.unrequire": _on_node_unrequire,
    "node.relate": _on_node_relate,
    "node.unrelate": _on_node_unrelate,
    "asset.produce": _on_asset_produce,
    "asset.consume": _on_asset_consume,
    "node.start": _on_node_start,
    "node.collect": _on_node_collect,
    "node.done": _on_node_done,
    "node.fail": _on_node_fail,
    "node.retry": _on_node_retry,
    "node.skip": _on_node_skip,
    "node.import": _on_node_import,
    "node.context": _on_node_context,
    "node.stage": _on_node_stage,
    "node.acceptance": _on_node_acceptance,
    "node.rescope": _on_node_rescope,
    "tree.close": _on_tree_close,
    "tree.reopen": _on_tree_reopen,
    "node.reclaim": _on_node_reclaim,
}
