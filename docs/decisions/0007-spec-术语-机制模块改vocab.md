# 0007 spec 术语收口：机制常量改 `STORAGE`、机制模块改 `vocab`

- **Status**：Active
- **Date**：2026-10-11
- **关联**：[0006](0006-spec-一等公民.md) 把 **Spec** 定为交付单元实体（交付规格）；本篇把
  「spec」一词**收口到只剩这一个含义**，清掉其余历史别名。

## Context

[0006](0006-spec-一等公民.md) 的 Context 已点名：本仓「Spec」一词**多义** —— `disflow/spec.py`
的 `SPEC`（机制常量）、节点 `acceptance`（节点验收点）、`assets/registry.json` 的 `spec` 块
（交付单元）。用户裁决（2026-10-11）：

> 「spec 术语明确下，就是**交付规格 / spec 实体**。不是 spec 常量。`disflow/spec.py` 的 `SPEC`
> 常量换个术语比较合适吧。」

即：**spec = 交付规格（交付单元实体）**，其余用法都是撞名，必须清。

现状盘点（本轮实测）：

| 撞名处 | 指什么 | 处置 |
| --- | --- | --- |
| 常量 `disflow/spec.py::SPEC` | 磁盘存储布局（`state.dir`/`env`/`events`/`projection`/`checkpoints`） | 改名 |
| 模块 `disflow/spec.py` | 本实例的机制数据 + 内建默认（阶段 / 角色 / 事件闭集 / 提问纪律…） | 改名 |
| 参数 `spec: dict`（`store` / `worktree` / `Engine.spec`） | 同上「存储布局」 | 改名 |

## Decision

**`spec` 一词专指 0006 的交付规格 / Spec 实体**；下列三处改名（jev 判定，见判据）：

| 旧 | 新 | 含义 |
| --- | --- | --- |
| 常量 `SPEC` | **`STORAGE`** | 磁盘存储布局 |
| 模块 `disflow/spec.py` | **`disflow/vocab.py`** | 本实例的机制数据 + 内建默认（词汇集中地） |
| 参数 `spec` | **`storage`** | `store` / `worktree` / `Engine.storage` / `cli` 里承载 `STORAGE` 的形参 |

**判据（jev，本机 `jev-only`）**：

- `choice`「模块是否改名」：首轮 **0.5/0.5（confidence 0.0，判不了）**；补入用户明确意图
  「spec 专指交付规格」后 **重命名 0.69 / 保留 0.31（confidence 0.38）** ⇒ 改名。
- `noul`「保留模块名是否构成真实歧义风险」：**0.36**（否）—— 与上一条 framing 相反，
  说明本决定**低置信**；最终以「用户显式意图 + 本仓 AI 优先（术语撞名会持续误导读者/AI）」
  这条**决定性事实**为准。
- 模块新名 `choice`：`vocab.py` **0.55** / `instance.py` 0.23 / `mechanism.py` 0.21 / `defaults.py` 0.01。

**保留（不同概念，不属本次）**：节点验收 `model.validate_node_spec`（节点声明字典）、
`cli` 的 boards 参数、`assets/registry.json` 的 `spec` 块（**后者正是交付规格，是唯一正确用法**）。

## Consequences

- **代码**：`disflow/spec.py` → `disflow/vocab.py`（`git mv`）；`SPEC` → `STORAGE`；
  `store`/`worktree`/`engine`/`cli` 的 storage 形参改名；`__init__.__all__`、
  `tests/test_layering.py` 的针脚、`scripts/{fr_check,mutation_check,goal_check,seed_dev_board}.py`
  随之同步。
- **文档**：`docs/design.md` §9.1、`docs/architecture.md`、`README.md` 引用改
  `vocab.py`；`docs/fr-nfr-gap-report.md` 由 `fr_check.py --report` 重生成。
- **不改历史件**：`docs/dev-board.md` / `gap3-evaluation.md` / `self-harden-evaluation.md` /
  本 `decisions/` 诸篇是**快照/冻结**，保留当时的 `spec.py` 字样，不追溯改写。
- **术语边界澄清**：`disflow/vocab.py` 的 docstring 显式声明「本模块的 spec（机制数据）≠
  0006 的 Spec 实体」。
