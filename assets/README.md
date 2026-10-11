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

## registry.json 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `type` | ✓ | `code` / `test` / `script` / `doc` / `board` / `report` / `config`（文件形态） |
| `category` | 可选 | 语义类别（与 type 正交）：`requirement` / `design` / `implementation` / `verification` / `process` / `documentation` / `config` |
| `path` | ✓ | 相对仓根；文件或目录（目录取全树） |
| `purpose` | ✓ | 一句话：给谁、解决什么。空 → `scan` 拒收 |
| `tier` | ✓ | 见上 |
| `evidence` | `tier=verified` 时 ✓ | 可跑命令（字符串） |
| `premises` | 可选 | 依据指针：本资产的断言建立在这些资产 id 之上 |
| `owner` | 可选 | 产出它的 disflow 树/节点（追溯用） |

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
| **范围**（改动后刷哪些） | 改到的资产 ∪ `impact` 下游 |
| **时机**（什么时候刷） | 工作流事件：node-done / 收尾 |

`spec`（节点验收点）**不**管顺序——它最多管**门**：「本节点完成时，它碰到的资产已 `scan` 且 `check` 通过」。

## 决策记录（Jev 定，低置信/高风险才上人）

| 日期 | 决策 | 判据 | 结果 |
| --- | --- | --- | --- |
| 2026-10-11 | 引入 `category` 类别轴（与 `type` 正交） | jev `choice` conf 0.64 + 可逆低风险 | **加字段** |
| 2026-10-11 | 信任**不**沿依据边传递（保持一跳） | jev `choice` conf 0.89；传递 = 推测传染（一个根动一次刷红全图） | **保持一跳** |
| 2026-10-11 | 刷新顺序由 premises 拓扑序**派生**，不用手写 spec | jev `choice` conf 0.95；`noul`(spec 管顺序合适)=0.20 | **派生** |

## 已知局限（诚实声明，别当没有）

- 用 **git 提交时间 + 工作区脏**判 stale，不追踪语义变化：浅层改动也会触发 stale（**宁可误报**，
  不放过漂移）；未 commit 的新文件依赖 `git status` 而非 mtime。
- 不建跨项目/企业级聚合（YAGNI，无真实信号；出现再议）。
- 不接 disflow 引擎自动消费（`tree.assets` 是**任务产出物**，本登记是**项目资产**，两者不同层）。
