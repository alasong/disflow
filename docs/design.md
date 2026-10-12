# disflow 设计 v0.1

> 状态：**设计（待评审）** · 2026-10-10
> 配套：`docs/requirements.md`（需求）· `docs/dev-board.md`（开发看板种子）
> 判据来源：本线关于「分发器 / 树 / 图 / 看板」的讨论 + fat3 `skills/mgt` 复盘 + `scripts/flowcore` 先例

---

## 0. 一句话 + 分层

**一棵树管上下文，一张图管调度与引用，一个只推不跑的分发器按看板视图派发短命 worker。**

```text
┌─────────────────────────────────────────────────────────────┐
│ 视图层   board(stage)   tree   graph        ← 同一投影的渲染  │
├─────────────────────────────────────────────────────────────┤
│ 决策层   ready(n)  →  wave  →  drive(只推不跑) → brief        │
├─────────────────────────────────────────────────────────────┤
│ 数据层   Node(树) + Edge(图) ;  单一状态机 ;  门/证据          │
├─────────────────────────────────────────────────────────────┤
│ 存储层   events.jsonl(唯一真相) → state.json(投影, 可重建)     │
├─────────────────────────────────────────────────────────────┤
│ 宿主层   人 / 父 agent / 编排器  → spawn worker(短命)          │
└─────────────────────────────────────────────────────────────┘
```

分层继承 `flowcore`：`store`(L0) · `fsm`(L1) · `gates`(L2) · `forms`(L2b)，disflow 只在其上补 **图层 + 视图层**。

---

## 1. 单一事实源（修 mgt 的 P1/P2）

```text
events.jsonl  ──project()──▶  state.json（原子替换，可从事件重建）
   唯一真相                        只读缓存 / 查询用
```

- **只有一个状态机**：节点 `status`（见 §2.1）。mgt 的 `global_status` **不再存在**——审批/验证是图里的**节点**，不是第二条生命周期。
- **只有一个 registry 语义**：产出物登记（`produces`/`consumes`）住**投影**里，不再单独一份文件。
- 写路径：任何写操作 = **先 append 事件 → 再重算投影**（沿用 kaa/flowcore 约定）。

命令：

```text
disflow rebuild [--check]                # 从事件重建；--check 比对一致性（无 tree 参数，全库）
```

---

## 2. 数据模型

### 2.1 节点 Node

```jsonc
{
  "id": "n7",
  "parent": "n2",                 // 树边：唯一父（root 为 null）
  "kind": "task",                 // container | task  （HTN 的 compound/primitive）
  "stage": "dev",                 // 看板列：req | design | dev | verify（`done` 是节点状态，不是列）
  "role": "dev",                  // 负责方：dev | qa | pm（与 stage 独立）
  "goal": "实现 parse_config",
  "context": {                    // 供后代继承（沿树向下）
    "background": "…",
    "constraints": ["…"],
    "references": ["docs/x.md"]
  },
  "entry": "implement",           // 开工模板（决定门与证据要求）
  "produces": ["parse_config"],   // 图边：产出物
  "consumes": ["config_schema"],  // 图边：消费物（须指向已登记产出）
  "touch_plan": ["src/parse.py"], // 写面声明（规模门 + 冲突检测）
  "points": 3,                  // 估点（人给；见 §2.4 —— 它是**声明**侧，velocity/burndown 是派生侧）
  "awaits": ["boardA"],         // 跨树完成信号（等待别的树 closed），非图边
  "acceptance": {                 // 门
    "cmd": "pytest tests/test_parse.py",
    "expect": "exit0",             // exit0 | contains:… | regex:…
    "require_declarations": ["approver"], // 可选：done 时必须给出的声明字段
    "waiver": null                 // 可选：非空则显式豁免（理由记入证据）
  },
  "status": "pending",            // 存储态：pending|running|done|failed|skipped
  "lease": null,                  // {holder, at, expires_at}
  "evidence": null,               // done 时引擎自填（验收/改动集/作用域/规模）
  "skip_reason": null,            // skip 时记下理由
  "reclaimed": null               // reap 回收过期租约时记下原因
}
```

**字段分两类（声明 vs 运行时）——在本轮审查前它们是混在一起的：**

| 类 | 字段 | 怎么来 |
| --- | --- | --- |
| **可声明** | `id` `kind` `stage` `role` `goal` `context` `entry` `requires` `relates` `produces` `consumes` `touch_plan` `awaits` `points` `acceptance` | 在 `expand` 的 children 里给，或用对应动词改 |
| **运行时/派生** | `parent` `status` `lease` `evidence` | 由事件流决定 |

`expand` 里声明运行时字段会 **报错**（不是忽略）：`parent` 由 expand 的父子关系决定；
`status`/`lease` 走 `start`/`fail`/`retry`/`skip`/`reap`；`evidence` 由 `done` 自算。

> 为何“报错”而不是“忽略”：忽略就是**声明被接受、然后静默丢弃**——同一个 bug 家族。
> `requires`/`relates`/`produces`/`consumes` 曾经就是这样被丢弃的（后果是**调度乱序**）。
> 现在由 `tests/test_declarations.py` 的**结构性守卫**钉住：
> `DECLARABLE_FIELDS` 必须与 `new_node` 真正接下的参数**逐个相等**——
> 多一个就报错，而不是等下次有人踩到才发现。

同理，`spec.EVENT_KINDS`（声明）与 `state._HANDLERS`（可派发）由同一测试钉住必须相等；
`apply_event` 的报错文本改用 `_HANDLERS`（**真正可派发**的集合），不再引用可漂移的声明。

**关键区分（修 mgt 的 P2/P3）：**

| 概念 | 住哪 | 谁写 |
| --- | --- | --- |
| 层级（part-of） | `parent`（树，唯一） | `expand` |
| 调度依赖（顺序） | `requires`（图，DAG） | `require` / `unrequire` |
| 软引用 | `relates`（图，任意） | `relate` |
| 供给-消费 | `produces`/`consumes`（图） | `produce`/`consume` |
| 跨树依赖 | `awaits`（**非图边**；信号 = 对方树 `closed`） | `expand` |
| 派生就绪 | `ready`（**不存储**，现算） | — |
| 审批/验证 | **节点**（kind=task, stage=verify/approve） | `expand` |

> `status` **没有** "ready" 值——就绪是派生量。这消灭了 mgt「本地 status × 全局 status」的双状态机。
> mgt 的 `contains` 依赖**删除**（= `parent`）；`informs` → `relates`；`blocks` → `requires`。

### 2.2 树 vs 图：为什么必须分开

| | 树（`parent`） | 图（`requires`/`relates`/…） |
| --- | --- | --- |
| 管什么 | **上下文继承** | **调度与引用** |
| 约束 | 单父 → 祖先链**唯一、有限、无环** | `requires` 必须 DAG；`relates` 任意 |
| 读法 | root→…→parent | 入边 / 邻接 |

- 上下文**只走树**：走图会成环、多路径、指数膨胀。
- 调度**必须进图**：跨子树依赖、横向产出物依赖，树表达不了。
- 跨**树**（多块板）依赖**不进图**：它降级为 `awaits` 显式完成信号（§6）。

### 2.3 节点分型（修「容器/任务」混用）

| kind | 是否执行 | done 判据 |
| --- | --- | --- |
| `container` | 否，只分解 | 全部子节点 done（**派生**） |
| `task` | 是 | 过验收门（§7） |

`ready()` 只对 `task` 返回真；容器不派发。

### 2.4 树级字段（`deadline`）与量化的两侧（`points`）

树字典（`state["trees"][name]`）在 `goal` / `root` / `nodes` / `assets` 之外，有一个**声明**字段：

| 字段 | 怎么来 | 谁消费 |
| --- | --- | --- |
| `deadline` | 事件 `tree.deadline`（`Engine.deadline(tree, value)`；非法日期**响亮拒收**、`None` 清空） | `metrics` 的理想燃尽线；`trees` 的剩余/逾期读数 |

> **它不是门**：逾期不拒任何动词。同类边界见 §3.1（`done` 不要求租约）——
> **只声明事实，不替人做决定**；没有受害者要求「逾期即失败」，就不加门。

**量化分两侧，这条分界是刻意的**：

| 侧 | 是什么 | 在哪 | 谁产生 |
| --- | --- | --- | --- |
| **声明** | `points`（估点） | 节点字段，`expand` 时可给 | **人给**——引擎不猜、不推导 |
| **派生** | `velocity` / `burndown` / `eta_days` | `metrics.py`（纯函数、**零时钟**） | **引擎算**——从事件流推，完工时刻取事件 `ts` |

理由：估点是**非确定性判断**（人/模型的猜测）。让它进事实源会把非确定性带进引擎；
但一旦人把它**声明**下来，它就是一条可审计的事实（「我估了 3 点」）。
所以：**估计是声明，指标是派生**——与 `touch_plan`（人声明）vs 改动集（引擎自算）同一个形状。

未估（`points is None`）的节点**不进任何分母**：把它当 0 会把「没估」读成「不用干」，
凭空抬高 velocity、压低 eta——那正是量化最容易骗自己的地方。故单列 `points_unestimated`。

---

## 3. 就绪判据 `ready(n)`（唯一函数）

