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

# 看板列（stage）—— 仅用于视图分组，不参与就绪判定。
# **四个阶段**（需求 → 设计 → 开发 → 验证），与设计原文的阶段图同形。
# `done` **不是列**：它是节点**状态**（`core.TERMINAL`），与阶段是两个轴——留着它会造出一个
# 永远为空、且与状态同名的列（大半节点状态是 done，列里却是 0）。凡是「干完了」用 status 表达。
STAGES: tuple[str, ...] = ("req", "design", "dev", "verify")

# 负责方（role）—— 与 stage 独立
ROLES: tuple[str, ...] = ("dev", "qa", "pm")

# 规模预算（门 `size_budget`）
SIZE_BUDGET_DEFAULT: int = 400

# 租约有效期：`start` 加锁；超时后由 `reap` **显式**回收（reap 是事件，就绪判定保持纯函数）
LEASE_TTL_SECONDS: int = 900

# 提问纪律（FR-15）：brief 必须一次写全「必填声明 / 提问纪律 / 结果契约」。
# 为何要它：worker 自己拍板的代价是**默默改范围**——而范围是宿主定的。给一条合法通道
# （`collect --result` 的 `status: blocked`）比要求「别乱来」更能执行。
QUESTION_POLICY_DEFAULT: dict = {
    "ask_host": [
        "改 touch_plan / 验收标准 / goal",
        "跨节点的接口约定",
        "与 goal 相冲的取舍",
    ],
    "decide_alone": ["实现细节", "命名", "内部结构"],
    "how": (
        "要问就问：把问题写进 `collect --result` 的 summary 并把 status 置为 'blocked'，"
        "然后停。不要自己拍板改范围——宿主读到 blocked 后会决定（rescope / unrequire / 改 goal），"
        "再由你继续。status 里没有「我猜了一下」这个选项。"
    ),
}

# 树级终止条件门（`tree_closure`）：树里必须有 done 的收益验证 + 经验沉淀
CLOSURE_ENTRIES: tuple[str, ...] = ("evaluate",)
SEDIMENT_ENTRIES: tuple[str, ...] = ("reflect", "standardize")

# 事件 kind 闭集（投影 reducer 只认这些；出现未知 kind 即报错）
EVENT_KINDS: tuple[str, ...] = (
    "tree.init", "tree.goal", "tree.deadline",
    "node.expand", "node.require", "node.unrequire", "node.relate", "node.unrelate",
    "node.import",
    "node.context",
    "node.stage",
    "node.rescope",
    "asset.produce", "asset.consume",
    "node.start", "node.collect", "node.done",
    "node.fail", "node.retry", "node.skip", "node.reclaim",
    "tree.close", "tree.reopen",
)
