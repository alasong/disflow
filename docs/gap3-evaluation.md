# gap3 收益验证：量化 / 迭代 / worktree（做完 ≠ 有用）

⟳ gap3 · C/evaluate · 在跑：同一探针跑在 `d581d02`(做前) 与 `591bdf3`(做后) 上取实测差值 + 三项负向对照 · 下一步：把结论与遗漏面交给树收口（本节点认领 R-METRICS/R-ITER/R-WORKTREE）

> 口径：**只有同一探针在两个提交上的两组原始输出才算证据**。做前 = `d581d02`（三项工作开始前的基线），做后 = `591bdf3`（当前 HEAD）。凡不能被一条命令复核的句子，一律显式标 `[判断]`；能复核的标 `[实测]`。

---

## 0. 计划（开工前，≤10 行）

1. 接口：三动词 `metrics` / `deadline` / `worktree`；三个纯函数面 `metrics.summary` / `metrics.burndown` / `worktree.plan|reconcile|reap`。
2. 边界：做前无 `disflow/metrics.py`、无 `disflow/worktree.py`、无 `deadline` 动词——探针落到「命令不存在」。
3. 写面：**只新建 `docs/gap3-evaluation.md`**，不改任何源码/测试（负向对照放 `/tmp` 抛弃副本里做）。
4. 探针一：`python3 -m disflow.cli metrics <tree> --json`（做后）vs 做前 `status <tree> --json` 等价读数。
5. 探针二：`git grep -n 'iteration|velocity|burndown|deadline' <commit> -- disflow/` 两边命中。
6. 探针三：`python3 -m disflow.cli worktree plan/list/reap/apply <tree>`（做前命令不存在）。
7. 负向对照：改坏 `_estimated` / `parse_deadline` / `safe_to_drop` → 对应测试必须变红 → 贴红 → 抛弃副本自动还原。
8. 验收点：每个「收益」claim 旁有可复制命令；无命令的句子标 `[判断]`；遗漏面逐条给「没评 + 为什么」。
9. 需求认领：R-METRICS/R-ITER/R-WORKTREE 与交付节点 `968edaf1267e`/`0a1f5cbbfe3e`/`5cfaae85fcf5` 的 `verify_cmd` 逐条对齐。
10. 收尾：全量 `unittest` + `pyright` + `git status --porcelain`；**不 commit**。

---

## 1. 方法：为什么是「同一探针对照」而不是叙述

一句话：**收益是可机检的事实声明，不让实现者自证**。本会话已两次抓到「测试通过是真空的」（变异测试对新模块 0 针脚；脚手架 `git() ` 吞错误让断言比较空值）。所以本验证对每条「收益」claim 都要求：

- 有**一条命令**；
- 该命令在**做前与做后两个提交上各跑一次**，两组原始输出都贴出；
- 若做前也答得出 → 如实记「**未见收益**」。

探针用一棵演示树（`git init` 的临时仓 + `DISFLOW_STATE_DIR` 放**仓外**——`worktree` 会按设计拒绝仓内路径）。演示树的事件被回拨 3 天，好让 `days_elapsed>0`，从而速度/eta 是实数而非结构化 0。构建脚本见 §8。

**环境**：macOS，Python 3.12，git 2.54；测试框架 stdlib `unittest`（无 pytest）。跑测试前用 `ps -eo command | grep [m]utation_check` 确认无并发变异跑——本会话期间 `disflow-pins`（**另一份克隆**）确有一个变异跑在转，故本文所有测试都只在本仓 `disflow-eval` 与 `/tmp` 抛弃副本里跑。

---

## 2. 三项收益：做前 / 做后同探针对照

### 2.1 量化 —— owner 能答「还要多久」

**claim**：做前只能答「现在在哪」（进度 1/3）；做后能答「还剩几点 + 速度 + 预计几天」（velocity/eta）。

探针（同一命令文本，两提交各跑一次；cwd = 演示仓，`DISFLOW_STATE_DIR` 仓外）：

```bash
# 做后（HEAD）可 import disflow.metrics；做前不带该模块
python3 -m disflow.cli metrics demo --json
```