```text
ready(n) := kind(n) == task
          ∧ status(n) == pending
          ∧ ∀ d ∈ requires(n): status(d) ∈ TERMINAL      # 图入边（done 或 skipped）
          ∧ ∀ t ∈ awaits(n): t ∈ closed                 # 跨树完成信号
          ∧ lease(n) == null                            # 未被告占用
          # lease 自带 expires_at；**过期不会自动放行**：
          # 靠显式 `disflow reap <tree>` 置回 pending（就绪判定不看时钟，保持纯函数）
          ∧ 无环（写入时已保证）

blocked_by(n) := [ d ∈ requires(n) : status(d) ∉ TERMINAL ]
                 ⊕ [ "@"+t : t ∈ awaits(n) ∧ t ∉ closed ]
```

> `TERMINAL = (done, skipped)`（`core.py`）。即：**一个前置被 `skip` 掉，不再阻塞后继**——
> 「这条前置被显式放弃了」，与「还没做」是两回事。此前文档写 `status(d) == done`，与实现不符，已按实现修正。

**唯一性**（FR-11）：分发器和 worker 调**同一份** `ready`；两面不分叉。

### 3.1 租约的语义边界（措辞曾被抳出歧义）

| 断言 | 是否为真 | 为何 |
| --- | --- | --- |
| `ready` 不看时钟 | ✅ | 保持纯函数；过期**不会自动放行**，靠显式 `reap` |
| 租约过期 ⇒ 不能 `done` | ❌ **不是** | 见下 |
| 租约过期 ⇒ `doctor` 报“故障” | ❌ **不是**（仅提醒） | 见下 |
| state dir 在 repo 内 ⇒ `worktree` 功能不可用 ⇒ `doctor` 报“故障” | ❌ **不是**（需人判断） | 见下 |

**租约防的是并发，不是“收工晚了”。** 两个 worker 同时干同一节点才是要防的事；
一个人干得慢不是。所以 **`done` 不要求 `running`、也不要求租约未过期**——
这一点以前只在实现里成立（属**意外行为**），现已由测试钉成有意契约
（`test_done_does_not_require_a_lease`）。

**`doctor` 对“running 且租约过期”只报「需人判断」**（不计入退出码，见 `cli.py` 的
`_DOCTOR_ADVISORY_FOOTNOTE`）。理由：`doctor` **无法**区分这两种完全不同的病因——

| 病因 | 正确做法 | 错误做法 |
| --- | --- | --- |
| worker 崩了（节点被遗弃） | `reap` 收回（退回 pending） | 一直等 |
| 人在正常干活（超过 `lease_ttl`） | 直接 `done`（**不要求租约**） | **`reap`——会把节点从人手里收回** |

既然分不出，就不能假装分得出：报告给的是**事实**（过期多久 + `holder`）+ **两种读法**
及其各自正确做法。旧措辞是「租约超时未回收（可 `disflow reap`）」——
把两种病因说成一种，并把一个**对其中一种是错的**修法当成唯一答案。

**`doctor` 对“state dir 落在 repo 内部”只报「需人判断」（不计入退出码）。** `worktree`
模块硬拒落在 repo 内的落点（在仓内会污染 `git status`，且一次 `git clean -xdf` 会把状态
目录连同全部 worktree 一起删掉），故该配置下「已交付的 `worktree` 功能不可用」。但仓内
state dir 是**出厂默认**（`vocab.py` 的 `state.dir = ".disflow"`），它只限制这一项**可选功能**
的可用性、其余功能正常——判成故障会因默认布局把所有人**假红**（假红比不报更坏）。所以
`doctor` **主动**算出这条（不等真跑 `worktree apply` 才炸），单独一条、单独一行报出：事实
（state dir 的真实路径 vs `repo`，**经 `realpath` 归一**——文本在仓外但符号链接指进仓内同样
算仓内）+ 修法（把 `[state].dir` 或 `DISFLOW_STATE_DIR` 配到仓外）。仓外配置**不报**。
判据与 `worktree._reject_if_inside` 同源（`cli.py` 的 `_state_dir_inside_repo`）：worktree 落点
`<state_dir>/worktrees` 派生自 state dir，故「state dir 在仓内」⇔「worktree 根在仓内」⇔
`worktree apply` 必被拒收。测试：`tests/test_doctor_worktree.py`（仓内/仓外两侧 + 符号链接两向）。

**波次 `wave`**：

```text
wave := greedy_pack({ n : ready(n) }, 冲突判据 = touch_plan 有交集)
```

同波内 `touch_plan` 不重叠；重叠者排到下一波（沿用 flowcore `drive` 的 `--touches` 提示）。

冲突判据**不止候选之间**：**正在运行**（非终态 + 租约在手）节点的 `touch_plan` 也算已占用。
与在跑者重叠的候选**不进**波次（`ready_wave` / `waves` 里都没有），而是进 `drive` 输出的
`deferred` 并**带原因**（点名在跑的谁 + 重叠哪些路径）——**绝不静默丢**。理由：宿主按 §5 的
循环走（⑥ 回到 ①）时，若这些候选照旧进就绪波次，宿主会派出一个正和在跑节点抢同一文件的
节点，而「冲突解错」正是**静默丢改动**的高发地。判据**不看时钟**（租约是否过期归 `reap`），
故 `drive` 仍满足「两次同输入同输出」。无节点被挡下时 `deferred` **不出现**。

**卡死检测**（FR-27）：

```text
stuck := pending() != ∅ ∧ ready() == ∅ ∧ running() == ∅
```

出现即报错（通常是环或全被阻塞）。

---

## 4. 上下文投影 `brief(n)`

```text
brief(n) := 树：祖先链 root→…→parent 各自的 context（浅合并，近祖先覆盖远祖先）
          ⊕ 图：每个 status==done 的 requires 前置节点的产出摘要（produces）
          ⊕ 图：consumes 指向的登记项要点
          ⊕ relates：只给引用句柄，**不递归展开**
```

- 前两段**沿树唯一、有限**；第三段只给指针，避免图膨胀。
- 每段受**规模预算**约束（NFR-6）：超预算即报错并要求拆节点，不静默截断。

命令：

```text
disflow brief <tree> <node>            # 输出该节点的完整 brief（worker 的输入）
disflow brief <tree> <node> --json
```

---

## 5. 分发器（只推不跑）

### 5.1 生命周期

```text
drive ──▶ ready_wave ──▶ brief ──▶ [宿主 spawn worker] ──▶ 执行
                                                          │
     done ◀── gate(验收) ◀── collect(校验结果) ◀──────────┘
      │
      └──▶ 事件入流 ──▶ 投影更新 ──▶ 下次 drive
```

### 5.2 命令

| 命令 | 作用 | 关键约束 |
| --- | --- | --- |
| `disflow drive <tree> [--json]` | 算就绪波次 + 每节点建议 | **只推不跑**；两次同输入同输出 |
| `--dry-run`（`start`/`done`/`close`/`drive`） | 预演：门照跑、`prepare` 照算，**不落任何事件** | 拒收行为与真跑一致（预演不消耗节点） |
| `disflow brief <tree> <node>` | 写全派发契约 | **一次写全**：必填声明 + `question_policy` + `result_contract`（FR-15） |
| `disflow host-next <tree> [--json]` | 一轮派活的**全部输入**：就绪波次 + 每个就绪节点的完整 brief + `start`/`collect`/`done` 命令 | **纯读**：不落事件、不改状态、不起常驻进程；无就绪时显式报 `complete`/`stuck` |
| `disflow collect <tree> <node> --result` | 校验结果契约后落盘 | **不写 done**；`status ∈ {ok, failed, blocked}` |
| `disflow done <tree> <node> --decl` | 跑门 → 通过/拒收 | 证据自算；拒收给缺项。**CLI 层**：无已落盘 collect ⇒ 拒收（exit 1），`--no-result "<理由>"` 显式破例且理由落进事件 |
| `disflow fail/retry/skip <tree> <node>` | 状态操作 | 经同一状态表 |
| `disflow reap <tree>` | 回收**过期**租约（running → pending） | 显式事件；不自动”看时钟“ |
| `disflow trees` | 列出所有树 + 是否 closed | 投影，不是第二存储 |
| `disflow close <tree>` | 终止树（跑 `tree_closure` + `tree_coverage` 门） | 供跨树 `awaits` 信号 |
| `disflow reopen <tree> [--reason S]` | 显式撤销 `closed`（落 `tree.reopen`，理由进 `reopen_log`） | 关错了 / 漏了活时的正路：不至于要么造新树、要么静默失效 |

**Spec（交付单元）—— 独立顶层实体（ADR 0006 / FR-33）**：

| 命令 | 作用 | 关键约束 |
| --- | --- | --- |
| `disflow specs` | 列出 Spec + 角色覆盖 | 投影，不是第二存储 |
| `disflow spec <spec> [--json]` | 看一个 Spec 详情（逐资产角色） | 缺角色是**报告**，不拦（与 `check` 同纪律） |
| `disflow spec-init <spec> --goal G` | 建 Spec（`spec.init`） | 重名拒收；配 `--dry-run` |
| `disflow spec-link <spec> <asset> <role>` | **关联**一个资产（多对多、带角色） | 角色是 `choices`（参数错 ⇒ 2）；非法角色不落事件 |
| `disflow spec-unlink <spec> <asset>` | 取消关联 | 未关联 ⇒ 拒收（exit 1） |
| `disflow spec-close <spec> [--dry-run]` | 关闭（`spec.close`） | 已关 ⇒ 拒收；配 `--dry-run` |
| `disflow spec-reopen <spec> --reason S` | 重开（`spec.reopen`，理由进 `reopen_log`） | 未关 ⇒ 拒收 |

