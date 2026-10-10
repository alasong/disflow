---
name: disflow
description: 用任务树 + 依赖图编排多节点长任务——把目标拆成节点树，drive 算就绪波次，每个叶子派一个 fresh-context subagent，done 时跑验收门。当任务有多个互相依赖的步骤、需要验收证据、或要跨多轮推进时用（单步小改不要用）。
---

# disflow —— 你是**宿主**，它是「只推不跑」的分发器

**disflow 自己不干活、也不 spawn 任何东西。** 它算两件事：谁该动（`drive`）、怎么算完成（门 + 证据）。
**派活和干活都是你的事**——在 pi 里，派活就是用 subagent。

先把这条记住，否则你会等一个永远不动的引擎：

```
disflow 算出 ready_wave  →  你 spawn 一个 subagent 干那个节点  →  collect  →  done  →  再 drive
        ↑ 引擎只给命令                            ↑ 只有这一步是「跑」
```

## 0 环境（一次）

```bash
export PYTHONPATH=<本仓的绝对路径>          # 本仓根
export DISFLOW_STATE_DIR=<仓外的目录>        # ⚠ 想用 worktree 隔离时必须放仓外，见 §6
D="python3 -m disflow.cli"
```

零依赖、纯标准库。状态是 `events.jsonl`（唯一真相）+ `state.json`（可重放的投影）。

## 1 建树

```bash
$D init <tree> --goal "这个树要达成什么"
$D expand <tree> root --children '[
  {"id":"a","goal":"写 greeting.txt 内容 Hi","points":2,
   "acceptance":{"cmd":"cat greeting.txt","expect":"contains:Hi"},
   "touch_plan":["greeting.txt"]},
  {"id":"b","goal":"依赖 a 才做","requires":["a"],"acceptance":{"cmd":"true"}}]'
```

**可声明的字段**（**没有 `title`——人类可读的标签就是 `goal`**）：
`id` `goal` `kind` `stage` `role` `entry` `context` `requires` `relates` `awaits` `produces` `consumes` `touch_plan` `points` `acceptance`

四条最容易搞错的：

| 字段 | 含义 | 搞错的后果 |
| --- | --- | --- |
| `goal` | 干什么（不是标题，是**给 worker 的任务描述**） | 写 `title` → 报错列合法字段 |
| `acceptance` | 怎么算完成：`{"cmd":…,"expect":"exit0\|contains:…\|regex:…"}` | 少了它 `close` 的覆盖门会拒 |
| `touch_plan` | 写面声明：这个节点会改哪些路径 | 声明了却没改 → 门报「空洞通过」 |
| `requires` | **调度边**（必须 DAG，写时拒环） | 与 `relates`（纯引用，不参与就绪）混用 |

`requires` 是唯一的调度边；`relates` 是信息边；跨树的依赖用 `awaits: ["<别的树>"]`（等那棵树 `closed`）。

## 2 宿主循环（每轮七步）

```bash
$D drive <tree> --json        # ① 读 ready_wave / blocked / waves / stuck —— 该派谁
$D brief <tree> <node>        # ② 这就是要交给 worker 的**全部**输入
$D start <tree> <node> --holder <你>   # ③ 领租约 + **拍开工基线**（必须在 worker 动手前）
#  ④ 派：subagents_enable 后，用 subagent 工具起一个 worker，
#     task = 上面那份 brief 的 JSON（**原样给**，别自己改写或补背景）
#  ⑤ worker 干活：只改 touch_plan 里声明的路径，**不 commit**（宿主统一提交，见 §3）
$D collect <tree> <node> --result '{"status":"ok","summary":"…","changed_files":["greeting.txt"]}'
$D done <tree> <node>         # ⑥ 引擎自算证据 + 跑门；拒收会逐条列缺什么
#  ⑦ 回到 ①
```

**为什么 ④ 必须用 fresh context**：brief 里已经有 goal + 祖先链注入的 `context` + 前置产出摘要。
worker **不需要**知道整棵树——这是整棵树的上下文继承在替他做减法。把当前会话的历史塞给它，
等于把 disflow 唯一的收益（每任务干净上下文）扔掉。

