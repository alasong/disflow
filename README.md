# disflow

任务分发流引擎：**树提供上下文继承，图提供调度与引用，看板是视图，分发器只推不跑。**

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求 v0.1：mgt 复盘 + 29 条可验收 FR/NFR |
| `docs/design.md` | 设计 v0.1：数据模型、`ready()`、分发器、看板、门 |
| `docs/dev-board.md` | **开发看板**（14/15，仅 PM 审批未做） |
| `docs/design-dispatch-tree-graph.md` | 早期讨论稿（历史） |

## 核心判断

- **分发器，不是多保活 agent**：保活的是进程，不是 agent 上下文；每任务 fresh context，从树祖先链注入。
- **树管上下文继承（唯一父、无环、有限）；图管调度与引用（`requires` 必须 DAG）**，不混用。
- **看板是投影，不是第二份存储**；多块板 = 多棵作用域隔离的树。
- **引擎只推不跑**，无常驻 daemon。

## 用法

```bash
export DISFLOW_STATE_DIR=.disflow
python3 -m disflow.cli init demo --goal "目标"
python3 -m disflow.cli expand demo root --children '[{"id":"a","goal":"做A","acceptance":{"cmd":"true","expect":"exit0"}}]'
python3 -m disflow.cli require demo b a          # 建图（写时拒环）
python3 -m disflow.cli drive demo                # 只推不跑：就绪波次 + brief 命令
python3 -m disflow.cli brief demo a              # worker 的完整输入契约
python3 -m disflow.cli start demo a --holder w1
python3 -m disflow.cli collect demo a --result '{"status":"ok","summary":"…","changed_files":[]}'
python3 -m disflow.cli done demo a --decl '{"note":"…"}'   # 跑门；无证据拒收

# 三种视图（同一投影）
python3 -m disflow.cli board demo          # 按 stage
python3 -m disflow.cli tree demo           # 按 parent
python3 -m disflow.cli graph demo [--dot]  # 按边

# 从 mgt 导入
python3 -m disflow.cli import mgt <project-root> --tree imported

# 树级：合并 context / 终止条件门 / 多块板
python3 -m disflow.cli context demo root --set '{"closure": "not_applicable: <理由>"}'
python3 -m disflow.cli close demo          # 需 done 的 evaluate + reflect/standardize → 标记该树 closed
python3 -m disflow.cli trees               # 列出所有树（● = 已 closed）
python3 -m disflow.cli reap demo           # 回收过期租约（worker 崩溃后的兜底）

python3 -m disflow.cli doctor
```

## 开发看板（dogfooding）

```bash
python3 scripts/seed_dev_board.py      # 把 docs/dev-board.md 种进 .disflow/（每步真跑验收）
python3 -m disflow.cli board disflow-v0.1
python3 -m disflow.cli drive disflow-v0.1
```

## 测试

```bash
python3 -m unittest discover -s tests -t tests    # 87 passed（零第三方依赖）
```

## 结构

```text
disflow/
  store.py        # L0 事件流 + 原子投影（vendored 自 fat3 flowcore）
  state.py        # 事件 → 投影 reducer
  model.py        # 节点模型 / 树语义
  graph.py        # requires/relates + 环检测
  ready.py        # ready() / 波次打包 / 卡死
  context.py      # brief() 上下文投影
  gates.py        # 验收门 + 证据 + 作用域 + 规模预算 + 树终止条件
  dispatch.py     # drive/brief/collect（只推不跑）
  views.py        # board/tree/graph（同源视图）
  import_mgt.py   # mgt 导入器
  engine.py       # 动词面（锁 + append）
  cli.py          # 命令装配
tests/            # stdlib unittest
scripts/          # seed_dev_board.py
```

## 状态

v0.1 完成（16/17，仅 PM1 审批未做）：87 测试通过、CLI 端到端、`drive` 确定性、三视图同源、mgt 导入、五道门（acceptance/design_first/size_budget/tree_closure/**tree_coverage**）、**多块板**（多树作用域隔离 + 跨树 `awaits` 信号）、**租约超时回收 `reap`**、`doctor` 全绿。需求覆盖见 `docs/requirements.md` §7。
已知局限见 `docs/dev-board.md` §0（V1–V4 未独立复跑、V4 用合成 fixture、`tree_coverage` 未做）。
