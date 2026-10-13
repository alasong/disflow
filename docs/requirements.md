# disflow 需求 v0.1

> 状态：**需求（待评审）** · 2026-10-10
> 来源：fat3 `skills/mgt` 复盘 + 本线关于「分发器 / 树 / 图 / 看板」的讨论
> 配套：`docs/design.md`（设计）· `docs/dev-board.md`（开发看板种子）

---

## 0. 定位

disflow 是一个**任务分发流引擎**：以**看板**为一等实体（固定列结构 + 挂在板上的分发器与验收器），
用**树**在板内组织层级与上下文继承、用**图**描述调度与引用，
配一个**确定性分发器（只推不跑）**把就绪任务派给短命 worker。

它是 fat3 `skills/mgt` 的**系统性重做**，不是续写。

---

## 1. 背景复盘：mgt 做了什么、为什么「不够系统」

### 1.1 mgt 已有的能力（约 10.5k LOC / 9 个版本增量）

| 能力 | 实现 |
| --- | --- |
| 树 | 4 级工作项 `R/O/T/S` + `parent` + `depth` |
| 图 | `deps` 带类型边 `blocks/informs/contains`；`_topological_waves` **含环检测** |
| 调度 | `plan-loop` / `exec-loop` / `phase auto` / 按依赖波次 |
| 并发 | 每 TODO 一个 git worktree + `--touches` 写面冲突检测 |
| 跨角色 | `registry.json` 全局状态机 `new→dev_done→verified→approved / rejected / reopened→closed` |
| 角色 | dev / qa / pm 三 org，各一个 daemon + 文件消息目录 |
| 门 | `G2 Gate`（git clean + pytest）、worktree close 测试、git hooks |
| 视图 | Web dashboard、`org status`、架构层热力图 |
| 度量 | 故事点、velocity、burndown |
| 其他 | 设计文档模板、架构债务、知识沉淀、consult 咨询 |

**结论：mgt 不是「没做」，是「什么都做了、但没收敛」。** 它已经包含树、图、调度、看板、门——
问题出在它们**各长各的、彼此重叠**。

### 1.2 病根（八条，按危害排序）

| # | 病根 | 证据 | 后果 |
| --- | --- | --- | --- |
| P1 | **双源状态** | `state.json`（每 org 的 todos）+ `registry.json`（全局状态），靠 `_registry_sync_from_state` 同步 | 两份真相会漂移；文档却写「Registry 只有一个」 |
| P2 | **双状态机** | TODO 本地 `status`(pending/ready/iterating/done) ＋ 全局 `global_status`(new/dev_done/…/closed) | 同一实体两条生命周期，判定要同时看两处 |
| P3 | **树/图混用** | `deps` 的 `contains` 类型与 `parent` 表达同一件事 | 层级有两处真相，删一个不知道删哪个 |
| P4 | **多保活 daemon** | dev/qa/pm 各一个 `mgt-daemon` 轮询 state | 正是「多保活 agent 抢任务」反模式；空转、状态分散 |
| P5 | **就绪判断散落** | daemon tick / `exec-loop` pick / `phase auto` / `dep-resolve` 各算一遍 | 同一判据四处实现，必然分叉 |
| P6 | **手搓消息层** | `spawn/verify/results/fix/consult/events` 六种目录协议 | 与 `deps`/`status` 表达力重叠，协议无统一信封 |
| P7 | **阶段与角色混同** | `org=dev/qa/pm` 既是看板列、又是负责人、又是权限 | 分不清「在哪个阶段」和「谁负责」 |
| P8 | **无节点级统一门** | `G2 Gate` 只在 iteration close / worktree close | 单节点「完成」没有统一证据契约，易产生假完成 |

### 1.3 去留决策