**做后（HEAD）原始输出**（节选，完整 JSON 见 §8）：

```text
树 demo：量化
  点数    8（已完成 3，剩 5） · 未估 1 个节点
  进度    3 天 · 速度 1 点/天 → 预计还需 5 天
  截止    2026-10-20 · 剩 10 天
  燃尽    2026-10-07 5 → 2026-10-10 5（理想线 → 2026-10-10 归零）
```

```json
"summary": {"points_total": 8.0, "points_done": 3.0, "points_left": 5.0,
            "points_unestimated": 1, "days_elapsed": 3,
            "velocity": 1.0, "eta_days": 5.0, ...}
```

**做前（d581d02）原始输出**（`metrics` 动词不存在）：

```text
disflow: error: argument command: invalid choice: 'metrics'
(choose from init, goal, expand, ..., close, reap, trees, doctor)
(exit=2)
```

做前唯一等价读数 `status demo --json` 的原始输出（`progress` 段）：

```json
"progress": {"tasks": 3, "done": 1, "pct": 33},
"ready": ["B", "C"], "running": [], "stuck": false
```

（做前 `status --json` **逐节点**里确有 `"points": 3.0` / `"points": 5.0` 字段——点是投影字段，早就存了；做前缺的是**聚合、速度、eta** 这一层派生。）

**判定 [实测]**：**有收益**。做前无任何 `velocity`/`eta_days`/`points_left` 的机器可读输出，只能给「已完成 1/3」；做后给出 `velocity=1.0`、`eta_days=5.0`，且 `points_left == 5.0` 与 `points_total - points_done` 自洽。

**诚实边界 [实测]**：eta 只有在 `days_elapsed>0` 时才有值。同一棵「今天建、今天看」的树，`days_elapsed==0` → `velocity==0` → `eta_days==null`（`metrics.py` 的结构性分支，不是 bug）。**也就是说：对刚建树的当日查询，做后也答不出 eta**，只有在有历史天数的真实项目上才答得出——这正是本项收益的**隐含前提**（见 §4 遗漏面：无长期项目跑数）。

---

### 2.2 迭代 —— 树级 deadline / 轮次边界

**claim**：做前不存在任何「迭代边界」机制；做后树可声明 `deadline`，`trees` 报剩余/逾期，burndown 理想线以 deadline 为终点。

探针（同一命令，两提交各跑一次）：

```bash
git grep -n 'iteration\|velocity\|burndown\|deadline' <commit> -- disflow/
```

**做前（d581d02）原始输出**：

```text
(无输出)
(exit=1)          # git grep：0 命中
```

**做后（HEAD）原始输出**（节选，完整 60+ 行）：

```text
HEAD:disflow/cli.py:480:def cmd_deadline(engine: Engine, args) -> int:
HEAD:disflow/engine.py:133:    def deadline(self, tree: str, value: str | None) -> dict:
HEAD:disflow/metrics.py:40:def parse_deadline(value: str | None) -> str | None:
HEAD:disflow/metrics.py:249:def burndown(tree: dict, events: list[dict], *, slug: str, now_ts: float) -> dict:
HEAD:disflow/spec.py:36:    "tree.init", "tree.goal", "tree.deadline",
HEAD:disflow/state.py:70:def _on_tree_deadline(state: dict, event: dict) -> None:
(exit=0)
```

命令级探针（`trees` 报剩余；做前无 deadline 动词）：

| 探针 | 做前（d581d02） | 做后（HEAD） |
| --- | --- | --- |
| `disflow trees` | `· demo: 1/3（33%） · 就绪 2 · demo：…`（**无 deadline 尾**） | `· demo: 1/3（33%） · 就绪 2 · 剩 10 天 · demo：…` |
| `disflow deadline demo 2026-10-20` | `invalid choice: 'deadline'` (exit=2) | `✓ deadline 已设为 2026-10-20 · 剩 10 天` |

**判定 [实测]**：**有收益**——但**不是**用「iteration」这个词证明的。见下。