`drive --json` 里：
- `ready_wave` = 现在可以并发的节点（**同波写面互不重叠**，并避开**正在运行**节点的写面；
  被挡下的候选带理由进 `deferred`——都是引擎算的，别自己加，见 §10.2）
- `blocked` = 谁在等谁 · `stuck` = 没就绪也没在跑但还有 pending ⇒ 真卡住了
- 注意 `failed` **不算 stuck**（它可见、可 `retry`）

## 3 worker 的契约（`brief` 就长这样）

```json
{ "id": "a", "goal": "…", "context": {}, "from_requires": [],
  "acceptance": {"cmd":"cat greeting.txt","expect":"contains:Hi"},
  "touch_plan": ["greeting.txt"],
  "question_policy": {"ask_host": […], "decide_alone": […], "how": "…"},
  "result_contract": {"required":["status","summary","changed_files"],
                      "status_in":["ok","failed","blocked"], "question_channel":"…"},
  "brief_cmd": "disflow brief d a",
  "done_cmd":  "disflow done d a --decl '{\"evidence\": …}'" }
```

四条要点：

1. **`collect --result` 是 JSON，三个必填字段**：`status` `summary` `changed_files`。
   `status` 只能是 `ok` / `failed` / **`blocked`**。
2. **`blocked` 是「我要问」的合法出口**：worker 卡住时把问题写进 `summary`、`status` 置 `blocked`**然后停**——
   **不要自己拍板改范围**（范围是宿主定的）。宿主读到 `blocked` 后再决定（`rescope` / `unrequire` / 改 goal）。
   `blocked` 不是完成：`collect` **不写 done**。
3. **`done_cmd` 在 brief 里**——worker 收尾时照抄，不要自己拼。
4. **worker 不许自己 `git commit`（原地派发）——宿主统一提交；worktree 场景例外，可以 commit。**
   worker 只管把文件改到工作区（未提交）。分工的**判据**：

   | 派发方式 | commit 落在哪 | 可以自 commit 吗 |
   | --- | --- | --- |
   | 原地派发（`worktree: null`，仓就是宿主的工作区） | 宿主**当前分支** | ✗ 不许（宿主统一提交） |
   | worktree（`worktree apply` 之后） | 隔离分支 `disflow/<tree>/<node>` | ✓ 可以（宿主按分支合并） |

   ⚠ **旧机制解释已作废（n6 修了，别再照旧讲）**：以前写的是「无 worktree 时 `base_sha=None`
   ⇒ 门只读 `git status` ⇒ 提交后归因不到改动、规模门空转」。**现在不是这样**：`start`
   **一律**记 `head_sha`（不再只对 worktree 记），`done` 把它当 `base_sha` 并上
   `git diff --name-only <sha>`，规模读数也复用同一个 `base_sha`。
   当场读数（判据：`tests/test_worktree_gate.py` 的 `test_a5_inplace_committed_change_is_seen`
   等）：原地派发 + 自 commit ⇒ `introduced=['README.md']`、`vacuous={}`；worktree + 自 commit
   ⇒ 同上；改而不提交 ⇒ 同样看得见。**别再用「提交了门就瞎了」当理由。**

   那为什么原地派发仍不许自 commit？不是因为门，而是因为**原地派发就是宿主的当前分支**：
   自 commit 会把改动直接写进宿主的工作历史、绕过「宿主统一提交」，而 worktree 的提交从设计上
   就是落在隔离分支上给合并用的。

## 4 收尾

```bash
$D close <tree>        # 跑两道树级门：有 done 的收益验证 + 经验沉淀节点
                       # 不满足时的逃生口：root.context.closure = "not_applicable: <具体理由>"
$D reopen <tree> --reason "…"   # 关了又想接着干：**显式**重开（会落事件、记理由）
$D doctor              # 自检：投影漂移 / 成环 / 悬空引用 / 租约超时
```

**已关闭的树不能挂任务**（会被拒，并提示你 `reopen`）——因为 `closed` 是跨树 `awaits` 的完成信号，
在它之后挂任务会让**别的树基于一个假信号开工**。

## 5 退出码与陷阱

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 数据问题（树/节点不存在、结果契约不符、就绪判定不满足） |
| 2 | **门拒收**（`done` 的验收/写面/规模门、`close` 的树级门）—— 也**是 argparse 用法错误码**，别靠它区分 |