| 保留（思路对） | 重做（形态不对） | 丢弃 |
| --- | --- | --- |
| worktree 单写者收口 | 双源 → **单一事件流** | 每 org 一个常驻 daemon |
| 依赖波次 + 环检测 | 双状态机 → **单一状态机 + 阶段节点** | `contains` 依赖（= `parent`） |
| 量化（points/velocity） | 树/图分担 → **树管上下文、图管调度** | 六种目录消息协议 → 统一信封 |
| 验证与开发分离 | 角色 → **role 与 stage 解耦** | `informs` → 归入 `relates` |
| 迭代/燃尽 | 门 → **节点级验收门 + 证据契约** | — |

---

## 2. 目标与非目标

### 目标

1. 一个**单一事实源**的决定论引擎：就绪、依赖、阶段、证据全部从一处派生。
2. 树与图**职责清晰、不重叠**。
3. 一个**只推不跑**的分发器，无常驻进程。
4. **看板是一等实体**：固定结构（列恒在，空看板也一样），并挂载分发器与验收器；
   树/图是看板**内**的组织与依赖维度。「多块板」= 多棵作用域隔离的树。
5. 从 mgt 可**迁移**，不要求推倒重来。

### 非目标

- 不做 Web 服务/数据库；文件式，零第三方依赖。
- 不做常驻调度 daemon。
- 不让 LLM 参与确定性判定（就绪/依赖/波次）。
- 不追求通用工作流引擎（不替代 Airflow/Temporal 的通用性）。
- 不在本期做多机分布式调度。

---

## 3. 功能需求（FR，均可验收）

### 3.1 单一事实源

- **FR-1**：所有状态来自一条 **append-only 事件流**。
- **FR-2**：可从事件流**重建投影**，重建结果与投影**逐字节一致**（`rebuild --check`）。
- **FR-3**：投影写用**原子替换**，并支持 `flock` 真互斥；并发写不得产生半个文件。

### 3.2 树（结构 + 上下文）

- **FR-4**：每个节点**至多一个 `parent`**；提供祖先链 `root→…→parent` 查询。
- **FR-5**：禁止出现「层级」的第二表达（无 `contains` 边）；层级只由 `parent` 定义。
- **FR-6**：节点带 `stage`（看板列）与 `role`（负责方），二者**独立**。
- **FR-7**：节点可声明 `context`（供后代继承的字段），祖先链投影**有限且确定**。

### 3.3 图（调度 + 引用）

- **FR-8**：调度依赖边 `requires` 必须构成 **DAG**；写入时即检测，有环**立即拒收**。
- **FR-9**：软引用边 `relates` 可任意（可成环），**不影响就绪**。
- **FR-10**：产出物边 `produces` / `consumes` 登记供给-消费关系；`consumes` 指向未登记项即拒。

### 3.4 就绪

- **FR-11**：存在**唯一** `ready(n)` 函数，分发器与 worker **共用**；同输入必同输出。
- **FR-12**：`ready(n)` 至少覆盖：状态为 pending、容器/叶子分型、`requires` 入边全 done、未被租约占用、本波写面不重叠。
- **FR-13**：`ready` 结果可 `--dry-run` 输出，供测试与人工核对。

### 3.5 分发

- **FR-14**：`drive` **只推不跑**：输出就绪波次 + 每节点建议动作；**不 spawn**。
- **FR-15**：`handoff brief` 一次写全必填声明/提问纪律/结果契约；`handoff collect` 校验结果契约后落盘，**不写 node-done**。
- **FR-16**：spawn 归宿主（人 / 父 agent / 编排层）；引擎不内置常驻进程。
- **FR-17**：触发方式支持 cron / 事件 / 显式三种，引擎自身**不常驻**。

### 3.6 门与证据

- **FR-18**：任务节点 `done` 前必须过**验收门**（可执行命令 + 期望输出匹配）。
- **FR-19**：证据元数据由引擎**自算**（如 git 改动集、符号），**不手抄**。
- **FR-20**：拒收必须给出**缺项清单 + 契约片段**，不是只给错误码。

### 3.7 看板 / 视图

- **FR-21**：看板是一等实体：有稳定 `id` 与**固定列结构**，随事件流持久化、可重放；
  **空看板同样存在**（列恒在，不因没有任务而消失）。stage / 树 / 图三种视图是它的渲染，同源、不各存一份。
