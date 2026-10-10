# disflow 开发看板（v0.1）

> 状态：**v0.1 完成待批准（14/15，仅 PM1 审批未做）** · 2026-10-10
> 用途：disflow **自己的第一批节点**，开发按本看板推进（dogfooding）。
> 配套：`docs/requirements.md` · `docs/design.md`

---

## 0. 进展（2026-10-10）

| 项 | 结果 |
| --- | --- |
| R1 复用决策 | ✅ 只 vendor `flowcore/store.py`（`disflow/store.py`），其余重写 |
| R2 设计定稿 | ✅ `docs/design.md` |
| 实现 | D10–D95 全绿（含 D80 视图层、D90 mgt 导入器） |
| 测试 | `python3 -m unittest discover -s tests -t tests` → **Ran 52 tests, OK** |
| 端到端 | `drive` 确定性、`rebuild --check` 一致、`doctor` 全绿 |
| 门验证 | 缺 acceptance / 验收失败 / 改动超出 `touch_plan` → 三种都拒收 |
| 看板视图 | `board`（按 stage）/ `tree`（按 parent）/ `graph`（按边）三视图同源 |
| 偏离 | 本机无 pytest → 标准库 `unittest`（与 NFR-1 零依赖一致） |

**诚实标注的局限**：

1. **V1–V4 由同一 worker 执行，未独立复跑**（设计 §7 要求验证独立）。判据本身已由测试覆盖，但「独立验证」未满足。
2. **V4 用合成 fixture**：本机 fat3 `.fat/mgt/` 无 `state.json`，无真实项目可导入。
3. **`design_first` 门**目前是「改动 ⊆ `touch_plan`」，未含 `size_budget` 与 `tree_closure`。

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

### verify
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| V1 | 端到端：含跨子树依赖的树，`drive` 确定性 | D60 | ✅（同 worker） |
| V2 | 证据门：构造一次假完成，必须被拒 | D70 | ✅（同 worker） |
| V3 | `state rebuild --check` 一致 | D10 | ✅（同 worker） |
| V4 | 从 mgt 导入项目，`status` 等价 | D90 | ✅ 合成 fixture |

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

1. **独立复跑 V1–V4**（换成不同 worker），满足设计 §7。
2. 补 `size_budget` / `tree_closure` 门。
3. 若要 v0.2：多棵树（多块板）作用域隔离 + 跨树完成信号。
