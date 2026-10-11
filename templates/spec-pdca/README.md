# spec 模板（每个功能一份，内部按 PDCA）

**一个功能 = 一个 spec 文件，里面按 PDCA 分四段。**

把本目录拷到项目根改名为 `specs/`：

```text
specs/
├── README.md            # 本文件
├── constitution-模板.md # 项目铁律的**模板**（复制改名为 constitution.md；可选，一份，很少动）
├── 用户注册.md          # 一个功能一份（从 `SPEC-模板.md` 复制改名而来）
└── 订单导出.md          # 另一个功能
```

**建一个新功能的 spec**：把本目录的 **`SPEC-模板.md`** 复制成 `specs/<功能名>.md` 即可。
项目级铁律则把 **`constitution-模板.md`** 复制成 `specs/constitution.md`（可选，但推荐）。
（本目录里的 `README.md` 是使用说明，不用拷——或者拷了也不影响。）

## 为什么是 PDCA 四段

本模板的栏目**取自 OpenSpec**（`spec-driven` schema 的 proposal / specs / design / tasks
四个模板正文），按 **PDCA** 重组。两者不是同一东西：

| PDCA | 来源 | 产物 |
| --- | --- | --- |
| **P** 计划 | OpenSpec 的 **proposal / specs / design / tasks** 四件收进 P 段 | `P1…P5` |
| **D** 执行 | OpenSpec 的 `apply` + `tasks.md` 勾选框 | `D 执行` |
| **C** 检查 | **OpenSpec 缺这一段**（它的 `verify` 是**可选 profile**，不在核心五步里）| `C 检查`（本模板做成必填）|
| **A** 改进 | OpenSpec 的 `archive`（本模板扩成「改进 + 触发条件」）| `A 改进` |

P 段为什么有五个子节：那正是 OpenSpec 四件各自的栏目，不能混：

| 子节 | 对应 OpenSpec | 关键约束 |
| --- | --- | --- |
| `P1 为什么做` | `proposal.md` 的 `## Why` | 1–2 句 |
| `P2 要什么` | `proposal.md` 的 `## What Changes` / `## Capabilities` / `## Impact` | 破坏性变更标 `**BREAKING**` |
| `P3 需求场景` | `spec.md` 的 `Requirement` + `Scenario` | **WHEN/THEN**；**场景恰好四个 `#`**；用 SHALL/MUST |
| `P4 怎么做` | `design.md` 的 Context / Goals / Decisions / Risks / Open Questions | **决定要列「被否的替代方案」**；简单改动可跳过本段 |
| `P5 任务` | `tasks.md` | **每任务自带「怎么验」**；禁止把测试堆到最后一组 |

## 怎么用

**滚动填充**——不是开工前一次写完：

```text
建文件      → 只写「P 计划」（D/C/A 留空）
边做边记    → 往「D 执行」追加（做了什么、哪里偏离了计划）
验收时      → 把命令和输出贴进「C 检查」
收尾时      → 写「A 改进」（带触发条件）
```

**为什么滚动**：「D 执行」开工前根本无法写；先填满就变成"填空表"，不是过程记录。

### 改动多次怎么办（防文件无限增长）

一个功能改很多次时，「D/C/A」不能无限追加。按改动大小分两路：

| 改动规模 | 怎么做 |
| --- | --- |
| **小改**（bug 修、小调整） | 直接在现有文件的 D 段**追加一行**，C 段补命令与输出 |
| **大改**（新一期需求） | 把上一期的 D/C/A **整段移入文件底部的「历史」折叠区**，从新的 P 段重新开始 |

**为何不学 OpenSpec 的 `changes/` 目录**（每个变更一个文件夹 + `archive/`）：
那是**每个变更一份**，与「每功能一份」是不同的取舍。本模板选后者——**认知负担低**
（PDCA 就在一个文件里），代价是**一个文件会变长**，所以需要上面这条分路规则。
若你更看重「每个变更可单独审阅 + 完整归档」，用 OpenSpec 那种结构更合适。

## 配 disflow 用（可选）

「P 计划」里的任务直接映射成 disflow 节点，`done` 时引擎会**真跑**验收命令并**自算**证据：

```bash
disflow init 用户注册 --goal "<P 段的一句话目标>"
disflow expand 用户注册 root --children '[
  {"id":"t1","goal":"<任务描述>",
   "acceptance":{"cmd":"<C 段的验收命令>","expect":"exit0"},
   "touch_plan":["<会改的文件>"]}
]'
disflow host-next 用户注册
```

`C 检查` 段就是这些证据的存档——**引擎算的，不是 worker 自述的**。

## 反面清单（实测踩到的）

| 别做 | 为什么 |
| --- | --- |
| 「C 检查」写"已测试通过" | 不可审计。**贴命令 + 输出** |
| 「P 计划」里写死实现细节 | 混掉 WHAT/HOW；改实现时要动需求 |
| 写「保证安全」「性能要好」 | AI 无法执行，人也无法验收。**量化** |
| 「A 改进」写成待办列表 | 没触发条件的待办 = 永远不做。**带条件** |
| 开工前把四段填满 | D 段无法预写；填满 = 走形式 |
