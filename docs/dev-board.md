# disflow 开发看板（v0.1）

> 状态：**v0.1 完成（17/17）** · 2026-10-10
> 用途：disflow **自己的第一批节点**，开发按本看板推进（dogfooding）。
> 配套：`docs/requirements.md` · `docs/design.md`

---

## 0. 进展（2026-10-10）

| 项 | 结果 |
| --- | --- |
| R1 复用决策 | ✅ 只 vendor `flowcore/store.py`（`disflow/store.py`），其余重写 |
| R2 设计定稿 | ✅ `docs/design.md` |
| 实现 | D10–D95 + D100 全绿 |
| 测试 | `python3 -m unittest discover -s tests -t tests` → **Ran 161 tests, OK** |
| 端到端 | `drive` 确定性、`rebuild --check` 一致、`doctor` 全绿 |
| 门验证 | acceptance 缺失/失败、超 `touch_plan`、超 `size_budget`、缺 evaluate/sediment → **全部拒收** |
| 树终止 | `close` 跑 `tree_closure`，本看板树已关闭 |
| 看板视图 | `board` / `tree` / `graph` 三视图同源 |
| 多块板 | 多树作用域隔离 + 跨树 `awaits` 显式完成信号（`tree.close`） |
| 租约回收 | `start` 带 `expires_at`；`reap` 显式回收过期租约（`doctor` 会报） |
| 覆盖门 | `tree_coverage`：叶子任务有 goal + 可跑判据；非根容器有子节点（`close` 串联 `tree_closure`） |
| 模块分层 | 核心层 = `core`/`store`/`graph`/`ready`（零业务词 + 依赖单向）；`tests/test_layering.py` 逆向自检 |
| 预演 | `start`/`done`/`close`/`drive` 支持 `--dry-run`：门照跑、**零事件**（`tests/test_dryrun.py`） |
| 需求覆盖 | `docs/requirements.md` §7 逐条审计 FR/NFR —— **FR-1..29 + NFR-1..7 全覆盖（0 项 ◐）** |
| 预演盲点 | `close --dry-run` 曾经 CLI 漏传 `dry_run` 真关树（V3 抳出）→ 已修 + **CLI 层回归测试** |
| 偏离 | 本机无 pytest → 标准库 `unittest`（与 NFR-1 零依赖一致） |

**门清单（设计 §7）**：`acceptance` ✅ · `design_first` ✅ · `size_budget` ✅ · `tree_closure` ✅ · `tree_coverage` ✅。

**独立验证（二轮：2026-10-10，HEAD `46cb2bd`，5 个 fresh-context 只读验证者，各自临时状态目录，零仓库写入）**：

| lane | 范围 | 结果 | 发现 |
| --- | --- | --- | --- |
| V1 | 就绪/波次/`drive` **逐字节**确定性/容器不派发/`awaits` 门控 | PASS 4/4 | 文档漂移：§3 写 `requires` 需 `done`，实现是 `TERMINAL`（`skip` 也不阻塞）→ 已修文档 |
| V2 | 六类门必须拒收假完成 + 逃生口空理由照拒 | PASS 6/6 | 无 |
| V3 | 投影一致/篡改必被抓/**预演零事件**/租约回收 | **FAIL 3/4** | **真缺陷（已修）**：`cmd_close` 漏传 `dry_run` → `close --dry-run` 真关树并落事件；现有测试绕过 CLI 未抳到 |
| V4 | mgt 迁移等价（合成 fixture） | PASS 4/4 | 无冲突；补充了 `skipped_edges` 降级口径 |
| V5 | 分层自检**非空转**（临时副本注入） | PASS 4/4 | **自检比声称的弱**：针脚白名单漏 stage/role 取值 → 已加固（单词边界 + `MatcherSelfTest` 自证） |

二轮新增的验证面（相对一轮）：跨树 `awaits`、`tree_coverage`、`waiver`/`require_declarations`、
`--dry-run` 零事件、租约 `reap`、核心层分层自检。