- **FR-22**：看板 : 树 = **1:1**；每棵树（=每块看板）一个权威分发者，作用域隔离。
- **FR-23**：跨看板（跨树）依赖**降级为显式完成信号**，不假装是一张图。
- **FR-30**：**分发器是看板的附属**：分发策略由看板（逐列）持有、`drive` 消费它；首个实例 = 逐列 **WIP 上限**（列内已在跑数达上限 ⇒ 该列其余就绪者进 `deferred`，节点不各自携带策略）。
- **FR-31**：**验收器是看板的附属**：验收策略由看板（逐列）持有；节点未自带 `acceptance` 时由该列默认验收派生（节点自带优先）。
- **FR-32**：**动态看板**：看板结构可由用户预先设定、可逐树覆盖（已实现为可选覆盖，见 §7）。

### 3.8 上下文与并发

- **FR-24**：worker 的 brief 由**祖先链 `context` + 前置节点产出摘要**生成，无需人工拼背景。
- **FR-25**：同波派发前做 `touches` 写面重叠检查，重叠者**自动排队**。
- **FR-26**：同资源由**单一写者收口**；并发取任务用**租约**防重。
- **FR-27**：`status` 能识别**卡死**（无 ready、无 running、却未全 done）。

### 3.9 迁移与兼容

- **FR-28**：提供 `import mgt` 从 `state.json` / `registry.json` 读入，映射为 disflow 节点。
- **FR-29**：映射关系写进文档（见 `design.md` §10），导入后 `ready` 结果可人工核对。

### 3.10 Spec（交付单元）

- **FR-33**：**Spec 是一等公民**：作为独立的**交付单元**实体存在——稳定 `id` + `goal` +
  **完成判据（gate）** + 状态，有独立生命周期；随事件流持久化、可重放。Spec **自身也是资产**（可复用记录）。
- **FR-34**：**Spec 关联资产，不是归属**：`Spec → 资产` 是**多对多、可带类型**的关联
  （一份资产可被多个 Spec 引用）；资产独立存在，**无** `asset.parent = spec`。Spec 声明它交付的
  **角色清单**（requirement/design/implementation/verification/documentation），「全量交付」= 角色齐全。
  （**两层均已落地**：资产层 = `assets/registry.json` 的 `spec` 块 + `assets_scan.py delivery` 门；
  引擎层 = `spec.*` 事件 + 顶层 `specs` 投影 + CLI，见 FR-33。）
- **FR-35**：**建树有判据、跨树等待有自检**：`init` 时若已有 `closed` 的树 ⇒ 提醒
  「这可能是它的下一阶段」并给出可照抄的 `reopen` + `expand`；`doctor` 报出声明了 `awaits`
  指向另一棵树的树对（`awaits` = 承认依赖 ⇒ 本该是同树内的 `requires`）。
  **只提示不拒收**（目标是否独立是语义问题，词面/结构信号都有假阳性）。见 `design.md` §6.3。
- **FR-36**：**节点可追溯（`why`）**：给定 `(树, 节点)`，把事件流里与它相关的记录
  **按原序**一屏打出（树级背景 `tree.init/close` + `require/start/collect/rescope/acceptance/`
  `retry/skip/fail/done`），含「谁执行（`lease.holder`）/ 干了什么（`result.summary`）/
  为何当口改（`reason`）/ 拿什么验（`acceptance.cmd`）/ 过没过（`evidence.exit`）」。
  **纯读**：不产生新事实、不落事件；无记录时**明说没有**（不打白屏）。
  为何要：事件流已是唯一真相，但「出问题找谁去」原本要把散在多个 kind 里的字段**人工拼**——
  手拼只有会写 Python 的人能看。见 `design.md` §6 `views.trace_node`。

---

## 4. 非功能需求（NFR）

