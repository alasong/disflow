"""disflow spec —— 机制数据（路径、状态集、阶段集）。业务名词只出现在这里与 node 模板。"""
from __future__ import annotations

# 存储机制：状态目录与文件名（store 层按此取路径）
SPEC: dict = {
    "state": {
        "dir": ".disflow",
        "env": "DISFLOW_STATE_DIR",
        "events": "events.jsonl",
        "projection": "state.json",
        "checkpoints": "checkpoints",
    }
}

# 节点分型（HTN 的 compound / primitive）
KINDS: tuple[str, ...] = ("container", "task")

# 存储状态：就绪（ready）是**派生量**，不在这里
STORED_STATUS: tuple[str, ...] = ("pending", "running", "done", "failed", "skipped")

# 终态
TERMINAL: tuple[str, ...] = ("done", "skipped")

# 看板列（stage）—— 仅用于视图分组，不参与就绪判定
STAGES: tuple[str, ...] = ("req", "design", "dev", "verify", "done")

# 负责方（role）—— 与 stage 独立
ROLES: tuple[str, ...] = ("dev", "qa", "pm")

# 规模预算（门 `size_budget`）：节点开工后引入的改动行数上限（可用 node.context.size_budget 覆盖）
SIZE_BUDGET_DEFAULT: int = 400

# 树级终止条件门（`tree_closure`）：树里必须有 done 的收益验证 + 经验沉淀
CLOSURE_ENTRIES: tuple[str, ...] = ("evaluate",)
SEDIMENT_ENTRIES: tuple[str, ...] = ("reflect", "standardize")

# 事件 kind 闭集（投影 reducer 只认这些；出现未知 kind 即报错）
EVENT_KINDS: tuple[str, ...] = (
    "tree.init", "tree.goal",
    "node.expand", "node.require", "node.relate", "node.import", "node.context",
    "asset.produce", "asset.consume",
    "node.start", "node.collect", "node.done",
    "node.fail", "node.retry", "node.skip",
    "tree.close",
)
