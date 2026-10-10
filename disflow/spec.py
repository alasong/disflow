"""disflow spec —— 机制数据（路径、状态集、阶段集）。业务名词只出现在这里与 node 模板。"""
from __future__ import annotations

# 机制词汇从核心层再导出（NFR-2：核心不依赖业务层，方向是 spec → core）
from .core import KINDS, STORED_STATUS, TERMINAL  # noqa: F401  （向后兼容再导出）

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

# 看板列（stage）—— 仅用于视图分组，不参与就绪判定
STAGES: tuple[str, ...] = ("req", "design", "dev", "verify", "done")

# 负责方（role）—— 与 stage 独立
ROLES: tuple[str, ...] = ("dev", "qa", "pm")

# 规模预算（门 `size_budget`）
SIZE_BUDGET_DEFAULT: int = 400

# 租约有效期：`start` 加锁；超时后由 `reap` **显式**回收（reap 是事件，就绪判定保持纯函数）
LEASE_TTL_SECONDS: int = 900

# 树级终止条件门（`tree_closure`）：树里必须有 done 的收益验证 + 经验沉淀
CLOSURE_ENTRIES: tuple[str, ...] = ("evaluate",)
SEDIMENT_ENTRIES: tuple[str, ...] = ("reflect", "standardize")

# 事件 kind 闭集（投影 reducer 只认这些；出现未知 kind 即报错）
EVENT_KINDS: tuple[str, ...] = (
    "tree.init", "tree.goal",
    "node.expand", "node.require", "node.unrequire", "node.relate", "node.unrelate",
    "node.import",
    "node.context",
    "node.rescope",
    "asset.produce", "asset.consume",
    "node.start", "node.collect", "node.done",
    "node.fail", "node.retry", "node.skip", "node.reclaim",
    "tree.close",
)