| 编号 | 需求 |
| --- | --- |
| NFR-1 | **零第三方依赖**，仅 Python 标准库 |
| NFR-2 | 核心**语言中立**：业务名词不进 core（沿用 flowcore 的判据） |
| NFR-3 | **文件式**：无数据库、无常驻服务；运行产物与源码分离（**按需启停的本地入口**不属此禁，见下方注） |
| NFR-4 | **可测试**：就绪/依赖/门是纯函数，可单测 |
| NFR-5 | **可断点续跑**：进程崩溃后由事件流恢复 |
| NFR-6 | **规模预算**：节点声明 `touch_plan`，改动超预算即拒（防范围蔓延） |
| NFR-7 | **不静默降级**：解析失败/写失败/坏事件行一律报错，不吞 |

> **NFR-3 的边界（2026-10-11 修订，机检实现对齐）**：本条管的是**数据与运行形态**——
> 「无数据库」是本体（事件流 + 投影就是存储层），「无常驻服务」指引擎**自身不保活**。
> **不禁**「按需启停的本地入口」（如 `disflow/web.py`：`python3 -m disflow.web` 才起，
> Ctrl-C 即停）——它不是常驻进程，判据也不该把 `http.server` 当符号禁。
> 常驻禁令的机械判据是 **FR-16**：服务的启动点必须在 `main` 保护下可达（AST 可达性）。

---

## 5. 验收判据：怎么算「系统」了

1. `rebuild --check` 对一棵真实任务树返回一致。
2. 一棵包含**跨子树依赖**的树，`ready` 输出与人工拓扑判断一致，且**不误判、不漏派**。
3. 给同一棵树、同一投影，**两次 `drive` 输出完全相同**（确定性）。
4. 一个节点在 `done` 时，**不给命令行证据无法通过**。
5. 从 mgt 导入一个真实项目后，`status` 能列出与 mgt 等价的待办与依赖。
6. 全程**无常驻进程**。

---

## 6. 术语

| 词 | 含义 |
| --- | --- |
| 节点 Node | 树里的一项工作（容器或任务） |
| 树 Tree | 由 `parent` 定义的层级，管**上下文继承** |
| 图 Graph | 由 `requires`/`relates`/`produces`/`consumes` 定义的边，管**调度与引用** |
| 看板 Board | **一等实体**：稳定 id + 固定列结构（空也在），随事件流持久化；挂载分发器与验收器 |
| 阶段 Stage | 看板列（需求/设计/开发/验证） |
| 角色 Role | 负责方（dev/qa/pm），与 stage 独立 |
| 就绪 Ready | `ready(n)` 为真 |
| 波次 Wave | 同一时刻全部就绪、且写面互不冲突的节点集合 |
| 门 Gate | 由看板策略派生的、节点 `done` 的验收判据 |
| 分发器 Dispatcher | 看板的调度附属（`drive` 只算不跑）；策略由看板持有 |

---

## 7. 实现覆盖审计（2026-10-10）

逐条核对 FR 与实现（证据 = 命令/测试文件，非自述）：