- **「门空洞通过」是警告不是拒收**：`done` 打印 `⚠️ 门空洞通过（无东西可验）` 时**它是过了**。
  所以「没改任何东西就 done」**不会**被拦——它会 exit 0 并警告。门拦的是**验收不匹配**。
  看到这个警告就别宣称「门都验了」。
- **`done` 前必须真改 `touch_plan` 里的文件**，否则 `introduced` 为空、规模门空转。
- **每轮先 `start`，否则就没有开工基线**：`start` 既领租约也拍基线（开工那一刻的脏文件 → 内容指纹），
  而 `collect`+`done` **不 start 也能跑**——门于是在**空基线**上跑：开工前已脏的文件会被算成
  本节点引入的改动（实测：先留一个未跟踪的 `old_dirty.txt` ⇒ `done` exit 2，理由
  「改动超出 touch_plan：['old_dirty.txt']」）；真没改东西时 `introduced=[]` ⇒ `⚠️ 门空洞通过`、exit 0。
  所以每轮派活前都先 `start`。
- **`--dry-run`**（`start`/`done`/`close`/`drive`）：门照跑、不落事件。想在派活前确认「能不能过」就用它。
- `--json` 只在 `drive` / `status` / `metrics` / `worktree` 上有（`trees` 没有）。
- `--help` 里**每个**选项都有说明（实测 **0 个无说明**；判据：当场数 `argparse` 各选项的
  `help` 为空者）。**但散文里的纪律以本文件为准**（`--result` 要 JSON 这类形状说明已在 help 里）。

## 6 可选：worktree 物理隔离

多个 worker 真并发改同一仓时，每个节点一个独立 git 工作区：

```bash
$D worktree plan <tree>            # 只给计划，零副作用
$D worktree apply <tree>           # 真建（可逆、幂等）
$D worktree list <tree>            # 对账：leak / missing / orphan / residual
$D worktree reap <tree>            # **只打印命令，永不执行**
```

**分界是「可逆 vs 不可逆」**：`apply`（创建）真跑；`reap`（删除）只给判据 + 宿主该跑的命令，
因为删除会连带丢掉未提交改动 / ignored 文件 / 未合并提交。**顺序要紧：先删工作区、再删分支**（分支一律 `-d`）。

⚠ `worktree` 要求 **state dir 在仓外**，否则守卫按设计拒收（worktree 路径会落进仓里）。

## 7 派活时给 worker 配能力（`role` **不带能力**）

**先看清一件事**：`role` 只是个**标签**（dev/qa/pm），`brief` 里**没有任何**能力字段。
它不告诉 worker 任何东西，也**不约束你把活派给谁**。所以「派给 dev」之后，你还得自己决定：
**用什么 agent、给什么 skill、给什么工具上限**。

### pi 里子代理默认拿得到什么（读源码 + CHANGELOG，不是推断）

| 层 | 子代理默认 | 谁在管 |
| --- | --- | --- |
| **项目层指令文件**（worker 自己 cwd 里的 `AGENTS.md`/`CLAUDE.md`） | **继承 ✓** | 所有内置 agent 都**显式**写了 `inheritProjectContext: true` |
| **全局层 context**（`~/.pi/agent/AGENTS.md`：常驻卡、输出纪律、证据纪律） | **剥掉 ✗** | `inheritGlobalContext ?? false`（无内置声明）· CHANGELOG 原话「omit the operator's global context file by default」 |
| **skills** | **剥掉 ✗** | 所有内置 agent 都写了 `inheritSkills: false` |

判据（源码）：`isGlobalContextFile(f) ⇔ dirname(f) == getAgentDir()`。
所以「全局」的定义是**文件在 agent 目录里**，与「项目」无关。

⚠ **一个很实际的后果**：worker **默认拿不到你的纪律**（进度块格式、证据纪律、方法论真名…）——
它们住在 `~/.pi/agent/AGENTS.md`，属于被剥掉的全局层。所以：

- 只靠默认，就得在**每一条 task 里手写纪律**——而手写会漂移，早晚与全局层不一致。
- 正确做法是**显式决定**：要么 `inheritGlobalContext: true`（子代理自己带纪律），
  要么接受「task 自带全部约定」并把它当纪律固定下来。