**硬边界（ADR 0006，勿顺手改）**：`specs` 与 `trees` **并列**（顶层），Spec **不是**树的字段、
**不是**节点的字段；`Spec → 资产` 是**关联**（多对多）不是归属 —— **无** `asset.parent = spec`，
删树不连带删 Spec。Spec **不得成第二真相源**：它不定义资产刷新顺序（派生自 `premises` 拓扑序），
也不复制 node 的状态/就绪（归引擎）。

**web 入口的只读端点**（ADR 0011：薄壳只读 + 写面白名单经 `Engine`）：

| 端点 | 给什么 | 约束 |
| --- | --- | --- |
| `GET /api/trees` | 每棵树摘要（同 `Engine.status`） | 只读 |
| `GET /api/board?tree=T[&by=column&#124;board]` | 看板读数（与 `disflow board --json` 同源） | 只读；缺 `tree` ⇒ 400 |
| `GET /api/assets` | 资产索引（trust/premises/stale_basis） | 只读 |
| `GET /api/specs` | Spec 摘要 + 逐资产角色 + `missing_roles` | 只读；**无 Spec ⇒ 200 + 空表**（空是正常状态，不 404） |
| `GET /api/host-next?tree=T` | 一轮派活的全部输入 | 只读；缺 `tree` ⇒ 400 |
| `POST /api/write` | 写动作白名单（经 `Engine` 的锁与门） | 唯一写面 |

判据：`tests/test_web.py`（真 HTTP；含「**只读端点打一遭后 `events.jsonl`/`state.json` 逐字节不变**」）。

### 5.3 spawn 归宿主

引擎**不内置常驻进程**（FR-16）。宿主可以是：人、父 agent、编排器、`run` 命令。
`drive` 输出里直接给**可照抄的 brief 命令**，宿主拿到就能派。

### 5.4 触发（FR-17）

| 方式 | 适用 |
| --- | --- |
| 外部 cron | `drive` + 派发 |
| 事件驱动 | 上游 `done` 后触发 |
| 显式 | 人主动跑 |

**引擎不常驻**——这是对 mgt「每 org 一个 daemon」P4 的直接修正。

### 5.5 提问纪律：给 worker 一条报得出来的通道（FR-15）

`brief` 一次写全三样东西：**必填声明**、**提问纪律**（`question_policy`）、**结果契约**（`result_contract`）。

关键在于三者缺一不可，尤其是结果契约里的那个值：

```jsonc
"result_contract": {
  "required": ["status", "summary", "changed_files"],
  "status_in": ["ok", "failed", "blocked"],   // ← blocked 就是「我要问」的合法出口
  "question_channel": "有问题就把问题写进 summary 且 status 置为 'blocked'（然后停）"
}
```

**为什么必须同时加 `blocked`**：只加 `question_policy` 字段是**不可执行的装饰**——
它要求 worker 提问，而结果契约里没有这个状态，worker 报不出来，唯一的出路还是自己拍板。
**「要求别乱来」不如「给一条合法的报法」。**

`blocked` 只是一个**结果**，不是完成：`collect` 照旧**不写 `node-done`**（FR-15 原文）。
所以走这条路口并不会把门绕过去——宿主读到 `blocked` 后决定（`rescope` / `unrequire` / 改 goal），
再由 worker 继续。`status` 里没有「我猜了一下」这个选项。

`question_policy` 三层解析（与 `size_budget` 同一形状）：**节点 `context.question_policy`
> 项目 `disflow.json` > 内建默认**，且是**合并**不是整体替换——节点只声明 `how` 时
不把 `ask_host` 弄丢（丢掉 = 纪律静默变窄，而 brief 看上去还是「有纪律」）。

**为何 `done` 缺 collect 会被 CLI 拒收（而引擎不拒）。** collect 是 worker 上报 `blocked` 的**唯一**通道：它的 `--result` 是 JSON，宿主 collect 失败（argparse 报错）后若 `done` 照旧静默落盘，就等于让提问通道失效（FR-15 形同虚设，树上也永远看不到「谁干了什么」）。故**契约在 CLI 层强制**：无已落盘的 collect 结果 ⇒ `done` 响亮拒收（exit 1，报文给出那条 `collect` 命令）；确实没有 worker 结果时显式 `--no-result "<理由>"` 破例，理由落进证据。**引擎层不拒收**——无 collect 的 `done` 是引擎 API 的合法组合（直干型节点不该被逼伪造一份自报）；引擎只负责**记账**：每次 `node.done` 都记 `evidence.result_collected`（true/false），缺结果时 stdout 也警告一句。分层判据：引擎是机制，CLI 是宿主入口的纪律；「绝不静默」由事件记账保证。

**工作目录（worktree）：brief 的第四样。** `brief` 还告诉 worker **到哪干活**——新增
`worktree` 字段：该节点已 `disflow worktree apply` 时给
`{"path": <绝对路径>, "branch": "disflow/<tree>/<node>"}`，否则 `null`；并在
`context.worktree` 写明「你的工作目录是 `<path>`，不是仓根」。**为什么必须写全**：`apply`
把工作区建在 `<state_dir>/worktrees/<tree>/<node>`，而门（验收命令 / `git status` / 内容指纹）
与 worker 若一个在 worktree、一个在仓根，两者不相交 ⇒ 改动看不见 ⇒ **空洞通过**。
故 `start` 记下开工现场 `workspace = {"cwd", "head_sha", "worktree"}`，`done` 的**所有**门
统一跑在 `_gate_cwd` 上（有真 worktree 就是它，否则仓根）；两处目录不一致时 fail-closed
**响亮拒收**，不给「静默空洞通过」留路。改动集在 worktree 场景下取「脏文件 ∪ 相对
`head_sha` 的已提交改动」——提交型 worker 的活也看得见（`git status` 为空 ≠ 没干活）。

### 5.6 `host-next`：把宿主一轮的六步压成一次只读调用（FR-16 的派活面）

宿主每轮要跑六步：`drive → brief → start → 派活 → collect → done`。宿主实测的摩擦力在于：
它能一步就派一个 subagent，而 disflow 要六步——于是 disflow 被绕开（本会话实测：**3 小时
4 次派发、disflow 事件参与 0 次**，原因就是六步 vs 一步）。`host-next` 直接对抗这个摩擦力：

```text
disflow host-next <tree> [--json]
```

一次只读调用给出**本轮该派的全部**：

- **就绪波次**：`waves`（每波内写面 `touch_plan` 互不重叠；且**已扣除**与正在运行节点写面
  重叠的候选 —— 见 §3.1）+ `ready_wave`（首波）+ `blocked` + `stuck`；
- **被挡下的候选不消失**：与在跑节点写面重叠的就绪候选**不进** `plan`（宿主不会派它与在跑者抢
  同一文件），但**原样出现**在 `deferred`（与 `drive` **同源**：同一份条目、同一个 `why`，不在
  这里重算——两面各算一遍是漂移的温床）。`deferred` **仅非空时出现**。**人读态同样报、不重复**：
  全被挡那格由 `state_message` 说清；混合场景（有可派 `plan`）另打一行 deferred（节点 + 在跑者 +
  重叠路径）。见 §3.1 的「绝不静默丢」；
- **每个就绪节点的完整 brief**（`plan[].brief`）：与 `disflow brief` **同一份** `handoff_brief`
  （必填声明 + `question_policy` + `result_contract` + 工作目录），宿主可直接把它当 worker 的 task；
- **收尾要跑的完整命令行**：每个节点给 `start_cmd` / `collect_cmd` / `done_cmd`，照抄即可。

三条硬约束：

1. **纯读**：只读投影 + 只读的 worktree 对账，**不落任何事件、不改状态、不起常驻进程**
   （宿主只是拿它去派活）。
2. **无就绪节点时不静默**：`state` / `state_message` 显式报一种病因，**绝不**静默返回空、也
   **绝不**报 `ready` 配空 `plan`（自相矛盾，读者会以为没活干）。取值为
   `complete`（全部完成）/ `stuck`（卡住）/ `waiting`（就绪候选**全**被在跑节点的写面挡下，
   等它们收尾）/ `running`（无候选但有节点在跑）/ `failed`（有失败待 retry）。
   **`waiting` 与 `stuck` 是两种病因，不许合并**：`stuck` = 「没就绪、没在跑、但还有 pending」；
   `waiting` = 「就绪候选被**在跑**节点的写面挡下」——后者恰恰**是在跑**。`deferred`（与 `drive`
   同源）在 JSON 结构与**人读输出**两处可读，均点名「被谁占用 + 重叠哪些路径」——全被挡那格由
   `state_message` 承载，混合场景另打一行 deferred，**不重复打印**；被挡节点不得从输出里消失；
3. **提问纪律要写全**（`plan[].brief.question_protocol`）：worker 的提问天然走**宿主通道**
   （pi 里即 `contact_supervisor`），而该通道**不进 disflow 事件流**——追溯会缺一块。故约定：
   **① 走宿主通道提问；② 同时在 `collect --result` 里把 `status` 置 `blocked`**。两条都做，
   disflow 的事件流里才留得下这次提问（只做①树上查不到；只做②宿主收不到）。

---

## 6. 看板（一等实体；当前渲染 = 阶段列视图）

> ⚠️ **定位已升级**（[ADR 0005](decisions/0005-看板提升为一等实体.md)，2026-10-11）：看板是**一等实体**
> （固定列结构 + 挂在其上的分发器/验收器），树/图是板内维度。本节的**分列渲染**仍描述当前实现
> （看板 = 树的投影渲染）；**板策略**（逐列 WIP / 默认验收，FR-30/31）已实现，见 §6.2。