**诚实边界 [实测]**：`git grep -n 'iteration' <commit> -- disflow/` 在**做前与做后都 0 命中**——源码里压根没有 `iteration` 这个标识符。真正的收益是 `tree.deadline` 机制（事件 `tree.deadline` + 投影字段 + `parse_deadline` 写时校验 + `trees`/`metrics` 读时报告），不是补了一个叫 iteration 的东西。把它写成「补上了 iteration」属措辞漂移，本验证如实纠正。

---

### 2.3 worktree —— 每节点物理隔离 + 只读对账

**claim**：做前无任何 worktree 命令（隔离只靠租约）；做后每节点确定性路径 + git 命令，`plan`/`list`/`reap` 纯读，只有 `apply` 真跑 git。

探针（同一命令，两提交各跑一次；`DISFLOW_STATE_DIR` 在仓外）：

```bash
python3 -m disflow.cli worktree plan demo
```

**做前（d581d02）原始输出**：

```text
disflow: error: argument command: invalid choice: 'worktree'
(choose from ... close, reap, trees, doctor)
(exit=2)
```

**做后（HEAD）原始输出**：

```text
worktree 计划（根：/tmp/gap3/state/worktrees）
  B  [待建]  /tmp/gap3/state/worktrees/demo/B
        git worktree add -b disflow/demo/B /tmp/gap3/state/worktrees/demo/B HEAD
  C  [待建]  /tmp/gap3/state/worktrees/demo/C
        git worktree add -b disflow/demo/C /tmp/gap3/state/worktrees/demo/C HEAD
只推不跑：要真建请 `disflow worktree apply <tree>`
```

再证明「`apply` 真跑 git（可逆）、`reap` 只给计划（不执行）」：

```text
$ disflow worktree apply demo
✓ 建了 2 个：B C · 重新附着 0 · 已存在跳过 0
$ git worktree list
/private/tmp/gap3/repo                   [main]
/private/tmp/gap3/state/worktrees/demo/B [disflow/demo/B]     # ← 真建出来了
/private/tmp/gap3/state/worktrees/demo/C [disflow/demo/C]

# 弄脏 B 的 worktree，再把 B 收成终态 → 出现 leak
$ printf 'uncommitted work\n' > .../worktrees/demo/B/dirty.txt
$ disflow skip demo B --reason "probe leak"
$ disflow worktree list demo
worktree 对账（树 demo）
  leak      B: 节点已 skipped 但 worktree 还在（**有未提交改动**）
$ disflow worktree reap demo
可回收 1 项（**只推不跑**：引擎不执行删除，下面命令由人跑）
  leak     B  ⚠ 跑了会丢东西
           理由：dirty:未提交的已跟踪改动
           ⚠ 未提交的已跟踪改动会丢失（git 会拒绝，需 `--force` 才能删）
           删工作区：git worktree remove /tmp/gap3/state/worktrees/demo/B
           删分支：  git -C /private/tmp/gap3/repo branch -d disflow/demo/B
$ ls -d .../worktrees/demo/B     # reap 之后目录仍在 → 证明确实没执行删除
/private/tmp/gap3/state/worktrees/demo/B
```

**判定 [实测]**：**有收益**（能力面 + 只读对账）。做前 `worktree` 命令根本不存在；做后给出确定性路径、真跑 `apply` 建出 worktree、`reap` 对危险条目 `safe_to_drop=False` 且只打印命令、执行前后目录仍在。

**诚实边界 [实测]**：以上只证明「能力存在且分界正确」，**没有**证明「它在真实并行开发里真的挡住了一次写面互踩」。要证后者需要两个真实并发 worker 跑在同一个长期项目上——本会话没有（见 §4）。

---

## 3. 未见收益 / 未测出差值（如实）