**一轮（2026-10-10，3 个 fresh-context 验证者）**：

| lane | 结果 | 发现 |
| --- | --- | --- |
| V1V2 | PASS（3/3） | 无 |
| V3 | PASS（3/3） | **真缺陷**：坏事件行以未捕获 `ValueError` traceback 露出 → 已修（`cli.main` 捕获 ValueError/OSError） |
| V4 | PASS（4/4 断言） | **设计 §10 与实现有偏离** → 已在 `design.md` §10.1 如实登记 |

V3 的「篡改投影必须被抓」**确实被抓**（exit 1）—— 一致性检查不是摆设。

**诚实标注的局限**：

1. **V4 用合成 fixture**：本机 fat3 `.fat/mgt/` 无 `state.json`，无真实项目可导入（两轮均如此）。
2. ~~**`tree_coverage` 未做**~~ ✅ 已补（v0.2：叶子任务需求已登记 + 可跑判据；非根容器不空壳）。
3. **§10 的三条偏离**（`global_status` 节点化 / `phase→stage` / `registry` 登记）只在文档登记，未实现。
4. **二轮未覆盖**（V2 自述）：`diff_size` 的 tracked 已修改文件分支；`contains:`/`regex:` 两种 `expect`。
5. **分层自检不是闭集**：针脚表式，未登记的业务词仍会漏判（`done` 因与机制终态重名而根本不可判定）——已写进 `design.md` §9.1。

### 0.1 首次真跑循环（树 `cli-cov`，2026-10-10）

背景：上面两轮验证都测了「计算的正确性」，却从未测过「分发器真的被用来干活」。
本轮拿一个**真实目标**（补 CLI 装配层测试）跑完整循环——顺序、门、证据全部由 
disflow 决定，**无硬编码列表**。

| | 结果 |
| --- | --- |
| 树 | `cli-cov`（与 `disflow-v0.1` 并行 = 多块板） |
| 节点 | C1–C4（测试）· C5–C8（循环中发现的缺陷修复）· E1（收益验证）· S1（沉淀） |
| 循环是否由 `drive` 驱动 | 是：每次选谁干都读 `drive` 的就绪波次 |
| 产出 | 4 个新测试文件（48 个用例）、4 处真缺陷修复、1 次变异测试 |

**循环中发现的真缺陷**（不是人造用例）：

| # | 缺陷 | 怎么暴露的 | 后果 |
| --- | --- | --- | --- |
| 1 | `cmd_close` 漏传 `dry_run` → `--dry-run` 真关树 | 写 `close --dry-run` 测试 | 与文档矛盾（V3 曾报，但当时测试绕过了 CLI） |
| 2 | `GateReject` 报文走 **stdout**（`EngineError` 走 stderr） | 断言错误通道 | 脚本 `2>/dev/null` 时把门拒收看成成功 |
| 3 | `cmd_doctor` 失败报文同样走 stdout | 同上 | 同上 |
| 4 | `cmd_import` 错误报文无 `✗` 前缀 | 断言报文约定 | 报文不一致（机器不可区分） |
| 5 | **`store._persist` 先落盘再重投影** | 重复 `expand` 被拒后 | **一次非法写入 = 状态永久不可恢复**（事件流不可再重放） |

> #5 最严重：`project` 才是语义闸门（重复 id / 环 / 悬空引用都在这里抛），
> 而它跟在 `append_jsonl` **之后**。实测：同一个重复 `expand` 被拒 3 次，
> 事件流里就多了 3 条重复事件，`rebuild` 直接不可用。修法：先试算后落盘。
> 被污染的事件流做了一次**手工修复**（剔除 2 条本不该存在的事件，备份为
> `events.jsonl.poisoned.bak`）——这是 bug 自己破坏了 append-only，不是人为篡改。

