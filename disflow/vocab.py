"""disflow 机制数据（路径 / 状态集 / 阶段集）。业务名词只出现在这里与 node 模板。

⚠ **术语**：本模块名为 `spec`，但此处的「spec」是**本实例的机制数据（vocabulary + 内建默认）**，
**不是** ADR 0006 的 **Spec（交付规格 / 一类交付单元实体，登记在项目资产里）**。
存储布局的常量叫 `STORAGE`（不再叫 `SPEC`），避开与该实体撞名。
"""
from __future__ import annotations

# 机制词汇从核心层再导出（NFR-2：核心不依赖业务层，方向是 vocab → core）
from .core import KINDS, STORED_STATUS, TERMINAL  # noqa: F401  （向后兼容再导出）

# 存储机制：状态目录与文件名（store 层按此取路径）
STORAGE: dict = {
    "state": {
        "dir": ".disflow",
        "env": "DISFLOW_STATE_DIR",
        "events": "events.jsonl",
        "projection": "state.json",
        "checkpoints": "checkpoints",
    }
}

# 看板定义（stage）的**内建默认**。**权威来源已搬到独立文件 `boards.json`**
# （见 `disflow/boards.py`）：`Config.boards` 从那里读，`Config.stages` 由它派生。
# 这里保留一份是为了「零配置可用」——两个文件都不存在时用这套。
#
# **四个阶段**（需求 → 设计 → 开发 → 验证），与设计原文的阶段图同形。
# `done` **不是板**：它是节点**状态**（`core.TERMINAL`），与阶段是两个轴——留着它会造出一个
# 永远为空、且与状态同名的板（大半节点状态是 done，板里却是 0）。凡是「干完了」用 status 表达。
# （`boards.py` 把这条变成机检：`RESERVED_IDS`，写进 boards.json 即拒收。）
STAGES: tuple[str, ...] = ("req", "design", "dev", "verify")

# 板的人读标签的**内建默认**（同上，权威来源是 boards.json 的 `label`）。
# 缺项**回落成板 id 本身** —— 自定义板不会因为少一条标签就崩，也不逼使用者补一份标签表。
# 机器态（`--json`）不带标签：标签是**渲染**的事，不是事实。
STAGE_LABELS: dict[str, str] = {
    "req": "需求",
    "design": "设计",
    "dev": "开发",
    "verify": "验证",
}

# `stage` 的**两个内建默认**（都只在上面这套内建板里有意义；自定义 boards.json 时由
# `Config.entry_stage` / `Config.default_stage` 从板序**派生**，见 `disflow/config.py`）。
#
# 为何要分开两个名字：它们回答的是两个不同的问题 ——
#   - `ENTRY_STAGE`：**root 容器**落在哪块板。root 是树的自身目标，按流程语义该在**入口**。
#   - `DEFAULT_STAGE`：**任务**没声明 `stage` 时落在哪块板。内建流程里是「开发」
#     （大多数节点是干活的），这个选择**不是**从板序能推出来的 —— 所以它是个显式策略值。
# 两者的**唯一权威**是 `boards.json`；这里这份是「两个文件都不存在」时的回落。
# 注意：`state.py` 的 reducer 只把这两个名字当**重放旧事件流的兜底**（早期 `tree.init`
# 不带 stage 字段），改它们会改变历史事件的重放结果 —— 不要为了让新配置生效而改这里。
ENTRY_STAGE: str = STAGES[0]
DEFAULT_STAGE: str = "dev"

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
    "tree.init", "tree.goal", "tree.deadline", "tree.boards",
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