同一投影的三种渲染，**不各存一份**：

```text
disflow board <tree> [--stage S] [--all] [--all-columns] [--json]   # 看板：按 stage 分列 × 列内树层级 × 状态分组
disflow tree  <tree>                        # 树：按 parent 缩进
disflow graph <tree> [--dot]                # 图：requires/relates
```

**看板布局只有一处实现**（`views.board_layout`，2026-10-11 用户要求「按阶段列出来 + 按树结构的
类别分层」）：人读态（`board_lines`）与机器态（`board --json`，带 `parent`/`depth`）都是它的渲染。
宿主侧**运行视图**（`.agents/skills/disflow/lanes_watch.sh`，2026-10-11 改版）以 `state.json` 的
**全部节点**为骨架，**逐行复用看板的成品行**（`views.board_rows` = 唯一行序来源，按 `kind` 消费）（列 / 缩进 / 分组一个字符都不自己写），
再 join 宿主 pi 运行目录的实时事实（join 键：lane 名 = 节点 id），在**在跑**节点上就地显示
进展（`◈` 目标 / `⌘` 最后命令 / `↳` 自报进展）；「派发批次」降为**次级注解区**（排障用）。
**数据源分离、布局同源**：看板 = 纯 disflow 事实；运行视图 = disflow 事实 + pi 运行目录；
区分两个视图的依据是**数据源与刷新节拍**，不是布局——**任何一侧都不许再写一套分列/缩进**
（同一判据两份实现 ⇒ 两面分叉）。

> **看板 / 运行视图的历次重设计（v8 → v9 → v10）见 `docs/decisions/`**：
> [0001](decisions/0001-运行视图-v8-可用性四修.md) 追加式刷新 / 终态行不打 goal / 默认只列未完成 / 无在跑节点明说、
> [0002](decisions/0002-看板-v9-默认只列运行中加待完成.md) 默认只列运行中+待完成、
> [0003](decisions/0003-运行视图-v10-按板列不按树.md) 看板 = 阶段列（**当前形态**）。
> 本节只留**现状**；「为什么这么定」在 ADR，不在这里复述。

| 布局判据 | 行为 |
| --- | --- |
| **列** | 配置顺序（`boards.json` 的板序；缺失 = 内建 `spec.STAGES`）**逐列全出**，空列显式 `0/0`；非规范 stage 追加末尾并标注（读时容忍 ≠ 静默隐藏） |
| **列内层级** | 容器（`kind=container`）是**结构行**（`◦`），任务按 `parent` 缩进；深度按**列内**祖先链算——祖先不在本列时子节点照旧出现（不丢、不负缩进） |
| **`[req]` 列** | 显示**树自身的目标**（root 容器的 `goal`）；容器**不计入** `done/total`（与 `ready.progress`「容器不量化」同一口径） |
| **列内状态分组** | `▶ 运行中(holder)` / `· 可派` / `· 待前置(缺哪个 requires)` / `· 被在跑挡住(与谁、重叠路径)` / `✓ 完成` / `— 跳过` / `✗ 失败`——某组只要有一个节点就必须出现 |
| **可派性同源** | 直接消费 `dispatch.drive` 的读数（`waves` / `deferred` / `blocked`），视图层不重算写面冲突 |
| **纯读** | 跑 `board` 前后 `events.jsonl` 与 `state.json` **逐字节不变** |

**「多块板」= 多棵树**（FR-22）：每棵树作用域隔离，各有权威分发者（`drive`/`ready` 只看本树节点）。
跨树依赖**不进图**（FR-23）：降级为 **`awaits` 显式完成信号** —— 节点声明 `awaits: [<tree>]`，
就绪要求那些树已 `closed`（`tree.close` 经 `tree_closure` 门写入）。理由：把多棵树连成一张图会引入分布式共识。

```text
disflow trees                        # 列出所有树（● = 已 closed）
disflow close <tree>                 # 跑 tree_closure 门 → 标记该树 closed（跨树信号源）
```

> 当前实现：多棵树共享一个状态目录（`trees` 映射）。**物理隔离**可用 `DISFLOW_STATE_DIR` 给每棵树独立的事件流。

### 6.1 逐树看板：板定义进事件流（2026-10-11，D4 后半）

**板是策略，但「该树有哪些板」是事实**。`boards.json` 是**进程级**的（全项目一份，同一棵树在
不同机器上可能读到不同值）。若列由配置文件决定，重放旧事件流会渲染出不同的列 ⇒ **事件流
不可重放**。故：板定义随 `tree.init` / `tree.boards` 写进事件，投影携带（`tree["boards"]`）
——它成为该树的**事实**，与文件解耦。

| 环节 | 行为 |
| --- | --- |
| **播种** | `Engine.init_tree` 从 `cfg.boards` 写 `tree.init` 的 `boards`（**建树时**的快照）；CLI `init --boards '<ids>'` / `init --boards <file>` 可给该树**单独**的板集（逐树覆盖） |
| **读取** | `views`/`cli`/`engine` 一律读**该树**的板集（`Engine._tree_boards`）；缺失（旧事件流）回落 `cfg.boards` |
| **校验** | `stage` 写时闭集 = **该树**的板 id（不是进程级 `cfg.stages`）⇒ 甲树的合法列不会被乙树的字典拒掉 |
| **默认板** | 任务没声明 `stage` ⇒ 该树里 `dev` 在则用它，否则**入口板**（板序第一块）；补默认发生在 `prepare`（能拿到该树）而非调用前 |
| **改板** | `Engine.set_boards`（`tree.boards` 事件 + 理由；**暂无 CLI 动词**）——**不**追溯改写已有节点的 `stage`（节点的列是它自己的事实；旧列按「读时容忍」作非规范列显示） |
| **重放** | 换 `boards.json` 后，旧树读数**逐字节不变**；新树才拿新配置（`tests/test_boards_per_tree.py` 钉住） |

**为何不做「按树放一个 `boards.json` 文件」**：那还是**进程级**读取——同一棵树在不同机器/
时刻读到不同文件，重放照样不可复现。判据是**可重放**，不是「文件放哪」。

### 6.2 看板策略：WIP 与默认验收挂在板上（FR-30/31，2026-10-11）

**看板是一等实体**（ADR 0005），分发器/验收器是它的**附属**——策略挂在板/列上，不散在节点上。
板条目因此从 `{id,label}` 扩成可带策略（随 `tree.init`/`tree.boards` 进事件流，可重放）：

| 策略 | 字段 | 谁消费 | 语义 |
| --- | --- | --- | --- |
| 分发（FR-30） | 列 `wip`（≥1 整数，缺省=不限） | `dispatch.drive` | 列内**并发在跑**数达上限 ⇒ 该列其余就绪者进 `deferred`（点名原因），`waves` 不再含它；WIP 是**逐列**的，不挡别的列 |
| 验收（FR-31） | 列 `acceptance`（形状=节点 acceptance） | `Engine._effective_acceptance` → `done` | 节点**自带优先**，否则由该列默认验收派生；一次 `done` 里门 / 空洞判据 / 契约 / 必填声明**同源** |

**向后兼容**：无策略的板序列化仍是 `{id,label}`（`dump` 只在非空时写出 `wip`/`acceptance`）⇒
旧事件流 / 零配置读数**逐字节不变**。**校验**：`wip` 非正整数、`acceptance` 非对象在**写时**
响亮拒收；`from_projection` 对非法策略**读时容忍**（忽略该字段，不崩）——与 `stage` 的
「写时闭集 / 读时容忍」同一条纪律。判据：`tests/test_board_policy.py`（schema / WIP / 验收三组）。

### 6.3 树的数量：建树提示与跨树等待自检（2026-10-12）

**问题**：看板 : 树 = 1:1（FR-22）、树 = 任务依赖视图都是**机制上成立**的，但**建树无门槛**
—— 拆成几棵树全凭宿主裁量。实测反例：`mldev`（产出首个作品）与 `mlxfix`（把作品做进上游）
本是同一树内的 `repro → fix`，却被建成了两棵并列的树。

**判据不是「goal 有共同内容词」**（初稿这么写，**已改正**）：拿 `gates` 的切词去比
`mldev` / `mlxfix` 两句 goal，**只共享 1 个通用动词「产出」** —— 词面判据在它为之而写的
那个 bug 上必然失效（且 `mlxfix` 当时**已声明** `awaits: ['mldev']`，不是「漏声明」）。
改用两个**结构信号**：

| 落点 | 判据 | 动作 |
| --- | --- | --- |
| `disflow init` | 存在**已 `closed`** 的树 | 提醒「这可能是它的下一阶段」，并给出**可照抄**的 `reopen` + `expand` 命令 |
| `disflow doctor` | 某树声明了 `awaits` 指向另一棵树 | 报为「需人判断」：`awaits` = 承认依赖 ⇒ 本该是同树内的 `requires` |

两条都**只提示不拒收**（exit 0）：目标是否独立是**语义**问题，词面/结构信号都有假阳性；
拒收会挡住合法建树。已关闭的树尤其要紧 —— 想接着干只能 `reopen` 后 `expand`，而「另建一棵」
会把同一目标的两个阶段拆成两棵树。

共同内容词仍会打印，但**只作附加信息、明确标注「非判据」**。判据：`tests/test_tree_cardinality.py`
（6 项：提示/命令/不误报；doctor 的 awaits 对/不误报/悬空不报）。