- **别以为「项目层继承」就等于「它有纪律了」**：disflow 仓**没有**项目级 `AGENTS.md`，
  所以派给 worker 的项目层实际上是**空的**。

**结论**：方法论与纪律**默认不在**，领域 skill **默认也不在**。所以「执行能力弱」不是错觉，
而是默认值如此——**要补就得在派活时补**。

### 三个口子（任选）

```js
// ① 最常用：派活时显式给它领域 skill
subagent({ agent: "worker", skill: ["python-expert"], task: <brief JSON> })

// ② 自定义 agent 定义：把继承打开
//    inheritGlobalContext: true（← 最该考虑的是这个：给的是**纪律**，不是 skill）
//    inheritSkills: true / inheritProjectContext: true

// ③ 用 delegate agent：它**默认**就继承项目层（唯一一个）
```

**先想清楚你缺的是能力还是纪律**：worker 老因为「没给证据」「乱写完成」被打回，
那缺的是**纪律**（→ `inheritGlobalContext` 或把纪律写进 task）；
老因为「不会用这库」卡住，那缺的才是**能力**（→ 给 skill）。

### 一个建议映射（**建议，不是机制**）

| `role` | 建议给什么 | 为什么 |
| --- | --- | --- |
| `dev` | 领域 skill（如 `python-expert`）+ 写工具 | 它要改文件 |
| `qa` | 审查类判断纪律（`claim-tiering` 那类）+ **只读工具** | 给写工具就会让它「顺手修一下」——那就不是 QA 了 |
| `pm` | 只读 + 只能动声明（goal / points / acceptance） | 它的产出是声明，不是代码 |

### 两条硬纪律

1. **别把 `disflow` skill 派给 worker。** 它是**宿主手册**——教的是「你是宿主，去建树去派活」。
   给了 worker，它就会想自己建树、自己派活，而它只是个干一个叶子的执行体。
2. **`blocked` 有两种，宿主得分开读**：
   - 「范围不够」→ `rescope`（改声明）
   - 「**我要的能力没有**」→ 换执行体 / 加 skill（**别去改声明——改声明治不了能力不足**）

### 为什么这层必须在宿主，不能进 `brief`

disflow 是**语言中立**的（NFR-2：核心层零业务词），而「给哪个 skill / 什么模型 / 什么工具上限」
全是 harness 特有的。塞进 `brief` 就把它绑死到 pi——那就从「分发器」退化成「pi 的插件」。

### 可自己核实的一条

每个子代理的产物里都有 meta：`*_worker_meta.json` 里的 `skills` 字段。
派完读一下就知道到底给到没给到——**不要猜**。

## 8 别做的事

- **别自己算波次**：`drive` 已经算好了（含写面不重叠的分波）。手算等于绕开唯一的并发安全机制。
- **别在同一个节点上并发派两个 worker**：租约会在 `start` 挡住第二个（`exit 1`），
  但那是**白派一次**——`drive` 不持锁、不认身份，两个宿主会拿到同一个 `ready_wave`。
- **别用 `skip` 掩盖「不知道该怎么办」**：`skip` 要写理由，而理由是给人看的。
  真不知道就 `blocked` 报上来。
- **别改状态文件**：`state.json` 是投影，会被 `rebuild` 覆盖。要改就走动词（事件才是真相）。

## 9 两个视图，别混（一个知道「是什么活」，一个知道「跑到哪了」）

