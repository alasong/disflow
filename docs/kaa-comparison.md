# disflow vs kaa：机制对照（实测）

> **本文件的定位**：回答「disflow 的机制和 kaa 差多少」。
> 数据**全部实测**（非印象），取数命令写在每节里；`premises` 挂在 `pkg` 上 ⇒
> disflow 的代码一变，本文件自动 `stale`（回源码对，见 `assets/README.md`）。
> kaa 侧是**外部参照物**（`~/.pi/agent/skills/kaa/`）——它**不参与 premises 图**，
> 故 kaa 自己变化**不会**触发本文件 stale，需人工回查（这是本文件的已知局限）。

## 0. 一句话

> **disflow 是「调度器」，kaa 是「质量机」。** 两者是**并列关系**（各管各的项目、
> 各有独立事件流），不是主从、更不是可以合并的（见 [ADR 0010](decisions/0010-kaa融合-声明式.md)）。
> disflow 强在**编排与治理**，kaa 强在**执行质量与自演进**。

## 1. 规模（实测）

```bash
# disflow
wc -l disflow/*.py | tail -1          # 8331 行
ls disflow/*.py | wc -l               # 20 模块
grep -c 'node = add("' disflow/cli.py # 43 动词
python3 -c "import sys;sys.path.insert(0,'.');from disflow.vocab import EVENT_KINDS;print(len(EVENT_KINDS))"  # 34
# kaa
wc -l ~/.pi/agent/skills/kaa/kaa.py ~/.pi/agent/skills/kaa/engine/*.py | tail -1  # 4351 行
wc -l ~/.pi/agent/skills/kaa/spec/kaa.toml                                       # 1290 行
```

| 维度 | disflow | kaa | 差 |
| --- | --- | --- | --- |
| 代码行数 | **8331** / 20 模块 | 4351 / 7 模块 | disflow **1.9×** |
| 规格文件 | 0（配置散在 `boards.json`/`disflow.json`） | **1290 行** `spec/kaa.toml` | kaa 独有 |
| CLI 动词 | **43** | 15 | disflow 多 28 |
| 事件 kind | 34 | 28 | 相当 |
| 门 | 7 类（+三新门） | 7 道 | 数量相当 |
| 测试用例 | **943** | 302 | disflow 3.1× |
| 规模预算 | `size_budget=400`（软，可 `size_waiver`） | `loc_hard=4222` + `reserve=100`（硬，上修需三步记账） | **机制差最大的一项** |

## 2. disflow 有、kaa 没有（实测）

| 能力 | 依据 | 为何 kaa 无 |
| --- | --- | --- |
| **多树 + 跨树 `awaits` 信号** | FR-22/23；`disflow/engine.py` `closed_trees` | kaa 是单树（`slug` 维度） |
| **看板策略挂在列上** | [ADR 0005](decisions/0005-看板提升为一等实体.md) 策略层（WIP/默认验收）+ `views.board_layout`；**结构层已由 [ADR 0015](decisions/0015-板非顶层实体.md) 降级**（板非顶层实体） | kaa 无「板」概念 |
| **WIP 逐列并发上限** | FR-30；`dispatch.drive` 的 `deferred` | kaa 无并发上限 |
| **验收策略挂在板上（逐列默认）** | FR-31；`Engine._effective_acceptance` | kaa 的门由 `node_type` 决定 |
| **web 入口** | [ADR 0011](decisions/0011-web入口-实现.md)；`disflow/web.py` | kaa 无 |
| **项目资产图**（`premises` + `stale` 派生） | `assets/registry.json`；`scripts/assets_scan.py` | kaa 的 `registry` 是**产出物台账**，不同层 |
| **空洞通过识别** | `gates.vacuous_reasons` | kaa 无（这是 disflow 独有的设计） |
| **需求↔实现双向机检** | `scripts/fr_check.py`（30 条）+ `goal_check.py`（22 条） | kaa 有 `derive.py` 静态检查，但无「需求覆盖」这一轴 |

## 3. kaa 有、disflow 没有（实测）

| 能力 | kaa 的实现 | disflow 的替代 | 状态 |
| --- | --- | --- | --- |
| **逐型开工模板** | `[forms]` + `engine/forms.py#type_template` | ❌ 无（`brief()` 是统一结构） | [ADR 0010](decisions/0010-kaa融合-声明式.md) 标「待做」，**受阻**于需先定 disflow 的节点型 |
| **方法论注入**（该读哪篇） | `forms.methodology_hint(phase, mode)` | ❌ 无 | 未评估 |
| **人机通道** | `bus`（ask/answer + `orphans` 超时检测） | ⚠️ 只有 `question_protocol` 一句话 | ADR 0010 **明确不做**（单机定位不成立） |
| **逐码风险分档** | 1.4 增量：门强度可按**拒收码**调 | ❌ 无（门是开/关） | 未评估 |
| **经验账本** | `state.ledger_add` / `methodology_usage` | ❌ 无 | 未评估 |
| **自演进** | `kaa evolve`（策略/度量/进化） | ❌ 无 | 未评估 |
| **硬规模预算 + 记账上修** | `loc_hard` + `[justifications]` 三步判据 | ⚠️ 软预算 + `size_waiver` | 机制取向不同 |
| **`goal_align` 精确版** | 与树目标词对齐 | ✅ **本轮已补**（简化版，见 ADR 0012） | 已闭 |
| **`code_quality`** | AST 底线四查 | ✅ **本轮已补** | 已闭 |
| **`verify_real`** | 假证据拒收（逐码分档） | ✅ **本轮已补**（硬门版，未分档） | 部分闭 |

## 4. 差的是什么（结论）

**不是「谁多谁少」，是两个不同的问题域**：

| | disflow | kaa |
| --- | --- | --- |
| 回答的问题 | **任务怎么有序地派出去** | **派出去的活怎么保证质量** |
| 核心机制 | 树/图/看板/波次/租约/多树隔离 | 门/模板/分档/账本/自演进 |
| 治理面 | ✅ 强（需求对账、资产图、看板策略） | ⚠️ 弱 |
| 质量面 | ⚠️ 弱（本轮补了三道门，仍无分档/账本） | ✅ 强 |

**disflow 缺的三类，按可用性排序**：

1. **模板层**（`[forms]` 按型给模板）—— **最该补**：它直接对应「用 spec 让 AI 梳理先做什么」
   这个用户诉求；前置是先在 disflow 内定节点型集合。
2. **自适应层**（逐码分档 / `evolve`）—— 价值明确但**触发条件未出现**（disflow 还没有
   足够多的拒收码样本来谈分档）。
3. **人机通道**（`bus`）—— **已裁决不做**（ADR 0010）：disflow 是单机「只推不跑」，
   提问走宿主通道已够。

**而 disflow 有的那 8 项，kaa 一项都没有** —— 这不是 kaa 弱，是 kaa 不解决那类问题。
