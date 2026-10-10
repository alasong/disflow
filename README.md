# disflow

任务分发流引擎：**树提供上下文继承，图提供调度与引用，看板是视图，分发器只推不跑。**

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求 v0.1：mgt 复盘 + 29 条可验收 FR/NFR |
| `docs/design.md` | 设计 v0.1：数据模型、`ready()`、分发器、看板、门 |
| `docs/architecture.md` | **架构图**（Mermaid：四面职责 / 数据流 / 就绪 / 门 / 分层 / 多块板） |
| `docs/dev-board.md` | **开发看板**（17/17 完成） |
| `docs/v0.2-backlog.md` | v0.2 候选**存档**（停放，非承诺：每项写明触发条件） |
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
python3 -m disflow.cli done demo n7 --dry-run    # 预演：跑门不落事件（先看会不会被拒）
```

## 测试

```bash
python3 -m unittest discover -s tests -t tests    # 226 passed（零第三方依赖）
python3 scripts/mutation_check.py                 # 变异测试：19/19（验证测试真的有牙）
```

## 结构

```text
disflow/
  core.py         # 机制词汇（KINDS/STATUS/TERMINAL）—— 核心层，零业务词
  store.py        # L0 事件流 + 原子投影（vendored 自 fat3 flowcore）
  state.py        # 事件 → 投影 reducer
  model.py        # 节点模型 / 树语义
  graph.py        # requires/relates + 环检测
  ready.py        # ready() / 卡死（纯机制，不含写面）
  context.py      # brief() 上下文投影
  gates.py        # 五道门 + 证据（acceptance/design_first/size_budget/tree_closure/tree_coverage）
  dispatch.py     # drive/brief/collect + 波次打包（写面，业务）
  views.py        # board/tree/graph（同源视图）
  import_mgt.py   # mgt 导入器
  engine.py       # 动词面（锁 + append）
  cli.py          # 命令装配
tests/            # stdlib unittest（含 test_layering.py：核心零业务词的逆向自检）
scripts/          # seed_dev_board.py
```

模块分两层（NFR-2）：**核心层** `core`/`store`/`graph`/`ready` 零业务词、依赖单向；
`tests/test_layering.py` 把这条线变成可执行断言（而不是文档承诺）。

## 状态

**首次真跑循环**（2026-10-10，树 `cli-cov`）：顺序/门/证据全由 `drive` 决定（无硬编码列表），
产出 4 个新测试文件（48 用例）+ 4 处真缺陷修复 + 一次变异测试（**捕获率 4/4**）。
其中 `store._persist` 的「先落盘再试算」是真缺陷（**一次非法写入 = 事件流永久不可重放**）。
详见 `docs/dev-board.md` §0.1/§0.2；暴露的机制缺口见 `docs/v0.2-backlog.md` §C4。

v0.1 完成（**17/17**）：189 测试通过、CLI 端到端、`drive` 确定性、三视图同源、mgt 导入、五道门（acceptance/design_first/size_budget/tree_closure/**tree_coverage**）、**多块板**（多树作用域隔离 + 跨树 `awaits` 信号）、**租约超时回收 `reap`**、**核心层零业务词**（NFR-2，逆向自检）、**`--dry-run` 预演**（FR-13）、**改动归因用内容指纹**（设计 §7.1）、**写面声明的当口改动 `rescope` + 破例 `scope_waiver`**（设计 §7.2）、**边的增删 `require`/`unrequire`**（设计 §7.3）、`doctor` 全绿。需求覆盖见 `docs/requirements.md` §7（FR/NFR **全覆盖**）。

> `PM1`（最后一个节点）是初始种下的「批准发布」仪式，但本看板只是 dogfooding 演示、无下游消费者。
> 已如实 `skip`（理由入库），**不假装发生过真实审批**——种子脚本里可复现，不是一次性手工操作。

**二轮独立验证**（5 条 lane，HEAD `46cb2bd`）见 `docs/dev-board.md` §0：V3 抳出一个真缺陷（`close --dry-run` 真关树）已修 + 补 CLI 层回归测试；V5 抳出分层自检偏弱已加固。
已知局限见 `docs/dev-board.md` §0（V4 用合成 fixture；分层自检非闭集；`diff_size` tracked 分支未覆盖）。