---

## 7. 门与证据契约（FR-18/19/20）

- 每个 `entry`（开工模板）声明：**产出**、**证明**、**反模式**、**验收命令**。
- `done` 时引擎自算证据元数据：`git` 改动集、顶层符号、`touch_plan` 覆盖检查。
- 拒收输出：**缺项清单 + 契约片段**。

门的类别（对齐 mgt `G2 Gate` 与 kaa 的教训）：

| 门 | 判什么 | 挂在 |
| --- | --- | --- |
| `acceptance` | 验收命令跑过 + 期望匹配（或 `acceptance.waiver` 显式豁免，记入证据） | 所有 task |
| `design_first` | 改动集落在 `touch_plan` 内（`scope_waiver` 破例） | implement/configure |
| `size_budget` | 改动不超预算（`size_waiver` 破例） | implement/configure/merge |
| `tree_closure` | 树根有 done 的 evaluate（收益验证）与 reflect/standardize（沉淀） | 树根 / `close` |
| `tree_coverage` | 每个叶子任务有 `goal` 与可跑判据；非根容器有子节点 | 树根 / `close` |

后两个门在 `close` 里**串联**，任一不过即拒收。两者的逃生口均为树根 `context.<门名> = "not_applicable: <具体理由>"`（理由不能为空）。

审批完整性：`acceptance.require_declarations` 列出的字段（如 `["approver"]`）必须在 `done --decl` 里给出，否则拒收——人工审批门（如 PM1）靠它留下审批人。

### 7.1 证据归因：`baseline` → `introduced`（如何判断「是本节点改的」）

“改动集落在 `touch_plan` 内”成立的前提是：**先把不属于本节点的改动剔掉**。做法：

1. `start` 时对**当时所有脏文件**取**内容指纹**（`sha256`），存进 `node.baseline`：`{路径: 指纹}`。
2. `done` 时把「当前脏文件 ∪ 基线键」逐个重算指纹，**指纹与基线不同**的才是 `introduced`
   （新出现 / 内容变 / 被删 / 读不到）。
3. `introduced` 交给 `design_first`（作用域）与 `size_budget`（规模）判定。

**为何用指纹而不是「按路径做差集」**（曾是真缺口）：同一个文件被前一节点弄脏后，
本节点又改它时，两边的路径集合**都有这个文件**，差集为空 ⇒ `introduced=[]`
⇒ 作用域门**静默失效**。指纹能看见内容确实变了。

| 情形 | 差集（旧） | 指纹（今） |
| --- | --- | --- |
| 前节点弄脏 `x`，本节点没碰 | 不报（对） | 不报（对） |
| 前节点弄脏 `x`，本节点**又改** `x` | **不报（错）** | 报（对） |
| 本节点新建 `y` | 报 | 报 |
| 同长度替换（大小看不出） | 不报 | 报 |

**局限（诚实标注）**：指纹只比内容，**不知道是谁改的**。两个节点真同时改同一文件时，
两者都会被归因——本机制消除的是「**静默**漏判」，不是「并发写同一文件的冲突检测」
（后者靠写面打包 —— 含把**正在运行**节点的写面算作已占用，见 §3.1 的 `deferred` —— 与单写者
收口）。旧事件（基线是路径列表）归一为未知指纹，一律判为已改（保守）。

### 7.2 写面声明的**当口改动**：`rescope` 与 `scope_waiver`

`touch_plan` 在 `expand` 时就声明。但「干活时才发现要多碰一个文件」是**常态**，
不是例外——没有合法路径时，`design_first` 会把人逼到「还原重做」或「废掉节点」。

但也不能就开一个「随便改计划」的口子：`design_first` 的全部力量来自
**计划先于改动**——若能把计划改成“我刚改过的东西”，门等于不存在。所以拆成两条路：

| 路径 | 何时用 | 判据 | 是否留违规记录 |
| --- | --- | --- | --- |
| `rescope <tree> <node> --touch <json> --reason <why>` | 发现要碰 X，**还没碰** X | 只允许纳入**不在本节点 `introduced` 里**的路径（与 §7.1 同一原语） | 否（计划仍先于改动） |
| `done --decl '{"scope_waiver": "<理由>"}'` | **已经**碰了 X，且有理 | 无技术判据——就是显式破例 | **是**：`violations` 照记，`scope_waiver` 原文进 `evidence` |

- `rescope` 只对 `running` 节点开放（没开工就没有“发现”可言），`--reason` **必填**，
  且每次变更（`from` / `to` / `reason`）累进 `node.rescope_log`——否则“计划先于改动”
  这个前提交代不清，事后无法审计。
- 已碰过的路径被拒时，报错会**点名替代路径**（走 `scope_waiver`）——拒收必须给出路。
- **守卫与 `done` 的门同一目录源**：判据取 `_gate_cwd(tree, node)`（该节点有真 worktree
  就是 worktree）与 `start` 记的 `head_sha`，**不是** `self.cwd`（进程 cwd = 仓根）。**已修缺陷**：
  守卫曾吃 `self.cwd`，worktree 派发时改动落在 `<state>/worktrees/<tree>/<node>`，两者不相交
  ⇒ 守卫在主工作区上看到干净 ⇒ `rescope` 放行「先改后补计划」，`scope` 门变成装饰（改完 →
  rescope → done 无违规，合法出口 `scope_waiver` 被绕过而不留痕）。原始读数
  （`python3 /tmp/disflow_rescope_guard_probe.py`，修前）：越界改 `B.txt` 并提交后
  `rescope exit=0`、报文 `✓ a 写面声明已更新：['A.txt', 'B.txt']`；修后同一序列
  `rescope exit=1`、报文点名 `已经改过`。已固化为 `scripts/disflow_selfgate_probe.py` 情形 5
  + `tests/test_rescope.py` / `tests/test_worktree_gate.py` 的对应测试。
- 两个破例口（`scope_waiver` / `size_waiver`）都**记入证据**：破例可以，装没发生不行。

对应对称：`rescope` ≈ `node.context.size_budget` 覆盖（改声明），
`scope_waiver` / `size_waiver` ≈ 同一个破例形状（不改声明，留痕放行）。

### 7.3 边的增与删（`require`/`unrequire`、`relate`/`unrelate`）：故意不对称

`requires` 是唯一的调度边，建错方向很常见（真跑循环里 `C7` 就被废掉了）。
但**删边不能把加边的校验搞松**，所以两者故意不对称：

| | 加边（`require` / `relate`） | 删边（`unrequire` / `unrelate`） |
| --- | --- | --- |
| 两端节点存在 | 必须 | 只要求 `node` 存在——**不要求对端存在** |
| 自环 / 成环 | `require` 拒（写时拒环）；`relate` 不适用 | 不适用 |
| 边已存在 / 不存在 | 幂等（`_append_unique`） | 不存在则拒（报出当前出边） |

**为何删边不要求对端存在**：否则**悬空边反而清不掉**——`doctor` 报得出、却没有动词修得了。
悬空边不可能由 `require` 造成（它要求两端存在），只能由导入 / 手工修复路径写进来；
所以“能清”是必需的，即使它罕见。

**`unrelate` 为何存在（曾经缺了）**：`relate` 能建不能删，与 `unrequire` 修之前是
**同一个缺口家族**——当时建错依赖只能 `skip` 整个节点。信息边不参与就绪判定，
所以优先级低；但“建错了改不了”仍然只能靠 `skip`，那不是个可接受的答案。

**不带 `--dry-run`**：这些动词只做**存在性/形状**校验、不跑门。厂内规则：
只有跑门的写动词（`start` / `done` / `close` / `drive`）才配 `--dry-run`，
其余（`require`/`unrequire`/`relate`/`unrelate`/`produce`/`consume`）不配。

> 仍开的边侧不对称：资产产出物可增不可撤（`produce` 无 `unproduce`）。
> 理由：`tree.assets[name] → node` 是**唯一归属**，撤掉后已 `consumes` 它的节点会落下悬空引用，
> 得先定“撤了之后那些 consumes 怎么办”才谈得上做——已登记在 `docs/v0.2-backlog.md` §C。

### 7.4 验收判据与规模读数

**`acceptance.expect` 三分支**（缺省 `exit0`）。两条容易写错、后果很重：

| `expect` | 通过条件 | 易错点 |
| --- | --- | --- |
| `exit0` | `exit == 0` | — |
| `contains:<子串>` | **`exit == 0` 且** 输出包含子串 | 只查子串 ⇒ 命令“打印 ok 然后失败”也能过门 |
| `regex:<模式>` | **`exit == 0` 且** `re.search(pattern, 输出)` 命中 | 忘了退出码；或误以为是全匹配（实际是 **search**） |

- 输出 = **stdout + stderr 合并**（否则测试框架写 stderr 的断言永远看不到）。
- 非法 `expect` → **抛 `ValueError`**，不静默放行（静默放行比报错危险得多）。
- `acceptance.waiver` 非空 ⇒ 不跑命令直接放行，理由进证据（可审计破例）。
- 证据里 `output_tail` 封顶 2000 字符（否则单条证据能把投影撞爆）。

**`diff_size` 读数**（`size_budget` 的分母）：

0. `start` 记了 `head_sha` 时，以 `git diff --numstat <head_sha>`（**已提交 + 工作区**）
   ∪ 工作区 numstat 为基准，逐文件取 **max**（不相加：`git diff <sha>` 已含工作区改动）。