- **「iteration 这个词/概念被补上」：未见收益。** `git grep -n 'iteration' d581d02 -- disflow/` 与 `git grep -n 'iteration' HEAD -- disflow/` **都 0 命中**。收益体现在 `deadline`，与「iteration」命名无关。
- **「做前算不出点数」：未见收益（半真）。** 做前 `status --json` 的**逐节点**已带 `points` 字段（`"points": 3.0`）。做前缺的只是**聚合/速度/eta**。把收益说成「做前连点都看不到」是夸大。
- **「deadline 是闸门」：未见收益（且做后也不是）。** 逾期不阻塞任何写动词；`deadline` 只在 `trees`/`metrics` 里被**报告**。这是设计（迭代边界不是门），但因此**不能**把「有 deadline」宣称为「按时交付被强制」。
- **「worktree 挡过真实并发互踩」：未测出差值。** 没有任何跑数证明它减少了一次实际冲突（见 §4）。
- **「量化让 owner 的决策变好」：无法机检 [判断]。** 这属「值不值得」的语义层，脚本判不了；本文只证「读数存在且自洽」，不证「读数改变了决策」。

---

## 4. 遗漏面（哪些没评、为什么）

| # | 没评的面 | 为什么 / 现状 |
| --- | --- | --- |
| G1 | `docs/requirements.md` §3–§7 的 **29 条 FR + 7 条 NFR 未逐条机检** | 本次只机检了 §1.2 病根 8 条 + §1.3 保留项（原 5 条中 2 条缺失）+ §2 目标与非目标。三条需求的 `source` 字段也**只引用** §1.3 / `design.md` §10——没有一条机检落到 §3–§7。**证据（可复核）**：`/Users/song/.fat/kaa/events.jsonl` 里 R-METRICS/R-ITER/R-WORKTREE 的 `source` 字段逐条列出，无 §3–§7 引用。[实测] |
| G2 | **运行期实证无** | 全部证据是测试级（`unittest`，386 例）。没有**真实长期项目**的跑数：没有「连续 N 天真实的 points/velocity 序列」，没有「两个真实并发 worker 在同一长期仓上跑」。因此 §2 的收益都是**能力面**（命令存在、输出自洽），不是**效果面**（真的省了时间/真的少了一次冲突）。[判断] |
| G3 | **mgt 迁移映射的正确性无真实输入验证** | `import_mgt.py` 的映射从没有拿**真实 `.fat/mgt/` 状态**作对照。`tests/test_import_mgt.py` 用的是测试自己 `open(...).write()` 出来的**合成** `state.json`/`registry.json`（见该文件第 19/22 行），不是 mgt 真产出。**证据**：`grep -n "state.json\|registry.json" tests/test_import_mgt.py`。[实测] |
| G4 | **「不追求通用工作流引擎」属语义判断** | `requirements.md` §2 非目标。没有任何命令能判定「通用性够不够」；只能人工评审。[判断] |
| G5 | **kaa 侧 `satisfies` 漏写后无合法修复路径** | 见 §5 详述。已实测：重跑 `done` 被拦。 |
| G6 | **三项工作的 `verify_cmd` 在 `done` 时是否真跑过，无机器记录** | 三个 `node-done` 事件**不含** `verify_result` 字段（键集：`args/cmd/declaration_values/declarations/evidence_meta/exit/kind/layer/node/not_applicable/satisfies/slug/target_status/ts`）。故无法从事件流判定它们 `done` 当时是否跑过 `verify_cmd`。本验证节点**现在**把三条都跑了一遍（见 §5 表「本节点复跑」列）。[实测] |
| G7 | **burndown 理想线的「正确终点」无外部对照** | 理想线以 deadline 为终点、起点为 `tree.init`，是设计选择；没有第二份独立实现对照它是否「对」。只证了自洽（`series[-1].remaining == summary.points_left`），未证「这条线对项目有解释力」。[判断] |

---

## 5. 需求认领映射

三条需求由**三个交付节点**实现；但它们在 `done` 时**都没有声明 `satisfies`**（三条 `node-done` 事件的 `satisfies` 值均为 `""`）。