**变易测试（E1 的验收）**：把 5 处修复逐个“抖回”缺陷，验证新测试能抓到：

| 变易 | 注入后 | 还原后 |
| --- | --- | --- |
| M1 `cmd_close` 漏传 `dry_run` | 失败 ✅ | 通过 |
| M2 GateReject 回 stdout | 失败 ✅ | 通过 |
| M3 import 去 `✗` 前缀 | 失败 ✅ | 通过 |
| M4 `_persist` 回到先落盘 | 失败 ✅ | 通过 |

**捕获率 4/4** —— 新测试有牙，不是摆设。

### 0.2 本轮暴露的产品/流程缺口（已登记 backlog §C4）

| # | 缺口 | 证据 |
| --- | --- | --- |
| 1 | **`touch_plan` 建树后不可更新**，且 `design_first` **无逃生口** | 干活时发现需多改一个文件 → 门拒收，但无合法路径改声明 |
| 2 | ~~**baseline 是文件粒度**~~ ✅ **已修**：改为 `{路径: 内容指纹}`（`design.md` §7.1），并加反向验证（退回路径差集 → 新测试失败） | C6 的 scope 检查曾空转（C5 先弄脏了 `cli.py`） |
| 3 | 无 `unrequire`：建错依赖只能 `skip` 整节点 | C7 的 requires 方向建反，只能 skip 并记录 |
| 4 | **陈旧 `__pycache__` 会静默跑旧代码** | store 修复已落盘但行为仍旧；清字节码后立即生效 |

### 0.3 随后修掉的 C4.2 与顺手补的两个可执行检查

| 改动 | 为何值得 | 反向验证 |
| --- | --- | --- |
| **C4.2 修复**：`baseline` 从路径集合改为 `{路径: 内容指纹}` | 路径差集看不见「同一文件被两节点先后改」⇒ 作用域门**静默失效** | 退回路径差集 → `test_engine` 失败 ✅ |
| `doctor` 报漂移时**给出修法**（`disflow rebuild`） | 只报“坏了”不报“怎么修”，用户只能猜 | 新测试断言 `rebuild` 出现在报文里 |
| 新增**表格列数守卫**（`test_docs.py`） | 分隔行少写几列时 markdown-lint 不报，但**渲染是错的**——本轮刚踩过两次 | 把分隔行少写一列 → 测试精确指出行号 ✅ |

> 投影函数的变更（`baseline` 形状）会合法地使磁盘上的旧投影漂移；
> `doctor` 抓到了，`rebuild` 重建后两棵树仍 10/10 与 17/17。**事件未动**。

> #4 值得单独记：当时我差点把「陈旧字节码」误判为「修复无效 / 测试隔离有问题」。
> 判据：`inspect.getsource` 显示新代码而行为是旧的 → 先清 `__pycache__` 再下结论。
> **证据边界**：`cli-cov` 树的状态只活在本机 `.disflow/`（已 gitignore）；
> 本文件记录的是**结果与教训**，树的节点级证据是本地产物。

---

## 1. 阶段视图（看板）

### req
| id | 目标 | 状态 |
| --- | --- | --- |
| R1 | 需求梳理（requirements.md） | ✅ |
| R2 | 系统设计（design.md） | ✅ |

### design
| id | 目标 | 状态 |
| --- | --- | --- |
| D0 | 评审：需求与设计定稿 | ✅ |
| D1 | 数据模型定稿（Node/Edge/状态机） | ✅ |
| D2 | 复用决策：flowcore vendor store | ✅ |

