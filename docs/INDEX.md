# disflow 文档索引（`docs/` 单一入口）

> **找文档先看这里，不靠记忆。** 本仓所有 `docs/*.md` 必须在下面登记 ——
> `tests/test_docs_index.py` 守两件事：① 有文档没登记 ⇒ 红；② 登记了不存在的文件 ⇒ 红。
> 这条就是「**不登记 = 永不被读到**」，与资产登记同一纪律。
>
> **文档是资产**：多数已在 `assets/registry.json` 登记（`category` = requirement / design /
> process / documentation）。本索引是**人的入口**，registry 是**机器的入口**（信任/依据走它）。

## 1. 分类（Diátaxis 裁剪 + ADR）

文档混乱的常见病根是**把不同读者/不同生命周期的东西混在一处**。按「回答什么」分：

| type | 回答什么 | 生命周期 |
| --- | --- | --- |
| `requirements` | 要做什么、验收怎么算 | 现状（改 = 改需求） |
| `design` | 怎么设计（**现状**，不含重设计史） | 现状 |
| `reference` | 上手 / 命令 / 接口的确切定义 | 现状 |
| `process` | 这次是**怎么做的**（开发流水） | 随流程推进 |
| `decision` | 一个决定 + 理由 + 后果（ADR 形状） | 冻结（改了另起一篇，不修订） |
| `report` | 一次性评估 / 审计（有读数、有日期） | **历史**（写完不再改） |

## 2. 清单

| 文档 | type | status | 一句话 |
| --- | --- | --- | --- |
| `README.md` | reference | current | 5 分钟上手 + 命令 + 结构（代码块被 `test_readme_walkthrough` **真的执行**） |
| `docs/INDEX.md` | reference | current | 本索引 |
| `docs/requirements.md` | requirements | current | FR-1..29 + NFR-1..7 + 验收判据（§7 覆盖审计） |
| `docs/design.md` | design | current | 设计现状：数据模型 / 就绪 / 分发 / 门 / 看板（§6.1 逐树看板） |
| `docs/architecture.md` | design | current | 架构图（Mermaid 纯文本，零依赖渲染） |
| `docs/design-dispatch-tree-graph.md` | design | stub | 分发器 / 树 / 图 **专项**设计（未实现） |
| `docs/dev-board.md` | process | current | 开发看板 + dogfooding 流水（本仓第一份狗粮） |
| `docs/v0.2-backlog.md` | decision | current | 停放项 + **触发条件**（不是待办；没触发条件就不动） |
| `docs/gap3-evaluation.md` | report | historical | gap3 收益验证（量化/迭代/worktree 的实测读数） |
| `docs/self-harden-evaluation.md` | report | historical | self-harden 这批改动的收益评估 |
| `docs/self-harden-lessons.md` | report | historical | self-harden 踩过什么 / 怎么发现 / 下次怎么避免 |
| `docs/fr-nfr-gap-report.md` | report | historical | FR/NFR 缺口与机检报告（`scripts/fr_check.py` 生成） |
| `docs/decisions/README.md` | reference | current | 决策记录（ADR）规矩 + 索引（一决定一篇，写入即冻结） |
| `docs/decisions/0001-运行视图-v8-可用性四修.md` | decision | superseded | v8：追加式刷新 / 终态行不打 goal / 默认只列未完成 |
| `docs/decisions/0002-看板-v9-默认只列运行中加待完成.md` | decision | active | v9：默认只列运行中+待完成，事实不丢 |
| `docs/decisions/0003-运行视图-v10-按板列不按树.md` | decision | active | v10：看板 = 阶段列，树不是一块板（当前形态） |
| `docs/decisions/0004-ai-入口先读索引.md` | decision | active | AI 入口：动手前先读 docs/INDEX.md + assets/INDEX.md（仓根 AGENTS.md 兜） |

## 3. 增 / 改 / 删 的规矩

| 动作 | 规矩 | 谁守 |
| --- | --- | --- |
| **新增** | 先在本表登记（`type` / `status` / 一句话），否则测试红 | `tests/test_docs_index.py` |
| **修改** | `design`/`requirements`/`reference` 改**现状**；`report` 是**快照**，不改 | 人 |
| **删 / 归档** | 一次性的 `report` 不再被引用即可归档（删或移 `docs/archive/`）；**先删登记行**，否则「登记了不存在的文件」红 | `tests/test_docs_index.py` |
| **重设计史** | **不进** `design.md`（那里写**现状**）—— 进 `docs/decisions/`（ADR，一决定一篇、写入即冻结）或 `dev-board.md` 流水 | 人 + `test_docs_index.py` |

**冗余的判据**：同一**事实**出现在两处即冗余（例：板定义只在 `boards.py`/`boards.json`，
`design.md` 只**引用**不复制）。文档里贴计数会漂 —— 计数要么由脚本生成（如
`fr-nfr-gap-report.md`），要么被测试对着实物断言（如 README 的用例数/变异数）。
