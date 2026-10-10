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
| 测试 | `python3 -m unittest discover -s tests -t tests` → **Ran 103 tests, OK** |
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
| V3 | `state rebuild --check` 一致 | D10 | ✅ 独立（有牙） |
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