| 需求 | 交付节点 | 节点标题 | 节点 `verify_cmd` | 本节点复跑结果 |
| --- | --- | --- | --- | --- |
| **R-METRICS** 量化 | `968edaf1267e` | 量化：points 声明字段 + metrics.py 派生 velocity/burndown | `python3 -m unittest discover -s tests -t tests -p test_metrics.py -q` | `Ran 37 tests … OK`（exit 0） |
| **R-ITER** 迭代 | `0a1f5cbbfe3e` | 迭代：树级 deadline 声明 + 剩余/逾期读数 + burndown 理想线 | `python3 -m unittest discover -s tests -t tests -p test_deadline.py -q` | `Ran 9 tests … OK`（exit 0） |
| **R-WORKTREE** worktree | `5cfaae85fcf5` | worktree：每节点物理隔离（plan/list/reap）+ 泄漏核对 | `python3 -m unittest discover -s tests -t tests -p test_worktree.py -q` | `Ran 39 tests … OK`（exit 0） |

需求的 `accept`（来自 kaa 台账 `requirement.add` 事件）与本验证的对应：

- **R-METRICS** accept：`metrics <tree> --json` 给出 `points_total/points_done/points_left/velocity/eta_days`；对含 points 的树 `eta_days` 与 `points_left/velocity` 自洽；未估节点不污染分母。→ §2.1 对 `metrics --json` 实跑，`points_left=5.0`、`points_total-points_done=8-3=5`、`eta=points_left/velocity=5/1=5` 自洽；未估节点（C）计入 `points_unestimated=1`，不进分母。[实测]
- **R-ITER** accept：树可声明 deadline（非法日期拒收）；`trees` 报剩余/逾期；burndown 理想线以 deadline 为终点；无 deadline 报 `not_applicable` 而非静默。→ §2.2 实跑 `deadline`/`trees`；理想线由 `test_deadline.py`/`test_metrics.py` 覆盖。[实测]
- **R-WORKTREE** accept：`worktree plan` 给出确定性路径与命令且零副作用；`list` 核对现实（存在/已注册/脏/终结=泄漏）；`reap` 只清终结节点；`doctor` 报泄漏。→ §2.3 实跑 `plan`/`list`/`reap`/`apply`。[实测]

### 5.1 为什么 `satisfies` 只能由本验证节点认领（限制下的次优选择，不是等价替换）

**如实写明**：三个交付节点在 `done` 时 `satisfies=""`，于是这三条需求当时**没有任何节点认领**。而 lib 的结构使得「事后补声明」这条修复路径**被堵死**：

- `satisfies` 的**唯一写入点**是 `node-done` 事件（`engine/runtime.py#cmd_done` 把值并进 `node-done` 的 `extra`；`kaa.py#_declared_satisfies` 从 `node-done` 事件重算——投影不存声明）。[实测]
- 重跑 `done` 以补写一条新 `node-done`：lib `TreeRuntime.done` 在跨进程锁内有一道**终态守卫**——节点当前 status 已是终态即拒：`✗ <node> 已是终态（另一个会话刚推进）：本次未落盘`。[实测，见下方复现]
- 注意口径纠偏：拦截**不**来自 FSM 表。`[fsm.status_gate.done]` 的 `done` 行 token 其实是 **`idle`**（`fsm.validate(op=done, status=done)` 返回 `(True, '幂等…')`）。真正拒绝的是 `flowcore/tree.py` 里那道显式终态守卫（`if self._node(...).get("status") in spec["fsm"]["terminal"]: return self._fail(...)`）。**结论相同（补不了 satisfies），但机制描述必须写准**，否则下一个维护者会去改 FSM 表、改了个不相关的地方。[实测]

复现（lib 级，最小 spec；`/Users/song/1github/fat3`）：

```text
done#1 rc = 0           # 写入一条 node-done（satisfies=""）
done#2 rc = 1           # ✗ 已是终态（另一个会话刚推进）：本次未落盘
node-done events: [(<node>, '')]     # 只有一条 → satisfies 无法事后追加
```

**因此**：`satisfies` 由**本验证节点**（`70bfdd06a03e`，mode `evaluate`）在 `done` 时认领：`R-METRICS, R-ITER, R-WORKTREE`。**这不是等价替换**——交付节点的 `done` 与验证节点的 `done` 是两个不同的判决事件，验证节点携带的是**独立复核**后的认领，而非原交付节点当时的自我声明。这是**当前 lib FSM 限制下的次优选择**：因为「重跑 `done` 补声明」被终态守卫堵死，且 lib 的 `terminal=[done,failed,skipped]` 没有留下「重新打开终态节点」的合法动词（`retry` 只对 failed/skipped 生效，对 done 是 reject）。[实测]

