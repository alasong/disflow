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
export PYTHONPATH=<disflow clone 路径>      # 本仓根
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
|---|---|---|
| `goal` | 干什么（不是标题，是**给 worker 的任务描述**） | 写 `title` → 报错列合法字段 |
| `acceptance` | 怎么算完成：`{"cmd":…,"expect":"exit0\|contains:…\|regex:…"}` | 少了它 `close` 的覆盖门会拒 |
| `touch_plan` | 写面声明：这个节点会改哪些路径 | 声明了却没改 → 门报「空洞通过」 |
| `requires` | **调度边**（必须 DAG，写时拒环） | 与 `relates`（纯引用，不参与就绪）混用 |

`requires` 是唯一的调度边；`relates` 是信息边；跨树的依赖用 `awaits: ["<别的树>"]`（等那棵树 `closed`）。

## 2 宿主循环（每轮六步）

```bash
$D drive <tree> --json        # ① 读 ready_wave / blocked / waves / stuck —— 该派谁
$D brief <tree> <node>        # ② 这就是要交给 worker 的**全部**输入
#  ③ 派：subagents_enable 后，用 subagent 工具起一个 worker，
#     task = 上面那份 brief 的 JSON（**原样给**，别自己改写或补背景）
#  ④ worker 干活：只改 touch_plan 里声明的路径
$D collect <tree> <node> --result '{"status":"ok","summary":"…","changed_files":["greeting.txt"]}'
$D done <tree> <node>         # ⑤ 引擎自算证据 + 跑门；拒收会逐条列缺什么
#  ⑥ 回到 ①
```

**为什么 ③ 必须用 fresh context**：brief 里已经有 goal + 祖先链注入的 `context` + 前置产出摘要。
worker **不需要**知道整棵树——这是整棵树的上下文继承在替他做减法。把当前会话的历史塞给它，
等于把 disflow 唯一的收益（每任务干净上下文）扔掉。

`drive --json` 里：
- `ready_wave` = 现在可以并发的节点（**同波写面互不重叠**，这是引擎算的，别自己加）
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

三条要点：

1. **`collect --result` 是 JSON，三个必填字段**：`status` `summary` `changed_files`。
   `status` 只能是 `ok` / `failed` / **`blocked`**。
2. **`blocked` 是「我要问」的合法出口**：worker 卡住时把问题写进 `summary`、`status` 置 `blocked`**然后停**——
   **不要自己拍板改范围**（范围是宿主定的）。宿主读到 `blocked` 后再决定（`rescope` / `unrequire` / 改 goal）。
   `blocked` 不是完成：`collect` **不写 done**。
3. **`done_cmd` 在 brief 里**——worker 收尾时照抄，不要自己拼。

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
|---|---|
| 0 | 成功 |
| 1 | 数据问题（树/节点不存在、结果契约不符、就绪判定不满足） |
| 2 | **门拒收**（`done` 的验收/写面/规模门、`close` 的树级门）—— 也**是 argparse 用法错误码**，别靠它区分 |

- **「门空洞通过」是警告不是拒收**：`done` 打印 `⚠️ 门空洞通过（无东西可验）` 时**它是过了**。
  所以「没改任何东西就 done」**不会**被拦——它会 exit 0 并警告。门拦的是**验收不匹配**。
  看到这个警告就别宣称「门都验了」。
- **`done` 前必须真改 `touch_plan` 里的文件**，否则 `introduced` 为空、规模门空转。
- **`--dry-run`**（`start`/`done`/`close`/`drive`）：门照跑、不落事件。想在派活前确认「能不能过」就用它。
- `--json` 只在 `drive` / `status` / `metrics` / `worktree` 上有（`trees` 没有）。
- `--help` 里有 14 个选项没写说明（如 `--result` 要 JSON）——**以本文件为准**。

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

## 7 别做的事

- **别自己算波次**：`drive` 已经算好了（含写面不重叠的分波）。手算等于绕开唯一的并发安全机制。
- **别在同一个节点上并发派两个 worker**：租约会在 `start` 挡住第二个（`exit 1`），
  但那是**白派一次**——`drive` 不持锁、不认身份，两个宿主会拿到同一个 `ready_wave`。
- **别用 `skip` 掩盖「不知道该怎么办」**：`skip` 要写理由，而理由是给人看的。
  真不知道就 `blocked` 报上来。
- **别改状态文件**：`state.json` 是投影，会被 `rebuild` 覆盖。要改就走动词（事件才是真相）。
