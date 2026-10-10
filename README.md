# disflow

任务分发流引擎：**树提供上下文继承，图提供调度与引用，看板是视图，分发器只推不跑。**

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求 v0.1：mgt 复盘 + 可验收 FR/NFR |
| `docs/design.md` | 设计 v0.1：数据模型、`ready()`、分发器、看板、门 |
| `docs/dev-board.md` | **开发看板种子**（按它自己开发） |
| `docs/design-dispatch-tree-graph.md` | 早期讨论稿（历史，已被 design.md 取代） |

## 核心判断

- **分发器，不是多保活 agent**：保活的是进程，不是 agent 上下文；每个任务 fresh context，从树祖先链注入。
- **树管上下文继承（唯一父、无环、有限）；图管调度与引用（`requires` 必须 DAG）**，不混用。
- **看板是投影，不是第二份存储**；多块板 = 多棵作用域隔离的树。
- **引擎只推不跑**，无常驻 daemon。

## 先例（fat3 仓内，可直接复用）

- `scripts/flowcore/` — 语言中立树引擎（store/fsm/gates/forms/tree），`drive` 已是“只推不跑”。
  缺的正好是**图层**（`ready_siblings` 只有 `(parent, phase)`，投影无依赖字段）。
- `skills/kaa/` — `handoff brief|collect`、`bus` 分层异步、`job` 租约、`flock` 真互斥。
- `skills/mgt/` — 被本次重做的前身：树/图/看板/门都有，但**双源、双状态机、多 daemon、树图混用**。

## 状态

需求 + 设计已落盘，**待评审**（`docs/dev-board.md` 的 D0）。评审通过后按开发看板进入 W1。