---

## 6. 负向对照（证明测试有牙齿）

纪律：**绿不是证据，能红才是**。以下三处「改坏」都在 `/tmp/gap3/mut`（`git archive HEAD` 的**抛弃副本**）里做，改完即弃——**本仓 `disflow-eval` 全程零改动**。每处都是「改一处实现 → 对应测试必须变红」。

**① 量化：把「未估当 0 点」**（`disflow/metrics.py#_estimated` 改成 `float(node.get("points") or 0.0)`）：

```text
FAIL: test_unestimated_nodes_do_not_enter_the_sum (test_metrics.SummaryTest...)
  self.assertEqual(result["points_unestimated"], 1)
AssertionError: 0 != 1
----------------------------------------------------------------------
Ran 37 tests in 0.083s
FAILED (failures=1)          # exit=1
```

**② 迭代：让 `parse_deadline` 不做校验**（函数体开头 `return value`）：

```text
FAIL: test_surrounding_whitespace_is_stripped
  self.assertEqual(parse_deadline(" 2025-01-31 "), "2025-01-31")
AssertionError: ' 2025-01-31 ' != '2025-01-31'
----------------------------------------------------------------------
Ran 9 tests ... FAILED (failures=4, errors=1)          # exit=1
```

**③ worktree：把 `safe_to_drop` 改成恒 True**（`disflow/worktree.py#_plan_entry` → `safe = True`，fail-open）：

```text
FAIL: test_residual_branch_passes_same_guard (test_worktree.WorktreeTest...)
  self.assertFalse(entry["safe_to_drop"])
AssertionError: True is not false
----------------------------------------------------------------------
Ran 39 tests ... FAILED (failures=14)          # exit=1
```

三处都：**改坏 → 变红 →（抛弃副本丢弃=还原）**。对应的绿状态见 §5 表格（同一批测试在做后 HEAD 上全 OK）。

---

## 7. 方法论声明

本节点 mode = `C/evaluate`。`kaa methodology list` 的 `C/evaluate` 组真名 3 条：`conclusion-hygiene`、`conclusion-robustness`、`ach`；`C/verify` 组另有 `verify`、`evidence-law`、`triangulation-check`、`gap-fact-check`。

**实际采用：`gap-fact-check`（C/verify）**——「承重缺口/存在性判断（『有 X / 缺 X』）是可机器核验的事实声明，不让 LLM 自证」，其形状就是本文的骨架：每条 claim = 一条 `present`/`absent` 断言（命令），并强制**负向验证**（注入一条必然 FAIL 的 probe 确认脚本 exit 1 再弃）。本文 §2 的「同一探针两提交对照」是它的 `present/absent` 双向防漂移，§6 的三处变红是它要求的负向验证。辅以 **`conclusion-robustness`（C/evaluate）** 的「结论要与事实比对、事实可查、不靠自信」作为总框。`ach`（枚举+对比型）不合用：本任务没有多个竞争假说要先问「什么证据推翻它」，是单向的「声称 vs 事实」核验。

---

## 8. 复现附录

### 8.1 建演示树（做前/做后共用同一 state dir）

