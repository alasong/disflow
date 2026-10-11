# 项目资产登记（`assets/`）

> **P1 模型契约** · 手写事实源 `registry.json` + 生成索引 `index.jsonl` / `INDEX.md`。
> 生成器：`scripts/assets_scan.py`（stdlib-only，与 NFR-1 一致）。

## 为什么（两条判据，对应两个诉求）

1. **AI 优先**：资产的存在理由是让 AI **不必整读代码**就能定位。故产物必须机读、稳定 key、薄
   （`index.jsonl` 一行一资产；`INDEX.md` 仅供人/AI grep）。这是对旧「人读报告」定位的替换，
   不是补充。
2. **可信依赖**：一条资产的内容/断言建立在别的资产之上（`premises`，依据指针）。被依据的资产
   变了，本资产**自动降级为 stale**——不需要人记得去改。

## 三个概念（不许混）

| 概念 | 谁定 | 取值 | 说明 |
| --- | --- | --- | --- |
| **资产 / 档案** | 人 | — | 有下次消费者、可被引用 → **资产**（进本登记）；一次性过程文件 → **档案**（不进） |
| `tier` | 人（自估） | `verified` / `asserted` / `stub` | 对应 `playbooks/claim-tiering.md` 的 `[跑]` / `[读·推]` / 未实现 |
| `trust` | **派生**，不手写 | `verified` / `asserted` / `stub` / `stale` | 由 `premises` 保鲜度决定 |

**`tier` 是自称，`trust` 是实得。** `tier=verified` 必须给 `evidence`（可跑命令），否则 `scan` 拒收。

## Spec 与资产：**关联**，不是归属（ADR 0006）

**Spec**（交付单元，一等公民）通过**关联**引用资产：`Spec → 资产` **多对多**、可带类型
（requirement / design / implementation / verification / documentation）。**资产不挂在 Spec 下**——
没有 `asset.parent = spec`：资产独立存在、可被多个 Spec 引用（**可复用**正是它成为资产的判据）。

- **管**：本 Spec 交付的**角色清单** + 完成判据（gate）+ 关联了哪些资产；
- **不管**：资产刷新**顺序**（派生自 `premises` 拓扑序，见「刷新顺序」一节）、任务的就绪/调度
  （归 disflow 引擎）。Spec **不得成为第二真相源**。

## registry.json 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `type` | ✓ | `code` / `test` / `script` / `doc` / `board` / `report` / `config`（文件形态） |
| `category` | 可选 | 语义类别（与 type 正交）：`requirement` / `design` / `implementation` / `verification` / `process` / `documentation` / `config` |
| `path` | ✓ | 相对仓根；文件或目录（目录取全树） |
| `purpose` | ✓ | 一句话：给谁、解决什么。空 → `scan` 拒收 |
| `tier` | ✓ | 见上 |
| `evidence` | `tier=verified` 时 ✓ | 可跑命令（字符串） |
| `premises` | 可选 | 依据指针：本资产的断言建立在这些资产 id 之上。条目 = 纯 id（默认 `anchors`）或 `{"id":..,"kind":..}`（**分型**，见下） |
| `spec` | 可选 | **Spec 块**：本资产是一个**交付单元**。`required_roles`（非空角色数组）+ `delivers`（已登记资产 id 数组）→ 参与 `spec_coverage` / `delivery` |
| `owner` | 可选 | 产出它的 disflow 树/节点（追溯用） |

### 依据边的分型（`premises` 条目语法）

`premises` 条目两种形状（`anchors` 是默认）；同一串里混用合法：

| 形状 | `kind` | 含义 |
| --- | --- | --- |
| `"pkg"` | `anchors`（默认） | 正确性锚在它上 |
| `{"id":"pkg","kind":"generated_from"}` | `generated_from` | 本资产由它生成 |
| `{"id":"tests","kind":"verifies"}` | `verifies` | 本资产验证它 |
| `{"id":"design","kind":"documents"}` | `documents` | 本资产记录它 |

**四种都驱动 `stale`**（依据物变了，本资产都要复查）——`kind` 是**语义标签**，
不改变保鲜判定；非法 `kind` / 缺 `id` → `scan` 拒收。

## 派生规则（机械可查）

