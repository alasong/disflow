# disflow 开发看板（v0.1）

> 状态：**POC 完成（W1–W3 + acceptance 门）** · 2026-10-10
> 用途：这是 disflow **自己的第一批节点**。开发按本看板推进（dogfooding）。
> 配套：`docs/requirements.md` · `docs/design.md`

---

## 0. 进展（2026-10-10）

| 项 | 结果 |
| --- | --- |
| R1 复用决策 | ✅ 只 vendor `flowcore/store.py`（`disflow/store.py`），其余重写 |
| R2 设计定稿 | ✅ `docs/design.md` |
| POC 范围 | W1–W3 + 一道 acceptance 门（+ 作用域检查） |
| 测试 | `python3 -m unittest discover -s tests -t tests` → **Ran 37 tests, OK** |
| 端到端 | `init → expand → require → drive → start → collect → done → drive` 跑通；`drive` 两次输出相同；`rebuild --check` 一致；`doctor` 全绿 |
| 门验证 | 缺 acceptance → 拒收；验收失败 → 拒收；改动超出 `touch_plan` → 拒收 |
| 偏离 | 本机无 pytest → 用标准库 `unittest`（与 NFR-1 零依赖一致） |

**尚未做**：D80 专用视图（`board`/`tree`/`graph` 渲染）、D90 mgt 导入、V1–V4 独立验证。

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
| 波次 | id | 目标 | 依赖 | 写面 `touches` | 状态 |
| --- | --- | --- | --- | --- | --- |
| W1 | D10 | 存储层：事件流 + 原子投影 + reducer | — | `disflow/store.py` | ✅ vendor |
| W1 | D20 | 数据模型：Node/Edge + 校验 | — | `disflow/model.py` | ✅ |
| W2 | D30 | 依赖图：requires/relates + 写时环检测 | D20 | `disflow/graph.py` | ✅ |
| W2 | D40 | `ready()` 纯函数 + 波次打包 | D20,D30 | `disflow/ready.py` | ✅ |
| W3 | D50 | 上下文投影 `brief()` | D20,D30 | `disflow/context.py` | ✅ |
| W3 | D60 | 分发器 `drive/brief/collect`（只推不跑） | D10,D40,D50 | `disflow/dispatch.py` | ✅ |
| W4 | D70 | 门与证据（acceptance + 作用域） | D10,D20 | `disflow/gates.py` | ✅ |
| W4 | D80 | 视图层 `board/tree/graph` | D20,D30,D40 | `disflow/views.py` | ⬜ 未做 |
| W5 | D90 | mgt 导入器 | D20,D30 | `disflow/import_mgt.py` | ⬜ 未做 |
| W5 | D95 | CLI 装配 + `doctor` | 全部 | `disflow/cli.py` | ✅ |

### verify
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| V1 | 端到端：含跨子树依赖的树，`drive` 确定性 | D60 | ◐ 冒烟已过，未独立复跑 |
| V2 | 证据门：构造一次假完成，必须被拒 | D70 | ◐ 单测已覆盖 |
| V3 | `state rebuild --check` 一致 | D10 | ✅ 冒烟通过 |
| V4 | 从 mgt 导入真实项目，`status` 等价 | D90 | ⬜ 阻塞于 D90 |

> ◐ = 有证据但未由独立 worker 复跑（设计 §7 要求验证独立）。

### done
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| PM1 | 批准发布 v0.1 | V1,V2,V3,V4 | ⬜ |

---

## 2. 依赖图（图视图）

```text
R1 ─▶ R2 ─▶ D0 ─┬─▶ D1 ─┐
                └─▶ D2 ─┘

D10 ──────────────┐
D20 ─┬─▶ D30 ─┐   │
     └─▶ D40 ─┼───┼─▶ D60 ─┬─▶ V1
D20,D30 ─▶ D50 ┘   │        │
D20,D30 ─▶ D80     │        │
D10,D20 ─▶ D70 ────┴────────┼─▶ V2
D10 ────────────────────────┴─▶ V3
D20,D30 ─▶ D90 ─────────────────▶ V4
V1,V2,V3,V4 ─▶ PM1
```

---

## 3. 实现文件 ↔ 节点

| 节点 | 文件 | 验收 |
| --- | --- | --- |
| D10 | `disflow/store.py`（vendored） | `tests/test_store.py` |
| D20 | `disflow/model.py`, `disflow/spec.py` | `tests/test_core.py` ModelTest |
| D30 | `disflow/graph.py` | `tests/test_core.py` GraphTest |
| D40 | `disflow/ready.py` | `tests/test_core.py` ReadyTest |
| D50 | `disflow/context.py` | `tests/test_core.py` ContextTest |
| D60 | `disflow/dispatch.py` | `tests/test_engine.py`（drive 确定性） |
| D70 | `disflow/gates.py` | `tests/test_engine.py`（拒收用例） |
| D95 | `disflow/cli.py`, `disflow/engine.py`, `disflow/state.py` | CLI 冒烟 + `doctor` |

---

## 4. 开发纪律（dogfooding）

1. **本看板就是第一条狗粮**：按 §1 建树、§2 建图，`drive` 推进。
2. **只走 `drive`**：每次开工前跑 `disflow drive`，只做就绪节点。
3. **一波一并行**：同波 `touches` 不重叠者可并行派发。
4. **每个节点必须给证据**：`done` 附验收输出；无证据不放行。
5. **设计变更回写**：先改 `docs/design.md` 再改代码。
6. **验证独立**：`V*` 由不同 worker 执行。

---

## 5. 下一步

1. 完成 **V1–V4 独立验证**（尤其 V1 的 `drive` 确定性）。
2. 决定是否补 **D80 视图** 与 **D90 mgt 导入**（决定 v0.1 是否含迁移）。
3. 用 disflow 自己跑一条真实开发任务（把 §1 树 `init` 进 `.disflow/`）。