1. 已跟踪文件 → 优先 `git diff HEAD --numstat`（**工作区 + 暂存区** vs 最后一次提交）。
2. 仓里还没提交 ⇒ 合并「未暂存（`git diff`）+ 已暂存（`git diff --cached`）」。
3. 不在 numstat 里的路径：**已跟踪 ⇒ 0**（git 已经告诉了我们“净改动为 0”）；
   **未跟踪 ⇒ 数它的全部行数**（git 算不出，按整文件计）。

> 为何要第 0 条（复用 `head_sha`，与 `git_changed_files` 同一做法）：worker 提交后
> `HEAD` 已含改动，只看 `git diff HEAD` 它就是 **0** ⇒ `size.total=0`、`vacuous=[size_budget]`
> ——已提交改动恒被当作「没改动」（宿主实测 4/4 节点如此）。
>
> 为何强调第 1/3 条：第一版用裸 `git diff`，已跟踪文件的**暂存改动对它不可见**，
> 于是落到“数整文件”的兜底——200 行文件改 1 行、`git add` 后被判 200 行（真缺陷，B3 补测时查出）。
> 同理，把“已跟踪但净改动为 0”误归入兜底也会读成整文件行数。

**`expand` 的边字段**：`requires` / `relates` / `produces` / `consumes` 可以在 `expand` 的
children 声明里直接给，与 `require`/`relate`/`produce`/`consume` 动词等价且同一套校验。
接线在**建齐本批全部节点之后**按「`relates` → `produces` → `consumes` → `requires`」分相进行：
分相让同批兄弟的**声明顺序无关**（B 声明 `produces X`、A 声明 `consumes X` 两种顺序都成立）。

> 这一条曾是**真缺陷**：`validate_node_spec` 接受这四个字段、`new_node` 却没有对应参数 ⇒
> 声明通过校验后被**静默丢弃**。后果不是“少个字段”而是**调度乱序**——`requires` 丢了，
> 评估节点在它要评估的工作未完工时就被推进首波（第二次真跑循环实测）。

### 7.5 空洞通过：门“全过” ≠ 门“都验了”

`done` 的证据里多一个字段 `vacuous`：**哪些门没有东西可验**（附理由）。

| 门 | 何时算空洞 |
| --- | --- |
| `acceptance` | 用了 `acceptance.waiver`（命令未执行） |
| `design_first` | `touch_plan` 为空，或本节点 `introduced` 为空 |
| `size_budget` | 无规模读数，或**真** 0 行（已提交改动经 `head_sha` 已计入，不再把「提交后看不见」误报成 0） |

**边界**：空洞**不是失败**，本机制**不改变任何门的通过/拒收结果**，只记录 + 报出。
`done` 与 `close` 各报一行（`close` 还会跨节点汇总，因为那是新读者看到的第一屏）。

为何需要它（不是洁癖）：`disflow-v0.1` 看板被呈现为“v0.1 完成 17/17、五道门”，
而实测其 **16/16 个 done 节点的 `introduced == []`**——作用域与规模两道门其实
**什么都没验**。当时这件事没写在任何地方；而“门全过”会被读成“门都验了”。
真跑循环里也发生过一次（`C6` 的作用域门空转，无人告知）。

> 对作者来说，“作用域门没验到东西”通常意味着**本节点没产生可检测的改动**——
> 这本身就是个值得看的信号。

### 7.6 看板列的**当口改动**：`stage`

`stage`（看板列）原本只在 `expand` 建节点与 mgt 导入时写一次——建树之后**改不了**。
这与 §7.2 的 `touch_plan` 是**同一个缺口形状**（建树后不可改，后由 `rescope` 补上）。

**为什么这是真问题**（不只是“不方便”）：

- §4 要求「看板 = 按 stage 分组/过滤**同一棵树**的投影」「三种视图，一份事实源」。
  当节点的事实列与看板列不符而**没有合法路径**改，人就只能去改 `state.json`——
  而它是**投影**、会被 `rebuild` 覆盖 ⇒ **违反「事件才是真相」**。
- 本仓自己的先例把 `E1 收益验证` / `S1 经验沉淀` 放在 **[verify] 列**；树里却只能留 `dev`，
  于是看板列与事实不符，且无从修正。

`disflow stage <tree> <node> <stage> --reason <why> [--holder H]`：

| 裁剪 | 判据 | 为何 |
| --- | --- | --- |
| **写时闭集校验** | 取值必须在配置的 `stages` 内（与 `expand` 的 `stage`/`role` 同一套 `_validate_stage`） | 非法值**响亮拒收**，绝不静默忽略成默认列（静默忽略正是本动词要消灭的“列与事实不符”） |
| **落事件 + 记理由 + 记谁改的** | `node.stage` 事件；`stage_log` 累进 `{from,to,reason,holder}` | 改列必须可审计、可重放——与 `rescope_log`/`reopen_log` 同形 |
| **收尾态拒收** | `done`/`skipped`/`failed`（`_STAGE_FROZEN`）不可改 | 与「终态不可重开」一致：收尾记录是历史，改列=改写历史。要改先把节点放回活态（failed 走 `retry`） |
| **不碰调度** | `ready`/`drive`/波次/`is_stuck` 改前改后**逐字节一致** | `stage` 是**视图列**，不是调度边；否则改列会变成隐形的调度阀门（`tests/test_stage_change.py` 钉住） |

投影只赋值、不校验取值（校验在写时）——与 `tree.deadline` 同一分界：reducer 一依赖配置，
事件流就不可重放（§9.2 边界二）。不带 `--dry-run`：守卫只做闭集/状态校验，不跑门（§7.3）。

### 7.7 验收判据的**当口补挂**：`acceptance`

`acceptance`（节点的验收判据）原本只在 `expand` 建节点与 mgt 导入时写一次——
建树后**改不了**。这与 §7.2 的 `touch_plan`、§7.6 的 `stage` 是**同一个缺口形状**
（建树后不可改，前两者分别由 `rescope` / `stage` 补上，唯独判据一直没有补口）。

**为什么这是真问题**（实脉踩到，不是“不方便”）：本仓 `board-web` 树是**动手前拍的粗拆**，
整个交付（23 文件一批提交）完成后才发现节点没配 `acceptance` ⇒ `done` 被门拦，
而 34 个动词里**没有合法路径**补。后果与当年 `touch_plan` 一样：人只能去改 `state.json`，
而它是**投影**、会被 `rebuild` 覆盖 ⇒ **违反「事件才是真相」**。

`disflow acceptance <tree> <node> --set '<json>' --reason <why> [--holder H]`：

| 裁剪 | 判据 | 为何 |
| --- | --- | --- |
| **形状校验** | 必须是对象，且**至少含 `cmd` 或 `waiver` 之一** | 两者皆无 = 挂了等于没挂（静默无效比报错贵） |
| **落事件 + 记理由 + 记谁改的** | `node.acceptance` 事件；`acceptance_log` 累进 `{from,to,reason,holder}` | 判据是门的输入，必须可审计、可重放——与 `stage_log` 同形 |
| **收尾态拒收** | `done`/`skipped`/`failed`（`_STAGE_FROZEN`）不可改 | 终态的证据已按**旧判据**采集过，改判据 = 改写历史（failed 可 `retry`） |
| **不碰调度** | `ready`/`drive`/波次/`is_stuck` 改前改后逐字节一致 | 判据只影响 `done` 时跑什么门，不是调度边 |

**为何不破坏门的效力**（对抗性检验）：事后补判据**不让门失效** —— 验收命令仍在 `done` 时
**真跑**；本动词改变的是「判据什么时候写」，不是「跑不跑」。这与 `rescope` 的先例同款
（计划可补、但必须留痕），差别只在 `rescope` 要拦「已碰过的路径」（因为 `design_first`
的力量来自「计划先于改动」），而 `acceptance` 没有对象可比 —— 判据是**未来要跑的**，
不是**过去已做过的**。

实测佐证（本仓）：`api` 节点补上 `{"cmd": "python3 -m unittest tests.test_web -q"}` 后，
`done` **立刻拒收 exit=1** —— 因为该命令缺 `PYTHONPATH` 跑不起来。门没有因为“判据是事后补的”
而放水：它真跑了、真拒了，错误当场暴露（换正确的 discover 形式后才过）。

---

## 8. 角色与阶段（修 mgt 的 P7）

- **stage** = 看板列（流程位置）：req → design → dev → verify（四个阶段，与设计原文的阶段图同形）。
  `done` **不是列**：它是节点**状态**（`core.TERMINAL`，另一个轴），写进 `stage` 会被**响亮拒收**。
- **role** = 负责方（dev/qa/pm）：**与 stage 独立**。
- **审批/验证是节点**：例如 `stage=verify, role=qa` 的节点消费 `dev` 节点的产出。
- **改列**：`disflow stage <tree> <node> <stage> --reason <why>`（建树后能改列；写时闭集校验 + 留痕，见 §7.6）。

这样：

- 消灭了 mgt 的 `global_status` 第二状态机（P2）。
- 消灭了 mgt「org 既当列又当负责人」的混同（P7）。
- 跨角色协作 = 图上的 `produces→consumes` + `requires`，不需要六种目录协议（P6）。

---

## 9. 目录 / 触发 / 分层

```text
<project>/.disflow/
  events.jsonl            # 唯一真相（append-only）
  state.json              # 投影（可从事件重建）
  trees/<tree>/           # 多棵树（多块板）各自隔离
  designs/<node>.md       # 设计文档（可选）
```

