# disflow

任务分发流引擎：**树提供上下文继承，图提供调度与引用，看板是视图，分发器只推不跑。**

## 文档

| 文档 | 内容 |
| --- | --- |
| `docs/requirements.md` | 需求 v0.1：mgt 复盘 + 29 条可验收 FR/NFR |
| `docs/design.md` | 设计 v0.1：数据模型、`ready()`、分发器、看板、门 |
| `docs/dev-board.md` | **开发看板**（POC 完成状态 + 证据） |
| `docs/design-dispatch-tree-graph.md` | 早期讨论稿（历史） |

## 核心判断

- **分发器，不是多保活 agent**：保活的是进程，不是 agent 上下文；每任务 fresh context，从树祖先链注入。
- **树管上下文继承（唯一父、无环、有限）；图管调度与引用（`requires` 必须 DAG）**，不混用。
- **看板是投影，不是第二份存储**；多块板 = 多棵作用域隔离的树。
- **引擎只推不跑**，无常驻 daemon。

## 用法（POC）

```bash
export DISFLOW_STATE_DIR=.disflow        # 状态目录（默认 .disflow）
python3 -m disflow.cli init demo --goal "目标"
python3 -m disflow.cli expand demo root --children '[{"id":"a","goal":"做A","acceptance":{"cmd":"true","expect":"exit0"}}]'
python3 -m disflow.cli require demo b a          # 建图（写时拒环）
python3 -m disflow.cli drive demo                # 只推不跑：就绪波次 + brief 命令
python3 -m disflow.cli brief demo a              # worker 的完整输入契约
python3 -m disflow.cli start demo a --holder w1
python3 -m disflow.cli collect demo a --result '{"status":"ok","summary":"…","changed_files":[]}'
python3 -m disflow.cli done demo a --decl '{"note":"…"}'   # 跑门；无证据拒收
python3 -m disflow.cli doctor
```

## 测试

```bash
python3 -m unittest discover -s tests -t tests    # 37 passed（零第三方依赖）
```

## 结构

```text
disflow/          # 包（store 为 vendored 自 fat3 flowcore）
  store.py        # L0 事件流 + 原子投影（vendored）
  state.py        # 事件 → 投影 reducer
  model.py        # 节点模型 / 树语义
  graph.py        # requires/relates + 环检测
  ready.py        # ready() / 波次打包 / 卡死
  context.py      # brief() 上下文投影
  gates.py        # 验收门 + 证据 + 作用域
  dispatch.py     # drive/brief/collect（只推不跑）
  engine.py       # 动词面（锁 + append）
  cli.py          # 命令装配
tests/            # stdlib unittest
```

## 状态

POC 完成（W1–W3 + acceptance 门）：37 测试通过，CLI 端到端跑通，`drive` 确定性，`doctor` 全绿。
未做：专用视图（`board`/`tree`/`graph`）、mgt 导入器、独立验证（V1–V4）。见 `docs/dev-board.md`。