```text
dirty(a)   = a.path 有未提交改动（git status --porcelain）
newer(p,a) = last_commit(p) > last_commit(a)
stale_basis(a) = [ p ∈ premises(a) : dirty(p) ∨ newer(p,a) ]
trust(a)   = "stale"  if stale_basis(a) else tier(a)
```

`hash(a)`：文件 = `git hash-object`；目录 = 各文件 blob hash 排序后聚合。
图必须**无环**（`premises` 成环 → `scan` 拒收，与 `parent` 链成环同判据）。

## 产物分工

| 文件 | 谁写 | 进 git | 消费方 |
| --- | --- | --- | --- |
| `registry.json` | 人 | ✓ | 事实源 |
| `index.jsonl` | `assets_scan.py` | ✓ | AI/工具（一行一资产，机读） |
| `INDEX.md` | `assets_scan.py` | ✓ | 人/AI 快速 grep |

## 刷新顺序（执行任务时）

**顺序 = premises 拓扑序（依据先、被依据后），由 `assets_scan.py order` 派生——不手写。**
（Jev choice conf 0.95：手写 spec 会成第二事实源 → 漂移。）

三件事必须分开，别混：

| 问题 | 谁管 |
|---|---|
| **顺序**（先刷谁） | premises DAG 拓扑序 → `assets_scan.py order` |
| **范围**（改动后刷哪些） | 改到的资产 ∪ `impact` 下游 → `assets_scan.py plan` |
| **时机**（什么时候刷） | 工作流事件：node-done / 收尾 |

引擎口径的**节点验收点**（也叫 spec）**不**管顺序——它最多管**门**：「本节点完成时，它碰到的资产已 `scan` 且 `check` 通过」。资产层的 `spec` **块**同理（见上），也不管顺序——它管的是**交付角色齐不齐**（`delivery`）。

**执行任务时一条命令闭环**：

```bash
python3 scripts/assets_scan.py plan     # 范围：工作区改动 → 要刷的资产（含下游，已按拓扑序）
python3 scripts/assets_scan.py scan     # 刷新
python3 scripts/assets_scan.py check    # 收尾门（有 stale / 缺证据 → exit 1）
```

## 决策记录（Jev 定，低置信/高风险才上人）

| 日期 | 决策 | 判据 | 结果 |
| --- | --- | --- | --- |
| 2026-10-11 | 引入 `category` 类别轴（与 `type` 正交） | jev `choice` conf 0.64 + 可逆低风险 | **加字段** |
| 2026-10-11 | 信任**不**沿依据边传递（保持一跳） | jev `choice` conf 0.89；传递 = 推测传染（一个根动一次刷红全图） | **保持一跳** |
| 2026-10-11 | 刷新顺序由 premises 拓扑序**派生**，不用手写 spec | jev `choice` conf 0.95；`noul`(spec 管顺序合适)=0.20 | **派生** |
| 2026-10-11 | 加 `plan` 命令（改动→要刷的资产集，一步出范围+顺序） | jev `choice` conf 0.97 | **加** |
| 2026-10-11 | `premises` 支持**关系分型**（`anchors`/`generated_from`/`verifies`/`documents`）+ 资产带 **`spec` 块** + `delivery` 交付门（缺角色 rc=1） | 四词皆**驱动 stale**（kind 只作语义标签）；交付门判「角色齐不齐」与成熟度（`tier`）**两轴不混** | **加** |
| 2026-10-11 | `dev-board` / `lessons` 归为**档案**、移出登记 | jev `choice` conf 0.77；两者是过程记录，被测试当**夹具**读（非可复用资产）；其 premises 挂活 `pkg`/`tests` ⇒ 任何代码改动都令它们 stale ⇒ `check` 永久红（正是「把档案当资产」反模式）。活耦合已由 `test_typecheck`/`test_docs` 直接守，不靠 stale 提醒 | **移除** |

## 已知局限（诚实声明，别当没有）

- 用 **git 提交时间 + 工作区脏**判 stale，不追踪语义变化：浅层改动也会触发 stale（**宁可误报**，
  不放过漂移）；未 commit 的新文件依赖 `git status` 而非 mtime。
- 不建跨项目/企业级聚合（YAGNI，无真实信号；出现再议）。
- 不接 disflow 引擎自动消费（`tree.assets` 是**任务产出物**，本登记是**项目资产**，两者不同层）。