命令分层：

```text
disflow init <tree> --goal <g>
disflow goal <tree> <g>                            # 改目标
disflow expand <tree> <node> --children '<json>'   # 建树
disflow require <tree> <node> <dep>                # 建图（写时拒环）
disflow unrequire <tree> <node> <dep>              # 删边（增/删**故意不对称**，§7.3）
disflow relate <tree> <node> <node>                  # 建关系（参考，不影响就绪）
disflow unrelate <tree> <node> <node>                # 删关系（与 relate 故意不对称，§7.3）
disflow produce/consume <tree> <node> <name>
disflow context <tree> <node> --set '<json>'       # 合并 context（根用声明逃生口）
disflow stage <tree> <node> <stage> --reason <why> [--holder H]  # 改看板列（写时闭集校验，§7.6）
disflow rescope <tree> <node> --touch '<json>' --reason <why> [--dry-run]  # 改写面声明（§7.2）
disflow start <tree> <node> [--holder H] [--ttl N] [--dry-run]
disflow collect <tree> <node> --result '<json>'    # 校验结果契约，不写 done
disflow done <tree> <node> [--decl '<json>'] [--dry-run]
disflow fail/retry/skip <tree> <node>
disflow reap <tree>                                # 回收过期租约（running → pending）
disflow close <tree> [--dry-run]                   # 树级门
disflow reopen <tree> [--reason S]                 # 显式撤销 closed（关树后想接着干的正路）
disflow drive <tree> [--json] [--dry-run]          # 只推不跑
disflow brief <tree> <node>                        # 写全派发契约
disflow plan <tree>
disflow board <tree> [--stage S] [--json]         # 看板：按 stage 分列
disflow status [<tree>] [--json]
disflow metrics <tree> [--json]                    # 量化：点数/速度/燃尽/截止余量（只读，§2.4）
disflow deadline <tree> <YYYY-MM-DD|->             # 迭代边界（声明；逾期不拒任何动词）
disflow worktree plan|list|apply|reap <tree> [--json]
                                                   # 每节点物理隔离；只有 apply 真跑 git，reap 是纯计划
disflow worktree reap <tree> [--force] [--json]     # reap 默认安全删；--force 显式强删并记账 discarded
disflow worktree apply <tree> [--nodes a,b] [--json]  # 波次：只建点名节点（逗号分隔，可重复）；省略=全量
disflow trees                                      # 列出所有树（● = 已 closed）
disflow rebuild [--check]
disflow import mgt <path> --tree <T>               # 迁移（§10）
disflow doctor                                     # 自检
```

> 共 36 个动词，与 `--help` 一致；新增动词时同步本表。架构图见 `docs/architecture.md` §7。

### 9.1 边界：`apply` 执行、`reap` 只计划

`worktree` 是**派生资源**，不是状态：路径是 `(state_dir, tree, node)` 的纯函数，建删**不产生事件**、不新增存储字段。

| 动作 | 是否执行 git | 为什么 |
| --- | --- | --- |
| `plan` / `list` | 否 | 纯读面 |
| `apply` | **是**（创建 / re-attach） | **可逆且幂等**；建错删掉重来即可 |
| `reap` | **否**（纯计划） | **删除是唯一不可逆动作** |

`reap` 只给三样东西：**判据**（`reasons`，与守卫同一套词表）、**宿主该跑的命令**（`remove_cmd` / `branch_cmd`，分支一律 `-d` 安全删）、**跑了会丢什么**（`discard_warnings`，指名到提交 sha / ignored 路径）。
删除交给宿主跑它打印出来的命令——与 `drive` / `brief` 同形（引擎算，宿主执行）。

**节点子集只对 `apply` 开放**（`--nodes`）：`apply` 一律从 `HEAD` 分叉（`commands(..., base="HEAD")`，§9.1 模块 `commands`），故**波 2 建工作区必须在波 1 合并进 `HEAD` 之后**；没有 `--nodes` 会一次给全部节点建，波 2 的节点就从「还没有波 1 的 `HEAD`」分叉。因此 `--nodes a,b` 让调用方按波次逐个 `apply`。省略 `--nodes` = 全量，行为与不加该参数逐字一致；`plan`/`list`/`reap` 传 `--nodes` 一律响亮拒收（不静默忽略）。

**强删是显式破例（`worktree reap --force`）**：默认计划只给**安全命令**（`remove` 不带 `--force`、分支 `-d`）。确要连脏现场一起丢弃时，显式传 `--force`：`remove_cmd` 才加上 `--force`，且计划额外给出 `discarded`（逐条记下将丢弃什么，CLI 打印为「记账」块）——破例可以，但必须**显式、留痕**，绝不静默通过。`--force` 只对 `reap` 有意义，其余动作传它一律响亮拒收；**不传时计划与命令逐字节不变**。即便传了 `--force`，引擎仍**不执行**删除（`remove_cmd` 仍交给宿主跑）。

判决依据（不是审美）：本模块历史上 **9 条阻断级缺陷里 8 条落在「删」这条线上**（强删丢未提交工作 / `-D` 让提交不可达 / 不查返回码 / ignored 文件被静默销毁 / detached 提交被静默销毁 / 全局 `prune` 毁掉别人的登记 / 守卫短名歧义 fail-open / 残留分支路径绕过守卫），而纯读面连续多轮零阻断；且引擎替人删数据违反 disflow 自身的「只推不跑」契约。

守卫极性是 **fail-closed by construction**：`safe_to_drop` 仅当 `reasons` 为空才为真；任何探针不可判定一律落到拒绝侧；leaks 与残留分支**共用同一个漏斗**（`_plan_entry`），不存在第二条绕过判据的路径。

### 9.1 模块分层与 NFR-2（核心语言中立）

| 层 | 文件 | 允许知道什么 |
| --- | --- | --- |
| **核心（机制）** | `core.py` `store.py` `graph.py` `ready.py` | 状态/分型/边/租约/事件 IO —— **零业务词** |
| 业务（本实例） | `vocab.py` `model.py` `state.py` `gates.py` `views.py` `context.py` `dispatch.py` `engine.py` `cli.py` `import_mgt.py` | 视图列/负责方/门名/模板名/写面/验收 |

**依赖方向单向**：业务层 → 核心层；核心层**不得** `import` 业务层。
机制词汇（`KINDS`/`STORED_STATUS`/`TERMINAL`）住在 `core.py`，`vocab.py` 只做再导出。

**判据可执行**（沿用 flowcore「lib 零业务判据名」）：`tests/test_layering.py` 扫描四个核心文件，
命中业务词针脚即失败，并断言核心未 import 业务模块。写面打包（依赖业务字段）因此从
`ready.py` 移到 `dispatch.pack_waves`。

**允许留在核心的机制词**：`requires`/`relates`（边种）、`container`/`task`（分型）、
`pending`/`running`/`done`/`failed`/`skipped`（状态）、`lease`、`awaits`（跨树完成信号 = 机制）。

**自检的已知边界（诚实标注）**：

1. 针脚表 + 单词边界，**不是业务词闭集推导**——未登记的业务词会漏判。故 `MatcherSelfTest`
   用合成样本证明尺子本身有效（而非靠「仓库恰好干净」）。
2. **`done` 不可判定**：它是**机制终态**（`core.TERMINAL`），核心层合法出现（2026-10-11 起它
   **不再是**视图列取值——看板列只有 req/design/dev/verify），故不入针脚。
3. 本节只守「核心不认业务词 + 依赖单向」；它**不**证明业务层设计正确。

### 9.2 配置层（`config.py`）—— **独立配置文件**

**为何要它**：这些值随机器/项目而变，却曾写死在代码里 ⇒ 用户唯一出路是改源码或绕过门。
最尖锐的一条：验收超时 `120s` 是**模块常量**，节点声明不了 ⇒ 跑 >2 分钟的验收
**这道门永远过不去**，只能写 `waiver`——那等于**把门关掉**（已经在发生的损失）。

**它是独立文件，不在状态目录里、不在事件流里**：

```text
<项目根>/disflow.json          ← 策略（人写、要版本化、要 review）
<项目根>/.disflow/state.json   ← 状态（派生，可由 rebuild 从事件重建）
<项目根>/.disflow/events.jsonl ← 事件（唯一事实源）
```

| 为何强调“独立” | 不独立的后果 |
| --- | --- |
| 状态是**派生**的，删掉重置是合法动作 | 删 `.disflow/` 会**静默带走策略**（配置不是从事件推出来的，`rebuild` 重建不了它） |
| 配置是**策略**，该进版本库 | 混在状态目录里 ⇒ 天然被 gitignore，策略没法 review |

**判据是可执行的**（`tests/test_config.py`）：

- **位置无关**：改 `DISFLOW_STATE_DIR` 不能改变配置路径（这是“独立”的函数化定义）。
- **重置不丢**：删掉状态目录后配置照旧生效。
- 生产默认下二者是**兄弟**（`disflow.json` vs `.disflow/`），不是子文件。

**优先级**：内建默认 < `boards.json` / `disflow.json`（`$DISFLOW_CONFIG` / `$DISFLOW_BOARDS`
可指别处）< 显式参数/节点声明。

**两个配置文件，互不为前提**：引擎参数住 `disflow.json`，**看板定义住 `boards.json`**
（板 id 即 `stage` 取值闭集）。分开的理由：板定义会**长大**（动态建板），变更节奏与
引擎参数（超时/预算/租约）不同。`disflow.json` 的 `stages` 键**已退役**（写了响亮报错 +
迁移指引，不静默回落——回落就是「同一件事两处定义」）。