| 视图 | 命令 | 数据源 | 它答的问题 |
| --- | --- | --- | --- |
| **看板（阶段列）** | `$D board <tree> [--stage S] [--all] [--all-columns] [--json]` | disflow 自己的事件流（**纯 disflow 事实**） | 谁完成 / 谁在跑 / 谁就绪 / 谁被挡；**列** = 需求→设计→开发→验证（列定义来自配置；**默认只列「运行中 + 待完成」、空列不铺版面**——事实在表头两行报出：`══ 看板 <tree> ══ <树目标>` / `列 req 0/0 · …` / `状态 …`；`--all` 展开终态、`--all-columns` 铺空列；**`--json` 恒为全量事实**（全列 × 全状态））；**列内**按树层级缩进（容器 = 结构行 `◦`，任务按 `parent`）+ 按状态分组（运行中/可派/待前置/被在跑挡住/完成/跳过/失败）；`--json` 带 `parent`/`depth`；人读态终态行（`✓ 完成`/`— 跳过`）**不打 goal**，其余行 goal 截断到 80 字（`views.truncate_goal` 唯一实现），**完整 goal 只在 `--json`**。布局只有 `views.board_layout` 一处实现 |
| **运行视图** | `.agents/skills/disflow/lanes_watch.sh` | disflow 投影（**骨架**）+ **宿主（pi）的 run 目录**（实时事实，**join**） | **按阶段列（需求/设计/开发/验证）分别列出「运行中 + 待完成」的条目**（`--all` 连终态；树只作列内父子缩进 + 行尾来源标注），按**与看板同源的布局**（阶段列 + 列内树层级 + 状态分组）列出；在**在跑**节点上就地显示实时进展（`◈` 目标 / `⌘` 最后命令 / `↳` worker 自报进展）；派发批次降为**次级注解区**（排障用）。可用性（2026-10-11 用户当场提）：终态行（`✓ 完成`/`— 跳过`）**不打 goal**（完整文本在 `board --json`）；默认刷新是**追加式**（每轮一整块、带时间戳头、**保留回滚**），备用屏必须显式 `--screen`（不闪，代价是**无回滚**）；**非 TTY（管道/重定向）零控制序列**；**默认只列「运行中 + 待完成」**（`--full` 显示全部状态，含 `✓/—/✗`）；**派发批次次级区默认不显示**（`-v` 才展开）；无在跑节点时**显式打一句**（进展只对在跑节点显示是设计） |

⚠ **命名只认上面两个**：`done` **不是看板的列**——它是节点**状态**（「收尾没有」），
与阶段（「流程走到哪」）是两个轴；一个 pi workflow run（= 宿主一次 subagent 调用，可含 1..N 路
lane）是**运行视图**里的一个**派发批次**，**不许**把它叫「看板」/「工作流」（那两个词专指 disflow 的阶段列）。

```bash
sh .agents/skills/disflow/lanes_watch.sh once          # 快照（非 TTY 重定向也不吐控制序列）
sh .agents/skills/disflow/lanes_watch.sh               # 常驻 5s：**追加式**（保留回滚、可滞轮）
sh .agents/skills/disflow/lanes_watch.sh --screen      # 常驻 5s：备用屏（不闪，**代价：无回滚**）
sh .agents/skills/disflow/lanes_watch.sh once        # 一轮快照：按阶段列分别列运行中 + 待完成
sh .agents/skills/disflow/lanes_watch.sh once --all  # 连 ✓ 完成 / — 跳过 也列
sh .agents/skills/disflow/lanes_watch.sh once -v     # 在跑 lane 多给命令 + 自报进展
sh .agents/skills/disflow/lanes_watch.sh all           # 含已过窗口的历史 lane
```

> **为何默认不再用备用屏**：备用屏（`\033[?1049h`）**没有回滚缓冲**，滞轮/scrollback 因此失效。
> 默认改追加式后滞轮可用；代价是不再「就地重绘」（屏幕会向下长）。要得不闪就显式 `--screen` 并接受无回滚。

**为什么是两个**：disflow **不 spawn**，所以它不知道「worker 在跑哪条命令」（`board` 只看事件流）；
反过来 pi 不知道树的结构。两边各答一半 ⇒ **数据源与刷新节拍不同**（`board` 是一次性快照，运行视图
是 5s 刷新）。**这才是区分两个视图的依据——不是布局**：布局是**同一个**（都走 `views.board_layout` /
`board_lines`），否则同一棵树会长出两套缩进（本仓已知的「两面分叉」病）。