```bash
rm -rf /tmp/gap3 && mkdir -p /tmp/gap3/before /tmp/gap3/repo /tmp/gap3/state
cd /Users/song/1github/disflow-eval
git archive d581d02 | tar -x -C /tmp/gap3/before      # 做前代码
git archive HEAD    | tar -x -C /tmp/gap3/mut         # 负向对照抛弃副本

cd /tmp/gap3/repo && git init -q .
printf 'Hello\n' > greeting.txt && git add -A && git -c user.name=t -c user.email=t@t commit -qm init
cat > /tmp/gap3/children.json <<'JSON'
[ {"id":"A","goal":"改 greeting.txt","points":3,"touch_plan":["greeting.txt"]},
  {"id":"B","goal":"补一条测试","points":5,"touch_plan":["test_greeting.py"],"requires":["A"]},
  {"id":"C","goal":"写文档","touch_plan":["README.md"]} ]
JSON
export DISFLOW_STATE_DIR=/tmp/gap3/state               # ★ 仓外（worktree 会拒绝仓内）
export PYTHONPATH=/Users/song/1github/disflow-eval
d() { python3 -m disflow.cli "$@"; }
d init demo --goal "demo：量化/迭代/worktree 探针"
d expand demo root --children "$(cat /tmp/gap3/children.json)"
d deadline demo "$(python3 -c 'import datetime;print((datetime.date.today()+datetime.timedelta(days=10)).isoformat())')"
d skip demo A --reason "demo probe"
# 事件回拨 3 天，让 days_elapsed>0（eta 才是实数）
python3 - <<'PY'
import json, datetime, pathlib
p = pathlib.Path("/tmp/gap3/state/events.jsonl")
out=[]
for line in p.read_text().splitlines():
    if not line.strip(): continue
    e=json.loads(line); e["ts"]=(datetime.datetime.fromisoformat(e["ts"])-datetime.timedelta(days=3)).isoformat()
    out.append(json.dumps(e, ensure_ascii=False))
p.write_text("\n".join(out)+"\n")
PY
d rebuild
```

### 8.2 跑探针

```bash
# 做后
PYTHONPATH=/Users/song/1github/disflow-eval python3 -m disflow.cli metrics demo --json
PYTHONPATH=/Users/song/1github/disflow-eval python3 -m disflow.cli trees
PYTHONPATH=/Users/song/1github/disflow-eval python3 -m disflow.cli worktree plan demo
# 做前（同一 state dir）
PYTHONPATH=/tmp/gap3/before python3 -m disflow.cli status demo --json
PYTHONPATH=/tmp/gap3/before python3 -m disflow.cli metrics demo --json      # invalid choice
PYTHONPATH=/tmp/gap3/before python3 -m disflow.cli worktree plan demo       # invalid choice
# git grep 对照
git grep -n 'iteration\|velocity\|burndown\|deadline' d581d02 -- disflow/   # 0 命中, exit 1
git grep -n 'iteration\|velocity\|burndown\|deadline' HEAD    -- disflow/   # 60+ 命中, exit 0
```

### 8.3 负向对照（抛弃副本内）

```bash
# 例：metrics。改 disflow/metrics.py#_estimated 的推导式后：
cd /tmp/gap3/mut && python3 -m unittest discover -s tests -t tests -p test_metrics.py -q
# → Ran 37 tests … FAILED (failures=1)   （还原 = 重跑 8.1 的 git archive 覆盖）
```

---

## 9. 验收自检（本节点收尾）

- 全量测试（本仓 `disflow-eval`）：`python3 -m unittest discover -s tests -t tests -q` → 见下方 §9.1。
- 类型检查：本节点只新增 Markdown 文档，**未改任何 `.py`**。对 `.md` 跑 pyright 无意义——pyright 会把 Markdown 当 Python 解析、报 828 条假错（见 §9.1 原始输出）；有意义的读数是 `pyright disflow` → **0 errors**。
- `git status --porcelain`：见 §9.1（唯一新增 = 本文件）。

### 9.1 收尾实测输出

```text
$ python3 -m unittest discover -s tests -t tests -q
----------------------------------------------------------------------
Ran 386 tests in 27.064s

OK
(exit=0)

$ ~/.pi-lens/tools/node_modules/.bin/pyright docs/gap3-evaluation.md   # ← 无意义：pyright 按 Python 解析 .md
... - error: Expressions surrounded by backticks are not supported in Python 3.x ...
828 errors, 223 warnings, 0 informations            # 假错，Markdown 不是 Python

$ ~/.pi-lens/tools/node_modules/.bin/pyright disflow                 # ← 有意义的读数
0 errors, 0 warnings, 0 informations

$ git status --porcelain
?? docs/gap3-evaluation.md

$ ps -eo command | grep [m]utation_check
（唯一命中：disflow-pins 的变异跑，属另一份克隆，不触及本仓）
```