| FR | 状态 | 证据 |
| --- | --- | --- |
| FR-1/2/3 单一事实源 | ✅ | 事件流 + 原子投影 + flock；`tests/test_store.py`、`rebuild --check` |
| FR-4/5/6/7 树 | ✅ | `parent` 唯一、无 `contains`；`tests/test_core.py`、`test_import_mgt.py` |
| FR-8/9/10 图 | ✅ | 写时拒环；`consumes` 未登记即拒；`tests/test_engine.py` |
| FR-11/12 就绪 | ✅ | 唯一 `ready()`；`tests/test_core.py`、`test_multitree.py` |
| FR-13 就绪快照 | ✅ | `drive --json`（只读构造）；`drive/start/done/close --dry-run` 预演（门照跑、**零事件**）；`tests/test_dryrun.py` |
| FR-14/15/16/17 分发 | ✅ | 只推不跑；brief/collect；无 spawn；无常驻；`tests/test_e2e.py` |
| FR-18/19/20 门与证据 | ✅ | 验收门（含 `waiver` 显式豁免、`require_declarations` 审批声明）+ 证据自算 + 缺项契约；`tests/test_engine.py`、`test_gates.py`、`test_coverage.py` |
| FR-21/22/23 看板 | ✅ | 板定义随事件流持久化（`design.md` §6.1）；三视图同源；多树隔离；`awaits` 信号；`tests/test_views.py`、`test_multitree.py`、`test_boards_per_tree.py` |
| **FR-30 WIP 分发策略** | ✅ | 看板列 `wip` 在 `drive` 生效（列满则其余就绪者进 `deferred`）；`tests/test_board_policy.py::WipPolicyTest` |
| **FR-31 看板默认验收** | ✅ | 节点未自带 `acceptance` 时由该列派生（节点优先）；`done` 内同源；`tests/test_board_policy.py::AcceptancePolicyTest` |
| FR-32 动态看板 | ✅ | 板定义可逐树覆盖（`init --boards` / `set_boards`）；`tests/test_boards_per_tree.py` |
| **FR-33 Spec 一等公民（引擎级实体）** | ✅ | `specs` 与 `trees` **并列**的顶层实体：`spec.*` 事件闭集（init/goal/gate/link/unlink/deliver/undeliver/close/reopen）+ 投影 + 7 个 CLI 动词；`tests/test_spec_entity.py`（21 项：实体/重放/拒收/硬边界/CLI） |
| **FR-34 Spec 关联资产（非归属）** | ✅ | 资产层：`assets/registry.json` 的 `spec.required_roles`/`delivers` + `assets_scan.py delivery` 门；`fr_check` 机检 FR-34 |
| **FR-35 建树判据 / 跨树等待自检** | ✅ | 结构信号（非词面）：存在 `closed` 树 ⇒ `init` 提示 + 可照抄 `reopen`/`expand` 命令；`doctor` 报声明了 `awaits` 的树对（`awaits` = 承认依赖 ⇒ 本该是同树内 `requires`）；`tests/test_tree_cardinality.py`（6 项：提示/可照抄命令/词面仅附加/doctor advisory 不判红/不误报/干净 state） |
| **FR-36 节点可追溯（`why`）** | ✅ | `views.trace_node` 按原序收本节点的 `require/start/collect/rescope/acceptance/retry/skip/fail/done` + 树级背景（`tree.init/close`）；`why <tree> <node> [--json]` 纯读；无记录明说；`tests/test_why.py`（8 项：原序/只收本树本节点/六关键 kind 全收/空 summary 不崩/缺 payload 不崩/无记录明说/done 打勾/纯读不改事件流与投影） |
| FR-24/25 上下文与写面 | ✅ | `brief` 祖先链注入；`pack_waves` 冲突排队；`tests/test_core.py` |
| **FR-26 租约** | ✅ | 带 `expires_at`；`reap` 显式回收；`tests/test_lease.py`（**本轮补齐**） |
| FR-27 卡死 | ✅ | `is_stuck`；`tests/test_core.py` |
| FR-28/29 迁移 | ✅ | `import mgt`；`design.md` §10/§10.1；`tests/test_import_mgt.py` |
| NFR-1 零依赖 | ✅ | 仅标准库 |
| NFR-2 语言中立 | ✅ | 核心层 = `core`/`store`/`graph`/`ready`，零业务词；依赖单向；`tests/test_layering.py` 逆向自检（扫针脚 + 查 import） |
| NFR-3/4/5 文件式·可测·可重建 | ✅ | — |
| NFR-6 规模预算 | ✅ | `size_budget` 门；`tests/test_gates.py` |
| NFR-7 不静默降级 | ✅ | 坏行/写失败报错；`tests/test_store.py`、`test_cli.py` |

**新增门**：**`tree_coverage`**——每个叶子任务要「需求已登记」（`goal` 非空）且「有可跑判据」（`acceptance.cmd` 或 `waiver`），非根容器必须有子节点；与 `tree_closure` 在 `close` 串联。逃生口：树根 `context.coverage = "not_applicable: <理由>"`。

**未实现（❌）**：无 —— FR-33 已由本轮落地（`spec.*` 事件 + 顶层 `specs` 投影 + CLI）。

**未覆盖（◐）**：无 —— FR-1..34 与 NFR-1..7 全覆盖。