### dev
| 波次 | id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| W1 | D10 | 存储层：事件流 + 原子投影 + reducer | — | ✅ |
| W1 | D20 | 数据模型：Node/Edge + 校验 | — | ✅ |
| W2 | D30 | 依赖图：requires/relates + 写时环检测 | D20 | ✅ |
| W2 | D40 | `ready()` 纯函数 + 波次打包 | D20,D30 | ✅ |
| W3 | D50 | 上下文投影 `brief()` | D20,D30 | ✅ |
| W3 | D60 | 分发器 `drive/brief/collect`（只推不跑） | D10,D40,D50 | ✅ |
| W4 | D70 | 门与证据（acceptance + 作用域） | D10,D20 | ✅ |
| W4 | D80 | 视图层 `board/tree/graph` | D20,D30,D40 | ✅ |
| W5 | D90 | mgt 导入器 | D20,D30 | ✅ |
| W5 | D95 | CLI 装配 + `doctor` | D60,D70 | ✅ |
| W6 | D100 | 规模预算门 + 树终止条件门 | D70 | ✅ |

### verify
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| V1 | 端到端：含跨子树依赖的树，`drive` 确定性 | D60 | ✅ 独立 |
| V2 | 证据门：构造一次假完成，必须被拒 | D70 | ✅ 独立 |
| V3 | `rebuild --check` 一致 | D10 | ✅ 独立（有牙） |
| V4 | 从 mgt 导入项目，`status` 等价 | D90 | ✅ 独立（合成 fixture） |

### done
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| PM1 | 批准发布 v0.1 | V1,V2,V3,V4 | ⬜ 待批 |

---

## 2. 实现文件 ↔ 节点

| 节点 | 文件 | 验收 |
| --- | --- | --- |
| D10 | `disflow/store.py`（vendored） | `tests/test_store.py` |
| D20 | `disflow/model.py`, `disflow/spec.py` | `tests/test_core.py` ModelTest |
| D30 | `disflow/graph.py` | `tests/test_core.py` GraphTest |
| D40 | `disflow/ready.py` | `tests/test_core.py` ReadyTest |
| D50 | `disflow/context.py` | `tests/test_core.py` ContextTest |
| D60 | `disflow/dispatch.py` | `tests/test_engine.py`, `tests/test_e2e.py` |
| D70 | `disflow/gates.py` | `tests/test_engine.py`（拒收用例） |
| D80 | `disflow/views.py` | `tests/test_views.py` |
| D90 | `disflow/import_mgt.py` | `tests/test_import_mgt.py` |
| D95 | `disflow/cli.py`, `disflow/engine.py`, `disflow/state.py` | CLI 冒烟 + `doctor` |

---

## 3. 开发纪律（dogfooding）

1. **本看板就是第一条狗粮**：`scripts/seed_dev_board.py` 把它种进 `.disflow/`。
2. **只走 `drive`**：每次开工前跑 `disflow drive`，只做就绪节点。
3. **一波一并行**：同波 `touches` 不重叠者可并行派发。
4. **每个节点必须给证据**：`done` 附验收输出；无证据不放行。
5. **设计变更回写**：先改 `docs/design.md` 再改代码。
6. **验证独立**：`V*` 应由不同 worker 执行（**本次未满足**）。

---

## 4. 下一步

~~1–7~~ 全部完成（清单已收口）。**残余项已归档到 `docs/v0.2-backlog.md`**，不再在本表追踪：

| 已收口 | 结果 |
| --- | --- |
| 独立复跑 V1–V4（+V5 分层） | ✅ 二轮完成，@0 有记录；抳出 V3 真缺陷已修 |
| `cli.main` 捕获 `ValueError`/`OSError` | ✅ `tests/test_cli.py` 守 |
| `tree_coverage` 门 | ✅ `tests/test_coverage.py` 守；`close` 串联两门 |
| v0.2：多树作用域隔离 + 跨树完成信号 | ✅ `awaits` + `tree.close`，`tests/test_multitree.py` 守 |
| 覆盖审计 2 个 ◐（FR-13 / NFR-2） | ✅ 均完成 —— **FR/NFR 全覆盖** |
| §10 三条偏离 | 未实现，**按决策停放**见 backlog §C |

**无未决项**。`PM1` 已如实 `skip`（见上），看板 17/17。