运行视图**把两半接起来**（v7；v8 改默认刷新节拍）：外层结构 = `$DISFLOW_STATE_DIR/state.json` 的**全部节点**（树骨架），
布局**逐行复用 `board_lines` 的成品行**（自己不再写一套分列/缩进）；再把 lane 名（= 派活时给的 `key`，
即节点 id）join 到对应节点行上，就地标注「哪条 lane / 哪个批次 / run 状态 / 已跑 / 静默 / 消息数」。
**带实时进展的是运行视图**（`◈`/`⌘`/`↳`，且只给**在跑**节点）；**读不到就明说读不到**（`state.json`
缺失 → 显式报「读不到」；lane 名在投影里找不到 → 点名，绝不猜一个 goal 顶上）。
v8 只改刷新节拍（数据源与布局不变）：默认**追加式**（不用备用屏 ⇒ 保留回滚），`--screen` 才显式
切备用屏；**非 TTY 零控制序列**；**默认只看运行中 + 待完成**（`--full` 看全部状态）；批次注解区默认隐藏（`-v` 展开）；无在跑节点时显式打一句。

⚠ 它读的是 pi 的**内部布局**（`status.json` / `session.jsonl`）——所以它属于**宿主侧工具**，
住在宿主手册旁边，**不进 `disflow/` 核心**（那会把引擎绑死在 pi 上，破 NFR-2）。

另一个宿主侧工具是**派活器**（生成 pi-subagents 的 workflow JS，纪律块已内建）。
它随宿主环境走，不在本仓里；没有它也能干活，只是每一波要手写那四行 `runs.all`。

## 10 本轮沉淀的硬事实（self-harden 树实测，2026-10-10）

> 这一节每条都是「照着旧写法会用错」的新事实。每条都连**判据命令**一起给，下一轮才有东西
> 可以重新跑。**为何非要连判据不可**：本轮证明同一批产物会互相把对方写旧（至少 3 例：README 的
> 测试数 `563 != 567`、旁边 §3 第 4 条的机制解释、以及某个写着「故障」而实现改判成「需人判断」
> 的节点目标）。写「事实」不带判据，下一轮就不知道它还成不成立。

### 10.1 提问通道的实际情况：worker 走 pi 的 `contact_supervisor`，问题**不进事件流**

`brief` 的 `question_policy.how` 教 worker「把问题写进 `collect --result` 的 `summary`、`status`
置 `blocked`」。**实测它基本没被走**：worker 跑在 pi 里，问宿主走的是 pi 的
`contact_supervisor`，**不会**回头跑 `collect`。

判据与读数（本仓 state dir）：`node.collect` 里 `status=blocked` 的**条数 = 0**
（全部 11 条 `node.collect` 都是 `ok`），而同期 pi 的 99 个 worker 会话里 `contact_supervisor`
被调 **27** 次。

**后果（宿主必须自己补）**：worker 的问题**不在 disflow 的事件流里**——`board` / `state.json` /
`metrics` 都看不见它。所以「宿主读到 `blocked` 再决定」这条契约，实际要**宿主自己把问答补记**
（写进节点的 `context` 或提交信息），否则这棵树对后人就是「从没问过」。`collect` 仍是**结果**的
落盘通道（别省），但它不是**提问**的通道。

### 10.2 `drive` 的 `ready_wave` 会与**正在运行**的节点抢写面（n8 修，读法在宿主）

n8 修好之前：`ready_wave` 只算「pending 节点之间」的写面不重叠，**不看正在跑的节点**——
a 正持租约写 `out.txt` 时，`ready_wave` 照样把同样写 `out.txt` 的 b 推出去 ⇒ 并写冲突。

判据：临时仓，a/b 的 `touch_plan` 都是 `out.txt`，c 是 `c.txt`；`start a` 之后跑 `drive --json`。

- 修前：`ready_wave=["b"]`（撞车）
- 修后：`ready_wave=["c"]` + `deferred=[{"node":"b","why":"与正在运行的 a 写面重叠：out.txt"}]`

**宿主侧要点**：`ready_wave` 与 `deferred` 要**一起读**——被挡下的候选不会静默消失，它带着
理由出现在 `deferred` 里。但 `drive` **不持锁、不认身份**：它只从**租约**推定「谁在跑」
（非终态 ∧ 租约在手）。宿主自己绕过 `start` 并发派活，引擎看不见。

### 10.3 提交纪律：原地派发不许 `git commit`，worktree 场景可以