| 键（`disflow.json`） | 默认 | 节点级覆盖 |
| --- | --- | --- |
| `acceptance_timeout` | 120 | `acceptance.timeout` |
| `git_timeout` | 20 | — |
| `output_tail` | 2000 | — |
| `walk_ignore` | `.git`/`node_modules`/`__pycache__` 等 | — |
| `size_budget` | 400 | `context.size_budget` |
| `lease_ttl` | 900 | `start --ttl` |
| `roles` | `dev/qa/pm` | — |
| `question_policy` | 见 `spec.QUESTION_POLICY_DEFAULT` | `context.question_policy` |

| 键（`boards.json`） | 默认 | 说明 |
| --- | --- | --- |
| `boards` | `req/design/dev/verify` | 板数组；顺序 = 板序 = 流程顺序；条目可写 `{"id","label"}` 或纯 `"id"`（label 回落成 id），可选带策略 `wip`（逐列分发上限，FR-30）/ `acceptance`（逐列默认验收，FR-31）；`done` 是**节点状态**不是板，写了拒收 |

**板定义派生出三个值**（`Config` 的派生属性，不另存字段 ⇒ 不可能分叉）：
`stages`（板 id 列表 = `stage` 取值闭集）、`entry_stage`（板序第一块 = root 容器落的板）、
`default_stage`（任务没声明 `stage` 时落的板：`dev` 还在板集里就用 `dev`，否则回落入口板）。
为何默认板不能在 reducer 里写死：板集可配后，写死的 `"dev"` 会让节点落在**一块不存在的板**上
——写时校验看不见它（只在键存在时校验），视图只能把它当「非规范列」追加，**静默错位**。
故由 `Engine.init_tree` / `Engine.expand` **写时**落进事件（`tree.init` 带 `stage`、
`node.expand` 的子节点补 `stage`）；reducer 里那份 `"req"`/`"dev"` 只剩**重放旧事件流**的兜底。

**板定义本身也进事件流**（§6.1，2026-10-11）：`Engine.init_tree` 把 `cfg.boards` 的快照写进
`tree.init` 的 `boards`（逐树覆盖 = `init --boards '<ids>'` / `set_boards`）；此后该树的
**列 / `stage` 写时校验 / 默认板** 全读**投影里的板集**（`Engine._tree_boards`；缺失回落 cfg）。
于是换 `boards.json` **不改旧树读数** ⇒ 可重放（`tests/test_boards_per_tree.py` 钉住）。

**边界一：机制词汇不进配置**。`EVENT_KINDS`←→`_HANDLERS`、`KINDS`、`STORED_STATUS` 留在
代码里，靠 `tests/test_declarations.py` 做**编译/测试期**保证；挪进配置就退化成**运行期**才发现，
而错一个字母坏的是一个事件流的可重放性。

**边界二：`stages`/`roles` 可配，但**只在写时**校验**（`Engine._validate_vocabulary`）：

| 时机 | 行为 | 为何 |
| --- | --- | --- |
| **写**（`expand` / `stage`） | 未知值 → 报错，并指出“自定义看板请写进**该树的** boards（`disflow init --boards` / `boards.json`）” | 挡住错别字（之前 `stage:"audti"` 会**静默多出一个看板列**）；闭集是**该树**的板 id，不是进程级 `cfg.stages` |
| **读**（`views.board_layout`） | 未知 stage **容忍**（追加在末尾并标注「非规范列」） | 事件流可能来自更新的版本/不同配置，视图不能崩 |

**为何 reducer 不校验**：reducer 一依赖配置，事件流就**不可重放**了——换台机器或改了
`boards.json`，重放旧事件会直接失败。「事件流不可重放」比「错别字没当场报」严重得多。
测试 `test_unknown_stage_from_another_writer_replays_and_renders` 钉住了这条。

**配置在 `doctor` 里可见**（全绿时按来源**分组**列出每个键的实际取值——两个配置文件了，
来源不再唯一；失败时只把路径并进 stderr 报告，保持“失败时 stdout 干净”的既有契约）：
配置改了行为就变，那就得看得见。

---

## 10. mgt 迁移映射（FR-28/29）

| mgt | disflow | 说明 |
| --- | --- | --- |
| `state.json.todos[]` | Node 集合 | 展平列表 → 按 `parent` 建树 |
| `type R/O/T/S` + `depth` | `kind` + 层级 | R/O = `container`；T/S = `task` |
| `parent` | `parent` | 直接映射 |
| `deps{type:blocks}` | `requires` | DAG |
| `deps{type:informs}` | `relates` | 软引用 |
| `deps{type:contains}` | **丢弃** | 与 `parent` 重复 |
| `status`(pending/ready/iterating/done) | `status`(pending/running/done) | `ready` 改为派生 |
| `global_status`(new/dev_done/…) | **转为节点** | 生成 verify/approve 节点 |
| `layer/domain/phase` | `stage` / `relates` | phase→stage；layer/domain→标签 |
| `mode`(ulww/pdf/…) | `entry` | 开工模板 |
| `files` | `touch_plan` | 写面 |
| `registry.json` | 投影中的登记 | 不再单独文件 |
| `org=dev/qa/pm` | `role` | 与 `stage` 解耦 |
| `iteration` | 一棵父树 | iteration = 有 deadline 的树 |

### 10.1 实现现状（v0.1，经独立验证核对）

导入器 `disflow/import_mgt.py` 与上表的差异（2026-10-10 由独立验证者核对，非实现者自述）：

| 上表约定 | 实现实际 | 判定 |
| --- | --- | --- |
| `global_status` → 转为节点 | 不生成节点；置 `status=done`、`stage=verify`、`imported=true` | **简化** |
| `layer/domain/phase` → `stage`/`relates` | 只写进 `context.mgt`，未映射到 `stage` | **简化** |
| `depth` | 未使用（层级只由 `parent` 推导） | 一致（`depth` 冗余） |
| `registry.json` → 投影登记 | 只读 `global_status`，未登记为资产 | **部分** |

其余项（`parent`、`status` 映射、`mode→entry`、`files→touch_plan`、`org→role`、`contains` 丢弃、`blocks/informs`）与上表一致。

**导入的降级报告（NFR-7 不静默降级的口径）**：被丢弃/写不进去的边不静默——它们在导入结果里逐条列出：

- `dropped_deps`：如 `contains` 之类按设计丢弃的边；
- `skipped_edges`：**写不进去**的边（如反向边会成环，被写时拒环拦下）；
- CLI 仍返回 0（导入整体成功），降级**可见但不阻断**。

> 独立验证（lane V4）实测：含环 fixture 导入后 `skipped_edges` 含「加边即成环」原因，图里只留单向边（DAG 保持）。若需要「有降级即非零退出」，那是另一条策略，v0.1 未做。

**简化理由（不是借口）**：

1. `global_status → 节点`：为**导入的既有数据**批量造审批节点会淹没真实待办；且 `node.import` 已显式标记 `imported:true`，**不声称过门**。「审批即节点」应用在**新任务**（`expand`）上，不是迁移时补造。
2. `phase→stage`：mgt 的 phase（A/B/C）是**排期批次**，不是 disflow 的 stage（req/design/dev/verify）；强行映射会造出假语义。
3. `registry.json` 作为登记：v0.1 只做状态迁移，不做资产登记。

→ 三条均列为 v0.2 候选，见 `docs/v0.2-backlog.md` §C（停放，含各自的触发条件）。

---

## 11. 与 flowcore 的关系

**优先复用** `fat3/scripts/flowcore`，disflow 作为它的一个**实例**（spec + reducer + gates + verbs）：

| 层 | 现状 | disflow 要做 |
| --- | --- | --- |
| `store` | 已有（append + 原子投影 + reducer 注入） | 直接用 |
| `fsm` | 已有（status_gate 表） | 直接用 |
| `gates` | 已有（门引擎，谓词注入） | 直接用 |
| `forms` | 已有（模板/流程串） | 直接用 |
| `tree` | 已有 `TreeRuntime.drive` + `ready_siblings` | 直接用 |
| **图** | **缺**（`ready_siblings` 只有 `(parent, phase)`，无依赖字段） | **补 `requires`/`relates` + `ready()` 图入边 + 写时环检测** |
| 视图 | 无统一视图层 | **补 `board`/`graph`** |

若要与 fat3 解耦 → fork `scripts/flowcore` 到本仓，保持公开 API 与语言中立判据。

---

## 12. 风险与未决

| # | 项 | 状态 / 倾向 |
| --- | --- | --- |
| R1 | 复用 flowcore vs fork | ✅ 定：只 vendor `store.py`（其余重写） |
| R2 | `relates` 传递深度 | 默认 1 跳，可配置，超预算即报错 |
| R3 | 环检测时机 | ✅ 定：写入即拒（fail-fast） |
| R4 | 跨 tree 完成信号协议 | ✅ 定：`awaits` + `tree.close`（显式信号，不共享图） |
| R5 | 多写者跨仓/跨 worktree | 单写者收口 + 租约，跨仓暂不做 |
| R6 | POC 范围 | ✅ 定：树 + `requires` + `ready` + `drive` |
| R7 | 是否 `git init` | ✅ 已做（`a76932b` 为基线） |
| R8 | 规模预算阈值 | 出厂 400 行；可 `node.context.size_budget` 覆盖，`size_waiver` 破例 |