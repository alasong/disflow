# disflow 开发看板（v0.1 种子）

> 状态：**计划** · 2026-10-10
> 用途：这是 disflow **自己的第一批节点**。开发本身按本看板推进（dogfooding）：
> `disflow init` 后把本文件导入为一棵树，之后 `drive → brief → 开发 → done`。
> 配套：`docs/requirements.md` · `docs/design.md`

---

## 0. 元信息

| 项 | 值 |
| --- | --- |
| 树根 goal | 交付 disflow v0.1：树+图+只推不跑分发器的最小闭环 |
| 阶段 | `req → design → dev → verify → done` |
| 角色 | `dev`（实现）· `qa`（独立验证）· `pm`（批准） |
| 已完成的阶段节点 | `R1 需求`（requirements.md）· `R2 设计`（design.md） |
| 开发前置 | **需求与设计评审通过后**才进入 `dev`（用户约定） |

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
| D0 | 评审：需求与设计定稿 | ⬜ |
| D1 | 数据模型定稿（Node/Edge/状态机） | ⬜ |
| D2 | 复用决策：flowcore vs fork | ⬜ |

### dev（按依赖波次）
| 波次 | id | 目标 | 依赖 | 写面 `touches` | 验收 |
| --- | --- | --- | --- | --- | --- |
| W1 | D10 | 存储层：事件流 + 原子投影 + reducer | — | `src/store.py` | 事件重放=投影，单测通过 |
| W1 | D20 | 数据模型：Node/Edge + 校验 | — | `src/model.py` | schema 校验单测通过 |
| W2 | D30 | 依赖图：requires/relates + 写时环检测 | D20 | `src/graph.py` | 环用例被拒，DAG 用例通过 |
| W2 | D40 | `ready()` 纯函数 + 波次打包 | D20,D30 | `src/ready.py` | 确定性单测：同输入同输出 |
| W3 | D50 | 上下文投影 `brief()` | D20,D30 | `src/context.py` | 祖先链+前置摘要正确 |
| W3 | D60 | 分发器 `drive/brief/collect`（只推不跑） | D10,D40,D50 | `src/dispatch.py` | 两次 drive 输出相同 |
| W4 | D70 | 门与证据（acceptance/design_first/size_budget） | D10,D20 | `src/gates.py` | 无证据不能 done |
| W4 | D80 | 视图层 `board/tree/graph` | D20,D30,D40 | `src/views.py` | 三视图同源 |
| W5 | D90 | mgt 导入器 | D20,D30 | `src/import_mgt.py` | 导入后 status 等价 |
| W5 | D95 | CLI 装配 + `doctor` | 全部 | `src/cli.py`, `src/doctor.py` | `doctor` 全绿 |

### verify（独立，role=qa）
| id | 目标 | 依赖 |
| --- | --- | --- |
| V1 | 端到端：含跨子树依赖的树，`drive` 确定性 | D60 |
| V2 | 证据门：构造一次假完成，必须被拒 | D70 |
| V3 | `state rebuild --check` 一致 | D10 |
| V4 | 从 mgt 导入真实项目，`status` 等价 | D90 |

### done
| id | 目标 | 依赖 |
| --- | --- | --- |
| PM1 | 批准发布 v0.1 | V1,V2,V3,V4 |

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

> 注：D0/D1/D2 是 `design` 阶段的容器/决策节点；D10 起是 `dev` 任务节点。
> 依赖只用 `requires`（DAG）；`V*` 通过 `consumes` 消费 `D*` 的 `produces`。

---

## 3. 节点详表（可直接喂给 `expand`）

> 每条 = 一个 `task` 节点：`stage / role / goal / requires / produces / touch_plan / acceptance`。

| id | stage | role | produces | acceptance |
| --- | --- | --- | --- | --- |
| D10 | dev | dev | `event_store` | `pytest tests/test_store.py` exit0 |
| D20 | dev | dev | `node_model` | `pytest tests/test_model.py` exit0 |
| D30 | dev | dev | `dep_graph` | `pytest tests/test_graph.py` exit0（含环用例） |
| D40 | dev | dev | `ready_fn` | `pytest tests/test_ready.py` exit0 |
| D50 | dev | dev | `brief_fn` | `pytest tests/test_context.py` exit0 |
| D60 | dev | dev | `dispatcher` | `pytest tests/test_dispatch.py` exit0 |
| D70 | dev | dev | `gate_engine` | `pytest tests/test_gates.py` exit0 |
| D80 | dev | dev | `views` | `pytest tests/test_views.py` exit0 |
| D90 | dev | dev | `mgt_importer` | `pytest tests/test_import.py` exit0 |
| D95 | dev | dev | `cli` | `disflow doctor` exit0 |
| V1 | verify | qa | `e2e_report` | 脚本 `tests/e2e_drive.sh` 对比两次输出 |
| V2 | verify | qa | `gate_report` | 假完成用例被拒 |
| V3 | verify | qa | `rebuild_report` | `disflow state rebuild --check` exit0 |
| V4 | verify | qa | `migration_report` | 人工核对清单签署 |
| PM1 | verify | pm | `approval` | 四项 verify 全 done |

---

## 4. 开发纪律（dogfooding）

1. **本看板就是第一条狗粮**：评审通过后 `disflow init disflow-v0.1`，按 §1 建树、§2 建图。
2. **只走 `drive`**：每次开工前跑 `disflow drive`，只做它列出的就绪节点；不手动跳过依赖。
3. **一波一并行**：同波 `touches` 不重叠的节点可并行派发（每节点一个 worker）。
4. **每个节点必须给证据**：`done` 时附验收命令输出；无证据不放行。
5. **设计变更回写**：实现中发现设计要改，先改 `docs/design.md` 并说明理由，再改代码（design_first 门）。
6. **验证独立**：`V*` 由不同的 worker 执行，不得由实现者自己签。

---

## 5. 下一步

1. 评审 `requirements.md` + `design.md`（D0）。
2. 定 R1/R6/R7（复用 vs fork、POC 范围、是否 git init）。
3. 通过后进入 W1。