门**两个场景都看得见**已提交的改动：`start` 一律记 `head_sha`，`done` 拿它当 `base_sha`。
所以「不许 commit」**不是**因为门瞎了（那是 n6 之前的旧机制，见 §3 第 4 条）。真正的分界是
**改动落在哪个分支上**：看一下 §3 第 4 条的表，机制细节也在那里，别两处各写一份。

### 10.4 `reap` 的 `--force` 语义：显式破例 + 强制记账，但**永不执行**

`worktree reap` 仍是**纯计划**（只打印命令，不跑 git）。`--force`（**只对 `reap` 生效**；传给
别的动作会 `✗ worktree apply 不支持 --force`）**只改打印出来的命令与记账**：

- `remove_cmd` 从 `git worktree remove <path>` 变成 `git worktree remove --force <path>`；
- `--json` 多出 `force: true` 与 `discarded`（逐条列出强删会丢什么）；
- 分支删除命令**一律 `-d`**（安全删），要 `-D` 得宿主自己改并自负。

判据与读数：造一个只含 **ignored** 文件的 worktree 与一个**脏** worktree，真跑删：

```text
git worktree remove（不加 --force）：ignored-only ⇒ rc=0；有未跟踪/已修改 ⇒ rc=128
  fatal: '…' contains modified or untracked files, use --force to delete it
```

所以提示里说「ignored 也会让 git 拒绝」是**假警报**（n11 修）：ignored 那半现在只说「会丢、
无需 `--force`」，dirty 那半才说「git 会拒绝、需 `--force`」。

### 10.5 效率纪律（每条的代价都是从本仓 46 个子会话的 session 日志数出来的）

| 纪律 | 代价读数 | 为什么 |
| --- | --- | --- |
| 负向对照只跑**目标测试文件**（`-p test_x.py`，0.2–2s）；全量只在收尾跑一次 | 全量被跑 **92** 次 × 46–65s ≈ **76 分钟**纯等；最差一条 lane 为 5 条对照跑了 **24** 次变异 | 全量 46–65s、对照 0.2–2s，差两个数量级 |
| 迭代期用 `python3 scripts/test_changed.py`（子集，带「不是全量」横幅）；**收尾**才 `--all` 或全量 discover | 本机实测：改 `disflow/views.py` 子集 8 文件 ≈ 7.7s；改 `README.md` 5 文件 ≈ 8.1s；改 `disflow/engine.py` 23 文件 ≈ 24.2s（全量 ≈ 58s） | 迭代期绝大多数命令不需要全量；子集把「跑测试」从分钟压到秒级，横幅 + 未覆盖清单保证不把子集读成全量 |
| 禁 `sleep` 轮询：<90s 前台跑；>90s 放后台，去做别的事，≤3 次 `tail` 查结果 | `sleep` 累计 **1236 秒 ≈ 20 分钟**前台空等 | 空转不产生任何读数 |
| 找符号/字符串先 `grep -n`（或查索引），别整读文件找符号 | 实测 `read : grep = 407 : 50` ≈ **8:1** | 整读是 O(文件) 换一次 O(命中) |
| 别为「确认」反复跑同一条命令（幂等查询如 `git status` / `ls` 除外） | 见上两条的超额次数 | 同输入同输出，再跑一次只花时间 |

### 10.6 开工先交 ≤10 行计划，并**点名方法论**

派活模板若不要求，方法论就只会出现在收尾报告里——那是**报告装饰**，不参与选路（实测：v1
模板下 46 个子会话，方法论只在收尾被要求声明）。**前向修法**：任务开头先交 ① 改哪几处
（`文件:行` 或函数名）② 选哪个方法论、怎么用它 ③ 怎么验（哪条命令、负向对照打哪个文件），
然后才开工。「怎么验」那句就是 §10.1 那条教训的正面用法。

### 10.7 符号索引：派生资源落**仓外**，先查它再定点读

配方（**别新建仓内脚本**）：stdlib `ast` 遍历 `*.py` 生成符号表 JSON；实测 1306 个符号
（101 类 + 1205 函数/方法）· 0.2s · 132KB，落点与 state dir 同级（**不进版本库**）。
⚠ 系统 `/usr/bin/ctags` 是 BSD 版，**不支持** `--output-format=json`（实测报 illegal option）
——别用 ctags 做 JSON 索引。用法：先查索引拿「文件:行」，再 `read` 只取那一段。
