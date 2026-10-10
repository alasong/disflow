# self-harden 收益评估（节点 n12-evaluate）

本文件用**当场跑出来的读数**评估本轮 self-harden 这批改动的收益。每条按
「**声称 → 判据命令 → 原始读数 → 结论**」写，不用形容词代替读数。
读不出来 / 没做到的部分**单列**在 §3，不许混进 §2 当收益。

判据的组织方式：**同一场景、同一 state dir，把「改之前」的代码与「改之后」的代码各跑一遍**
（改之前的代码用 `git -C <仓> archive <该节点 merge>^ | tar -x -C <临时目录>` 取快照，
再用 `PYTHONPATH=<快照>` 跑；注意必须让 cwd **不是**仓根，否则 `sys.path[0]` 会遮蔽
`PYTHONPATH` 而静默跑成当前代码——本评估第一次跑就踩了这个坑，见 §3.5）。

- 被评的树：`self-harden` · 起点 2026-10-10（`start_ts=1791638572.27`）· deadline `2026-10-12`
- 树级读数（`python3 -m disflow.cli board self-harden`）：`[dev] 14/14`、`[verify] 0/2`
- 事件流（`<state_dir>/events.jsonl`，59 条）：`tree.init 1 · node.expand 9 · tree.deadline 1 ·
  node.start 15 · node.done 14 · node.collect 10 · node.rescope 3 · node.stage 2 · node.context 4`
- state.json 里的图字段：16 个 task 节点 + root；`requires` 用在 3 个节点上（n10、n12、n13）、
  `entry` 用在 2 个（n12 `evaluate`、n13 `reflect`）、`produces` 1（n12）、`consumes` 1（n13）、
  `relates` **0**

---

## 1 读数总表

| 节点 | 声称的收益 | 判据命令（可复跑） | 改前读数 | 改后读数 | 结论 |
| --- | --- | --- | --- | --- | --- |
| n1-metrics | 量化有节点数分母 | `python3 -m disflow.cli metrics cli-cov` | 只有点数一行（未声明）+`燃尽 2026-10-10 0 → 0` | 多出 `节点 10（已完成 10，剩 0）` 一行 | 达成 |
| n2-probe | 探针进仓、可复跑 | `python3 scripts/disflow_selfgate_probe.py` | 文件不存在（`git cat-file -e` fatal） | `✓ 5/5 个情形全过`，3.845s | 达成 |
| n3-doctor | doctor 主动报仓内 state dir 限制 | `DISFLOW_STATE_DIR=<repo>/.disflow` + `python3 -m disflow.cli doctor` | 只印 `✓ doctor 全绿`，一个字不提 | 单独一行报「worktree 功能不可用」+修法 | **部分**（严重度与需求相反，见 U1） |
| n4-host-next | 六步压一步、纯读 | `python3 -m disflow.cli host-next t --json` | 动词不存在（argparse `invalid choice`） | 波次+brief+三行命令；事件流/状态 sha256 前后一致 | 达成 |
| n5-manual | 手册补 start、写明不许自提交 | `grep -n 'start <tree>' .agents/skills/disflow/SKILL.md` | §2 无 `start`；`git commit` 0 处 | §2 七步含 start；§3 第 4 条写明禁止自提交 | **部分**（机制解释被 n6 推翻，见 U2） |
| n6-gatehead | 门看得见已提交的改动 | 原地派发 + worker 自 commit + `done` | `introduced=[]`、`size.total=0`、exit 0 | `introduced=['out.txt']`、`size.total=1` | 达成 |
| n7-accounting | done 强制 collect、规模不再真空 | `done`（无 collect）／同一场景看 `size` | 无 collect 也 exit 0；`size.total=0`+`vacuous=[size_budget]` | 无 collect **exit 1** 拒收；`size.total=1`、`vacuous={}` | 达成（只对改后的节点生效，见 §2.7） |
| n8-drivewaves | 波次看得见在跑、被挡要报因 | `python3 -m disflow.cli drive t --json`（a 在跑、b 与之重叠） | `ready_wave=['b']`（撞车） | `ready_wave=['c']` + `deferred=[{b: 与正在运行的 a 写面重叠：out.txt}]` | 达成 |
| n9-ideal | 理想线画到 deadline、归零只说真话 | `python3 -m disflow.cli metrics self-harden` | `ideal` 只有 1 点，CLI 却写「→ 2026-10-10 归零」（那天值 42） | `ideal` 3 点（42/21/**0**），CLI 写「→ 2026-10-12 归零」 | 达成 |
| n10-docs-truth | 文档数字对齐实测 + 守卫 | `python3 -m unittest discover -s tests -t tests -p test_docs.py` | `301 passed`、`45/45`、报告头 `◐ 2 条` vs 正文写 3 条 | `567 passed`（当场 `Ran 567 tests`）、`64/64`、`◐ 0 条` 与正文一致 | 达成 |
| n11-cli-truth | CLI 说的话与行为一致 | `python3 -m disflow.cli worktree reap t` / `--help` | 14/14 个选项无说明；ignored 与 dirty 用同一句假警报 | 0/14 无说明；ignored 只说「会丢、无需 --force」；`--force` 记账 | 达成 |

---

## 2 逐节点：声称 → 判据 → 原始读数 → 结论

### 2.1 n1-metrics —— 量化有了「节点数」分母

**声称**：没有 `points` 声明时，量化也要给节点数维度的读数；点数维度保持诚实文案；
两个维度可区分。

**判据**：同一棵没有 `points` 声明的树（仓内 `cli-cov`：10 个节点、`points` 声明数 0），
用 `77ed8d5^`（n1 的 merge 前）与当前代码各跑一次 `metrics`。

改前（`PYTHONPATH=<77ed8d5^ 快照>`，cwd=中立目录）：

```text
树 cli-cov：量化
  点数    未声明——10 个 task 节点都没给 `points`
          （估点是**人给的声明**，引擎不猜；所以这里不会自己编一个数出来）
  截止    未声明
  燃尽    2026-10-10 0 → 2026-10-10 0
  理想线  ⚠ 无 deadline，不构造理想线（不伪造一条线）
```

改前 `--json` 的 `summary` 里**没有** `nodes_*` 三个键（键集合：`points_total/points_done/
points_left/points_unestimated/start_ts/…`）。

改后（当前代码，同一棵树）：

```text
树 cli-cov：量化
  节点    10（已完成 10，剩 0）
  点数    未声明——10 个 task 节点都没给 `points`
          （估点是**人给的声明**，引擎不猜；所以这里不会自己编一个数出来）
  截止    未声明
  燃尽    节点  2026-10-10 0 → 2026-10-10 0
  理想线  ⚠ 无 deadline，不构造理想线（不伪造一条线）
```

`--json` 的 `summary` 键变成 `['nodes_total','nodes_done','nodes_left','points_total',…]`。
在真实这棵树上：`节点 16（已完成 14，剩 2）` 与 `点数 42（已完成 38，剩 4）` 并列，标签分别是
「节点」「点数」。

**为什么这条有意义（不是装饰）**：仓内 4 棵树合计 **33 个 task 节点、0 个声明 points**
（当场数 state.json）；被评的 self-harden 树是 16 个节点、16 个都声明了 points。
也就是说「未声明点数」不是边角情形，而是本仓此前 100% 的情形——改之前那些树的量化
只剩「0 → 0」一行，改之后至少能读出 33 这个分母。

**结论**：达成。可测出的差：0-points 树的 `metrics` 从「零信息量的一行」变成
「有分母（节点数）+ 诚实文案（点数未声明）」，且 `--json` 多出三个可机读字段。

### 2.2 n2-probe —— 探针进仓、随仓走

**声称**：把宿主的独立探针搬进仓，成为可复用、可随仓跑的证据工具。

**判据**：`git -C <仓> cat-file -e c8e9c06^:scripts/disflow_selfgate_probe.py`（改前有无该文件）；
`python3 scripts/disflow_selfgate_probe.py`（改后能不能跑）。

改前：

```text
fatal: path 'scripts/disflow_selfgate_probe.py' exists on disk, but not in 'c8e9c06^'
```

改后（当场跑，`time` 实测 real `0m3.845s`）：

```text
disflow 自举门独立探针 · 仓根 /…/worktrees/self-harden/n2-probe
  PASS  [1] 1 · start 在 apply 之前 → done fail-closed
  PASS  [2] 2 · worktree 里改并提交 → 门看得见
  PASS  [3] 3 · 删掉验收要求的文件并提交 → 因验收拒收
  PASS  [4] 4 · worktree 在 done 之前被删 → 拒收
  PASS  [5] 5 · worktree 里越界提交后 rescope ⇒ 守卫拒收
✓ 5/5 个情形全过
rc=0
```

（第 5 个情形是本轮 n15-rescope-guard 后补进去的；n2 交付时是 4 个。
文件顶部 docstring 明写「本文件是**写在被测代码之前的独立判据**」，并逐条说明每个情形测什么机制；
配套 `tests/test_selfgate_probe.py` 2 条用例。）

**结论**：达成。可测出的差：这条判据从「只在宿主会话里的一个 /tmp 脚本」变成
「`git clone` 就带着、3.8 秒能复跑、退出码 0/1 直接可用」的仓内资产。

### 2.3 n3-doctor —— 限制提前可见

**声称**：state dir 落在 repo 内部时，`doctor` **主动**报出「worktree 功能不可用」并给修法；
正常配置不得误报。

**判据**：同一份当前代码，只换 `DISFLOW_STATE_DIR`，跑 `doctor`。

改前（`f80bcc2^` 快照 + `sys.path.insert(0, <快照>)`，确认快照里没有 n3 那行字样，`grep -c` = 0）：

```text
（配置回显…）
✓ doctor 全绿            ← 对「worktree 在本配置下不可用」一个字都不提
rc=0
```

改后（state dir 在仓内）：

```text
⚠ 需人判断（不是故障，不计入退出码）：
  - worktree 功能不可用：state dir 落在 repo 内部（/Users/song/1github/disflow/.disflow，repo=/Users/song/1github/disflow）——已交付功能（worktree）在当前 state dir 配置下的可用性限制，其余功能正常；修法：把 [state].dir 或 DISFLOW_STATE_DIR 配到仓外
✓ doctor 全绿
rc=0
```

改后（state dir 在仓外，`/Users/song/.disflow/disflow-self`）：**没有**那一行，其余输出相同，`rc=0`
（不误报）。`tests/test_doctor_worktree.py` 4 条用例覆盖仓内/仓外/符号链接两测。

**结论**：**部分达成**。「主动发现、单独一行、给修法、不误报」四条都做到了；
但需求 (1) 明写「这一条属于**故障**（exit 1）」，实现把它归成「需人判断（exit 0）」，
且同一段输出里仍然印着 `✓ doctor 全绿`。见 §3.1 U1。

### 2.4 n4-host-next —— 一轮六步压成一次只读调用

**声称**：一个动词给全「波次 + 完整 brief + 收尾命令」，纯读、无常驻、无就绪节点时说实话。

**判据**：临时仓（真 git）建 3 个节点（a/b 写面重叠 `out.txt`，c 用 `c.txt`），跑
`host-next t` 与 `host-next t --json`，并比对前后 `events.jsonl` / `state.json` 的 sha256。

改前（`361cc7f^` 快照）：该动词不存在 ——

```text
disflow: error: argument command: invalid choice: 'host-next'
(choose from init, goal, expand, require, unrequire, relate, unrelate, produce, consume,
 start, collect, done, fail, retry, skip, drive, brief, plan, status, rebuild, board, tree,
 graph, import, context, rescope, close, reopen, reap, metrics, deadline, worktree, trees, doctor)
```

改后（人读态节选）：

```text
进度 0/3（0%） · 就绪 2 · 运行中 0 · 卡死 否
就绪波次：2 波（首波 2 个，同波写面互不重叠）
  第 1 波：a, c
  第 2 波：b
▶ a  [dev/dev]  第 1 波
    start:   disflow start t a --holder <HOST>
    collect: disflow collect t a --result '{"status": "ok", "summary": "<一句话>", "changed_files": ["<路径>"]}'
    done:    disflow done t a --decl '{"evidence": …}'
```

`--json` 的键：`tree goal root state state_message progress running waves ready_wave blocked
stuck plan`，每个 `plan[i]` 含 `node/wave/stage/role/goal/brief/start_cmd/collect_cmd/done_cmd`，
`brief` 就是 worker 的 task（含 `acceptance`、`touch_plan`、`question_policy`、`result_contract`）。

纯读（同一场景，跑前后）：

```text
events.jsonl sha 前/后: 2cb4b163ff037b26f25315ea7c62420d6d697fcbb8cca70fc65e293093335d36 / 2cb4b163ff037b26f25315ea7c62420d6d697fcbb8cca70fc65e293093335d36
state.json   sha 前/后: e65c7f22c98ec00a263f3477283203fc5951c040caea3aad490fac025d039907 / e65c7f22c98ec00a263f3477283203fc5951c040caea3aad490fac025d039907
```

在本树真实状态上（n12 正在跑）：

```text
进度 14/16（88%） · 就绪 0 · 运行中 1 · 卡死 否
[running] 无就绪节点，但有 1 个运行中——等 n12-evaluate 收尾
  被阻塞 n13-reflect ← n12-evaluate
```

即「没活干」时它点名是「在跑/被阻塞」而不是静默返回空；`events.jsonl`/`state.json` 同样逐字节未变
（`7b47dc49…` / `7b47dc49…`、`e3d82016…` / `e3d82016…`）。无常驻进程：这条动词是纯函数式调用，跑完之后 `ps` 里没有由它拉起的
进程（本轮 `ps -ax` 里与本评估无关的常驻进程只有宿主自己的 `lanes_watch.py`，它早于本评估启动）。

**结论**：达成。可测出的差：宿主每轮从「drive → brief → 抄三个命令」变成一次调用拿全；
判据上「纯读」可以被 sha256 逐字节钉死。

### 2.5 n5-manual —— 手册补 start、写明不许自提交

**声称**：§2 循环补进 `start`；§3 写明 worker 不许自己 commit；README 同步；有守卫。

**判据**：`git show e57c54e^:.agents/skills/disflow/SKILL.md`（改前）与当前文件对比；
守卫的**负向对照**在仓的**副本**里做（改坏 SKILL 的 §2 → 守卫必须红）。

改前 §2（`grep` 出的行号是 §2 正文内的行）：`drive`/`brief`/「派」/`collect`/`done`，
**没有** `start`；全文 `git commit` 出现 **0** 次。改前 §2 的步骤编号是 ① ② ③(派) ⑤(done)——缺一步。

改后：

```text
$D start <tree> <node> --holder <你>   # ③ 领租约 + **拍开工基线**（必须在 worker 动手前）
```

`## 2 宿主循环（每轮七步）`；§3 第 4 条：

```text
4. **worker 不许自己 `git commit`——宿主统一提交。** worker 只管把文件改到工作区（未提交）。
```

§5 陷阱另有「**每轮先 `start`，否则就没有开工基线**…」一条（`test_start_trap_is_documented` 钉它）。

守卫可红（副本里做，不动本仓）：

```text
=== [基线] 两条守卫：Ran 2 tests … OK
=== [负向对照1] README 567 → 301
AssertionError: 301 != 567 : README 说 301 passed，当场装载 567 个测试
=== [负向对照2] 删掉 SKILL §2 的 start 步骤
AssertionError: Regex didn't match: '\\$D start <tree> <node>' not found in '…'
=== [还原] Ran 2 tests … OK
```

**结论**：**部分达成**。补 start、写「不许自提交」、守卫能红——都做到了；
但 §3 第 4 条用来说明这条纪律的**机制解释**已经被同一批次的 n6 修掉，手册没跟（见 §3.2 U2）。

### 2.6 n6-gatehead —— 门看得见已提交的改动

**声称**：`start` 一律记 `head_sha`、`done` 一律把 `base_sha` 交给门，修掉「原地派发 +
worker 自己 commit ⇒ 门完全看不见改动」的 fail-open。

**判据**：同一个场景（原地派发、无 worktree）：`start` → 改文件 → `git add` + `git commit`
→ `collect` → `done`，用 `9118101^`（n6 merge 前）与当前代码各跑一遍，读 `node.done` 的 evidence。

改前：

```text
✓ d done（证据已自算落盘）
⚠️ 门空洞通过（无东西可验）：design_first（本节点未引入任何改动（introduced 为空））；size_budget（改动行数为 0）
  [rc=0]
  "introduced": [],
  "size": { "total": 0, … },
  "vacuous": { "design_first": "本节点未引入任何改动（introduced 为空）",
               "size_budget": "改动行数为 0" },
  "result_collected": null
```

改后（同一场景）：

```text
✓ a 结果已校验落盘（未写 done）
✓ a done（证据已自算落盘）
  "changed_files": ["out.txt"],
  "introduced": ["out.txt"],
  "size": { "total": 1, "per_file": { "out.txt": 1 } },
  "vacuous": {},
  "result_collected": true
```

代码侧（当前 `disflow/engine.py`）：`start` 里 `"head_sha": gates.head_sha(cwd, self.cfg)` 无条件记录；
`done` 里 `base_sha = workspace.get("head_sha")` 后同时喂给 `newly_changed` 与 `diff_size`，
不再有 `if now_worktree else None`。

**结论**：达成。可测出的差：**提交过的改动从此会被算进 `introduced` 与规模**——
这正是「宿主统一提交」之外、任何提交型 worker 都会踩的那条 fail-open 路径。

### 2.7 n7-accounting —— 两处记账漏洞

**声称 (A)**：`done` 前必须有已落盘的 `collect` 结果，否则响亮拒收（不许静默通过）。
**声称 (B)**：`size_budget` 复用 `start` 记的 `head_sha`，只在真 0 行时报真空。

**判据 (A)**：`done`（同一个刚 `start` 过、没 `collect` 过的节点）。
改前（`845478d^` 快照，同一流程）：`✓ d done（证据已自算落盘）`、`[rc=0]`，
`"result_collected": null`，只有一行 `⚠️ 门空洞通过`。
改后：

```text
✗ 拒收 a：本节点没有已落盘的 collect 结果——
  这是宿主循环契约（不是门）：worker 的结果要先经 collect 落盘，否则树的读者看不出「谁干了什么」、blocked 提问也无处留痕。
  先跑：disflow collect t a --result '{"status": "ok", "summary": "<一句话>", "changed_files": ["<路径>"]}'
  （确实没有 worker 结果时显式破例：disflow done t a --no-result "<具体理由>"）
  [rc=1]
```

**判据 (B)**：同一场景看 `size`。改前（`845478d^`，有 collect 但提交过）：
`"introduced": ["f.txt"]` 而 `"size": {"total": 0}`、`"vacuous": {"size_budget": "改动行数为 0"}`
（正是需求描述的「introduced 非空、规模真空」）。改后：`size.total = 1`、`vacuous = {}`。

**历史存量（修不回来，也不该假装修回来）**：本树 14 个 done 节点里，
`node.collect` 事件数为 0 的有 **4** 个——`n1-metrics`、`n2-probe`、`n5-manual`、`n6-gatehead`
（它们 done 于 n7 之前）；n7 之后 done 的 10 个节点全部 `collect`=1。也就是说这条契约是
**向前生效**的，事件流本身把「改之前那 4 个节点没有结果上报」如实留着。

**结论**：达成（对改后的节点生效）。可测出的差：`collect` 从「可选动作」变成
「`done` 的硬前置」，`blocked` 提问通道（FR-15）重新接上；规模真空读数消失。

### 2.8 n8-drivewaves —— 波次看得见在跑、被挡必须报因

**声称**：`drive` 计算波次时把「在跑并且持有租约」的节点的 `touch_plan` 算作已占用；
重叠候选不进就绪波，且必须逐条报因（不许静默丢）。

**判据**：临时仓，a 与 b 的 `touch_plan` 都是 `out.txt`，c 是 `c.txt`；`start a` 之后跑
`python3 -m disflow.cli drive t --json`，用 `f785b06^`（n8 merge 前）与当前代码各跑一遍。

改前：

```json
{ "live": ["a","b"], "running": ["a"], "ready_wave": ["b"], "waves": [["b"]], "blocked": {}, "stuck": false }
```

（此刻 a 正持租约在写 `out.txt`，而 `ready_wave` 把同样要写 `out.txt` 的 b 推出去 ⇒ 并写冲突。）

改后：

```json
{ "running": ["a"], "ready_wave": ["c"], "waves": [["c"]], "blocked": {}, "stuck": false,
  "deferred": [ { "node": "b", "why": "与正在运行的 a 写面重叠：out.txt" } ] }
```

人读态也报（节选）：

```text
就绪首波 1 个 / 共 1 波（**只推不跑**，spawn 归宿主）：
  c  dev/dev  做C
写面被在跑节点占用（本波不派，防并写冲突；等它们收尾或改 touch_plan）：
  b  与正在运行的 a 写面重叠：out.txt
```

只推不跑也读到：`drive` 前后节点状态与事件流不变（n8 的测试里钉的是「无在跑时输出与基线逐字节一致」）。

**结论**：达成。可测出的差：`ready_wave` 从「可能让你派一个和正在跑的 worker 抢同一文件的节点」
变成「重叠候选被挡下 + 有一条 `deferred` 记录 blame」。

### 2.9 n9-ideal —— 理想线画到 deadline、只在真归零点说「归零」

**声称**：`ideal`/`ideal_nodes` 覆盖 start_day → deadline、末点为 0 且那天就是 deadline；
`series` 仍只到今天；CLI 不许在值 > 0 的日子说「归零」。

**判据**：在**活的** state dir 上（只读）跑 `metrics self-harden`，用 `7d36c3c^`（n9 merge 前）
与当前代码各跑一遍。

改前：

```text
  燃尽    节点  2026-10-10 2 → 2026-10-10 2（理想线 → 2026-10-10 归零）
          点数  2026-10-10 4 → 2026-10-10 4（理想线 → 2026-10-10 归零）
```

```json
{"series":[{"day":"2026-10-10","remaining":4.0,"remaining_nodes":2}],
 "ideal":[{"day":"2026-10-10","ideal":42.0}],
 "ideal_nodes":[{"day":"2026-10-10","ideal_nodes":16.0}],
 "deadline":"2026-10-12"}
```

——末点的值是 **42.0 / 16.0**（不是 0），却指着 2026-10-10 说「归零」；而 deadline 是 2026-10-12。

改后（同一份 state、同一时刻）：

```text
  燃尽    节点  2026-10-10 2 → 2026-10-10 2（理想线 → 2026-10-12 归零）
          点数  2026-10-10 4 → 2026-10-10 4（理想线 → 2026-10-12 归零）
```

```json
{"series":[{"day":"2026-10-10","remaining":4.0,"remaining_nodes":2}],
 "ideal":[{"day":"2026-10-10","ideal":42.0},{"day":"2026-10-11","ideal":21.0},{"day":"2026-10-12","ideal":0.0}],
 "ideal_nodes":[{"day":"2026-10-10","ideal_nodes":16.0},{"day":"2026-10-11","ideal_nodes":8.0},{"day":"2026-10-12","ideal_nodes":0.0}],
 "deadline":"2026-10-12"}
```

`series` 与改前**逐字节一致**（仍只到今天，不把未来画成事实）。
措辞侧还有一道防线：`disflow/cli.py` 的 `_ideal_tail` 在末值 ≠ 0 时改成
`（理想线 → <日> 剩 <N>）`，不寄生在「末值恒 0」这个不变量上。

**性质说明**：`series` 里的「今天」是 **UTC 日**——本机 `date` 是 `Sun Oct 11 00:07:52 CST 2026`、
`date -u` 是 `Sat Oct 10 16:07:52 UTC 2026`，所以 series 只有一个 2026-10-10 的点。这不是 bug，
但读的人要知道：跨零点前后的「今天」与本地日历差一天。

**结论**：达成。可测出的差：燃尽图从「一个点 + 一句假话」变成「一条能看出计划形状、
末点真为 0 且在 deadline 那天」的线。

### 2.10 n10-docs-truth —— 文档数字对齐实测 + 守卫

**声称**：README 的测试数/变异数、backlog 的变异计数、FR 报告的自相矛盾计数都改到与实测一致，
并加能红的守卫。

**判据（逐条当场跑）**：

```text
$ python3 -m unittest discover -s tests -t tests
Ran 567 tests in 58.332s
OK            (rc=0)
```

（收尾时又跑了一次全量（本文件写定之后）：`Ran 567 tests in 56.024s` · `OK` · `rc=0` —— 两次同数。）

改前 README（`081f105^`）：`# 301 passed`、`# 变异测试：45/45`；改后：`# 567 passed`、`# 变异测试：64/64`。
当场装载套件实测 **567** —— 与改后一致，与改前不一致。守卫钉的就是这个等式；
负向对照：把 `567` 改回 `301` ⇒

```text
AssertionError: 301 != 567 : README 说 301 passed，当场装载 567 个测试
```

变异数分母：`python3 scripts/mutation_check.py --list | wc -l` = **64** —— 与 README/backlog 的
`64/64` 分母一致（改前是 45）。backlog 改前 `（当前 **45/45**）`、改后 `（当前 **64/64**）`。
（**捕获率分子**另说，见 §3.6。）

FR 报告的自相矛盾（改前）：

```text
第 47 行：…另有 `[◐ 部分缺口]` 2 条。
第 51 行：**（空）**：29 FR + 7 NFR 里没有整条未实现的。但下节有 3 条 **◐ 部分缺口**…
```

改后跑生成器 `python3 scripts/fr_check.py`：

```text
[◐ 部分缺口]（整条机检通过，但子句缺失——诚实单列）
  （空：没有整条机检通过、但子句在实现里找不到的需求）
四类计数：机检 29（通过 29 · 失败 0） · 测试 6 · 未实现 0 · 不可机检 1 · 合计 36（应 36）；另有 ◐ 部分缺口 0 条
rc=0
```

`docs/fr-nfr-gap-report.md` 仍能由 `scripts/fr_check.py --report` 逐字节重生成
（`test_report_regenerates_byte_for_byte`，跑在 `test_docs.py` 里，19 条用例全绿）。

README 的「5 分钟上手」state dir 那条（改前用仓内 `.disflow` 却不说限制）现在是：

```text
export DISFLOW_STATE_DIR="$PWD/.disflow"       # 仓内 state dir：上手够用，但 worktree 会被硬拒（见「量化、迭代、worktree」）
```

这与 §2.3 的 doctor 读数一致（仓内 state dir ⇒ worktree 功能不可用）。

**结论**：达成。可测出的差：三处数字/措辞从「陈旧或自相矛盾」变成「与当场实测一致」，
并且**下次漂移会被 `test_docs.py` 抓红**（已用负向对照证明能红）。

### 2.11 n11-cli-truth —— 三处「说的话与行为不一致」

**(A) `worktree reap` 的 leak 提示**。判据：真的造一个「只有 ignored 文件」的 worktree，
不带 `--force` 删它，看 git 到底拒不拒。

```text
=== worktree 里 git status --porcelain --ignored=matching:
!! secret.log
=== git worktree remove（不加 --force）:
rc=0
=== 同一条再删:
fatal: … 'is not a working tree'   (rc=128)
```

对照（有未跟踪/已修改文件时）：

```text
fatal: '…' contains modified or untracked files, use --force to delete it
rc=128
```

改前（`9f3212f^` 的 `disflow/worktree.py`）两半共用同一句：

```python
warnings.append("未提交的已跟踪改动会丢失（git 会拒绝，需 `--force` 才能删）")
warnings.append(f"被忽略的文件 {detail} 会丢失（git 会拒绝，需 `--force` 才能删）")
```

改后（真跑 `worktree reap t`，同一棵临时树）：

```text
  leak     a  ⚠ 跑了会丢东西
           理由：dirty:未提交的改动（已修改或未跟踪）
           理由：ignored:secret.log
           ⚠ 未提交的改动（已修改或未跟踪的文件）会丢失（git 会拒绝，需 `--force` 才能删）
           ⚠ 被忽略的文件 secret.log 会丢失（git 不因 ignored 文件拒绝删除：这一条无需 `--force`）
```

即：**dirty 那半保留「git 会拒绝」，ignored 那半改成「无需 `--force`」**，两半各自只说自己成立的部分。

**(B) 显式 `--force` 记账**。`worktree reap --help` 里有 `--force`（只对 `reap` 生效：传给别的动作会
`✗ worktree apply 不支持 --force（显式强删计划只对 reap 有意义）`）。真跑 `worktree reap t --force`：

```text
可回收 1 项（**只推不跑**：引擎不执行删除，下面命令由人跑）
  ⚠ 显式 --force：破例强删，下列将被丢弃（记账，逐条留痕）
      a：未提交的改动（已修改或未跟踪的文件）会丢失（git 会拒绝，需 `--force` 才能删）
      a：被忽略的文件 secret.log 会丢失（git 不因 ignored 文件拒绝删除：这一条无需 `--force`）
           删工作区：git worktree remove --force …/state/worktrees/t/a
```

`--json` 里 `discarded` = `[{"node":"a","losses":[…两条…]}]`。没传 `--force` 时输出与改前逐字节同形
（`remove_cmd` 不带 `--force`）。

**(C) 14 个选项的 help**。判据：直接读 `argparse` 的 `action.help`（不是 grep 帮助文本，
因为带 metavar 的选项即使没 help 也会印成 `--goal GOAL`）：

```text
选项               旧代码 help   当前代码 help
init --goal        ✗ 空         树的总体目标（必填；一句话说清要达成什么）
expand --children  ✗ 空         子节点声明数组（JSON 字符串）：形如 [{"id":"a","goal":"…"}]；每项至少含 id
collect --result   ✗ 空         结果契约（JSON 对象）：必含 status/summary/changed_files；status ∈ {ok,failed,blocked}
done --decl        ✗ 空         声明（JSON 对象，可选）：只放破例/豁免，如 {"scope_waiver":"…"}；证据由引擎自算
…（其余 10 个同样）…
==> 无说明：旧 14/14 · 当前 0/14
```

**结论**：达成。可测出的差：狼来了的那半句话消失；`--force` 从「文档里的约定」变成
命令行可达且强制记账；14 个最易用错的选项现在自带形状说明。

---

## 3 未达成 / 未验证（单列）

### 3.1 U1 「n3 的严重度」与需求文本相反（**未达成**）

n3 的需求原文 (1)：「按本仓 `doctor` 既有约定区分『故障(exit 1)』与『需人判断(exit 0)』，
**这一条属于故障**」。实现的读数（§2.3）：`rc=0`，分类是「⚠ 需人判断（不是故障，不计入退出码）」，
同一段输出里还印着 `✓ doctor 全绿`。

实现侧给了理由（`tests/test_doctor_worktree.py` 顶部 docstring、`disflow/cli.py` 第 385-395 行注释）：
仓内 state dir 是**出厂默认**（`spec.py` 的 `state.dir = ".disflow"`），判成故障会因默认布局
把所有人假红——「假红比不报更坏」。这个取舍本身可能更对，但**它与 n3 的验收契约不一致**，
而 n3 的验收命令只跑它自己新增的测试文件，所以**门抓不到这个偏差**。本评估把它如实留在这一节：
要的是「故障」，交付的是「需人判断」。**谁来定**：宿主（改需求文本 or 改实现），本评估不改范围。

### 3.2 U2 手册 §3 的机制解释被同批次 n6 推翻（**未达成**）

`.agents/skills/disflow/SKILL.md` §3 第 4 条写着：

```text
实测（原地派发，无 worktree）：worker 自己 commit ⇒ 提交后工作区干净，而门的归因只读
`git status`（无 worktree 时 `base_sha=None`，不看已提交改动）⇒ `introduced=[]`、
`vacuous=[design_first, size_budget]`、**exit 0**——门彻底看不见改动
```

这段的读数**逐字复现得出来**——但只能复现**改前**的代码（`9118101^`，即 n5 的 merge 之后、
n6 的 merge 之前；n5 的 merge `e57c54e` 早于 n6 的 `9118101`）。当前代码给的是
`introduced=['out.txt']`、`vacuous={}`（§2.6）。也就是说：n5 写下的「为什么不许自提交」的
机制解释，被同一批次里紧接着 n6 的修复废掉了，而**后跑的 n10（文档真话）没有把它纳入守卫**。

留在这里的理由：这不是措辞问题，是「读者照做会得出错误的心智模型」——
手册现在仍然在告诉人「提交了门就看不见」（事实：现在看得见）。规则本身（宿主统一提交）
是否保留是宿主的决定，本评估不动。

### 3.3 U3 图侧机制（requires/relates/produces/consumes/entry）（**大部分未覆盖**）

需求要求单列这一条。当场读数（分两半，**不要合并成一句**）：

(a) **树里的实际使用**（`state.json` 逐字段计数）：

```text
本仓 4 棵既有树：disflow-v0.1 {requires:15, relates:0, produces:0, consumes:0, entry:2}
                 cli-cov      {requires:5,  relates:0, produces:0, consumes:0, entry:2}
                 gap-cov      {requires:4,  relates:0, produces:0, consumes:0, entry:2}
                 d            {requires:0,  relates:0, produces:0, consumes:0, entry:0}
self-harden 树：requires 3 个节点（n10 ← n7,n8,n9；n12 ← n1..n11；n13 ← n12）
                entry 2（n12=evaluate / n13=reflect）· produces 1（n12）· consumes 1（n13）· relates 0
```

(b) **单测覆盖**（`grep -rn <字段> tests/` 命中文件数）：`requires` 有 `tests/test_core.py` 的
`ready_requires_deps_done`、`unmet_requires`、环检测等用例（该用例自 `24e88fe` 起就在仓里，**早于本轮**）；
`relates` 在 `test_views.py`/`test_edges.py` 有软边断言；`produces`/`consumes` 在
`test_core.py`/`test_edges.py`/`test_cli_build.py` 有引用。

**结论**：需求里「图侧机制在本轮之前**零覆盖**」这个说法，按测量**不能整条成立**：
`requires`/`entry` 既有单测也有真实使用（4 棵既有树里都在用）；真正在**本轮之前零覆盖**的
是 `relates`（4 棵树、0 个节点声明）、以及 `produces`/`consumes` 的**生产使用**
（0 个节点声明，本树才第一次用上）。

**仍然未验证的部分**：`entry` 驱动 `close` 的树级门只在**单测的合成树**上跑过，
本树自己还没跑到那一步（见 U4）；`requires` 的跨节点完成信号在本树里只起「等待」作用
（n12 等 n1..n11），**没有**一次「前置未 done ⇒ 节点不就绪」的**拒绝**被本树真实触发过
（都被 `drive` 的就绪过滤静默挡在外面，看不见读数）。

### 3.4 U4 `close` 的两道树级门（**本轮未跑通**）

`close` 的两道树级门（`disflow/engine.py:688-703`）：`tree_closure`（要有 done 的
evaluate 与 reflect/standardize）与 `tree_coverage`（叶子任务有 goal + 可跑判据）。
当场读数：

```text
$ python3 -m disflow.cli close self-harden --dry-run
✗ 预演会被拒 self-harden：
  - 缺 done 的收益验证节点（entry=evaluate）
  - 缺 done 的经验沉淀节点（entry=reflect|standardize）
rc=2
```

`state.json` 里 `closed=false`；`host-next` 也如实报 `[verify] 0/2`。
所以：**这两道门在本树还没有一次成功通过**——真正跑通要看 n12（本文件）与 n13 都 done 之后。
门自身有测试：`tests/test_gates.py::TreeClosureTest` 4 条（含逃生口
`root.context.closure='not_applicable: …'` 与其空理由拒收）、`tests/test_coverage.py` 里 9 条用例
直接调 `tree_coverage_check`（该文件共 13 条用例，另有 `close` 级用例）；
`close` 的调用面在 `test_dryrun.py`/`test_reopen.py`/`test_vacuity.py` 里也有。
`tree_closure_check` 这个函数**没有同名直达用例**（`grep -rn tree_closure_check tests/` = 0），
只有经 `engine.close` 的间接覆盖——这条差异留在这里，别当成「两道门都零覆盖」。

### 3.5 U5 这份评估本身不独立（**构造者 == 验证者**）

本文件由 `n12-evaluate` 这个 worker 写；被评估的 n1..n11 由**同一个宿主、同一条会话**
派发与合并，本评估用的「改前」代码快照（`git archive <merge>^`）也是从**同一个仓**取的。
这不是独立验证，只是一个「按判据复跑」的报告。因此**只有「先声明判据、再跑」的那部分
才有循环外的信息量**，具体是这几段：

1. **节点 brief 里事先写死的回归判据**：n8（「无在跑时必须与基线逐字节一致」）、
   n9（「先存基线，再改，比对 series/节点数/点数/速度/截止」）、n15（「先写探针再修」）、
   n2（「写在被测代码之前的判据」）——这些判据是**开工前**就落进事件流的，我是事后复跑，不是事后编判据。
2. **本评估的负向对照**：先声明「改坏 ⇒ 必须红」，再改、再贴红、再还原（§2.5 的守卫对照、
   §3.7 的 acceptance 对照）。红是我预期过的红。
3. **一次「先入为主的判据被当场读数打脸」**：我第一次设计 `git worktree remove` 的实验时，
   夹具里 `.gitignore` 还没提交 ⇒ ignored 文件在 worktree 里其实是**未跟踪**，实测得到
   `rc=128`（不是我预期的 ignored 情形 `rc=0`）。这是夹具的错，不是被测代码的错；
   把 `.gitignore` 先提交后重跑才拿到 `rc=0`（§2.11）。同一类错误还有一次：
   我第一次拿 `git archive` 快照跑 `metrics` 时忘了 `sys.path[0]` 的遮蔽，
   cwd 就是仓根 ⇒ 导入到的是**当前代码**，于是「旧代码」跑出了新行为（当时差点写进报告）。
   装上 `cwd=中立目录` 后才拿到真正的改前读数——本文件所有「改前」读数都是修正后的口径。

**其余段（大部分）是**：读者必须假定「我写的判据是完备的」。这份报告**能证明**那些命令
今天给出这些读数；**不能证明**这些判据覆盖了所有失败模式。

### 3.6 U6 变异的**捕获率**（分子）

`64/64` 里的分母已被当场钉死（`mutation_check.py --list | wc -l` = 64，与 README/backlog 一致）。
分子（真跑一整轮注入、看有没有漏抓）**耗时以十分钟计**，本评估把它放在**仓的副本**里跑，
不占用也不污染本仓；读数见 §3.6.1。**若那一栏为空/缺失，就是没跑完，
读的人应当把它当「未验证」，不要当「通过」。**

判据命令（必须 `--list` 先行，避免针脚过期把结果说成漏抓）：

```bash
python3 scripts/mutation_check.py --list | wc -l      # 分母：64
python3 scripts/mutation_check.py                     # 分子：整轮注入 + 还原
```

**为什么它不属于「文档已经保证过的事」**：README/backlog 的 `64/64` 只被
`test_docs.py` 钉了**分母**（当场装载变异表长度）；「64 条真的都被测试抓住」这句话，
改前改后都没有一条常驻断言在核。

#### 3.6.1 整轮读数（本轮实测）

在**仓的副本**里跑（`M=$(mktemp -d); git -C <仓> archive HEAD | tar -x -C "$M"; cd "$M"`），
不改本仓任何文件：

```text
…（64 行，每行一条，形如）
  ✅ expand 事件不带 holder（则谁挂的无从追溯）（注入 exit=1 / 还原 exit=0）
  ✅ question_policy 用整体替换而非合并（纪律静默变窄）（注入 exit=1 / 还原 exit=0）
  ✅ 结果契约里去掉 blocked（则提问通道不存在）（注入 exit=1 / 还原 exit=0）
  ✅ brief 不写 question_policy（FR-15 三样缺一）（注入 exit=1 / 还原 exit=0）
  ✅ 配置里 question_policy 拼错了不报（静默丢掉，纪律没生效而不自知）（注入 exit=1 / 还原 exit=0）
捕获率：64/64
rc=0
```

数一下：`✅` **64** 行 · `❌`/`⛔` **0** 行 ⇒ 分子 **64** 与分母一致；
每条读数都带「注入 exit / 还原 exit」（只有「注入非 0 **且** 还原 0」才算抓住，
否则就是「测试根本没跑起来」被当成抓住）。
**结论**：README/backlog 的 `64/64` 这一轮是真的（且是本轮**当场跑出来**的，不是抄的）。

### 3.7 U7 acceptance 是空门（**诚实条款**）

本节点的验收命令是：

```bash
test -s docs/self-harden-evaluation.md
```

它只检查「文件存在且非空」。当场验证这个门有多空（临时目录里做，不用本仓）：

```text
$ printf 'x' > docs/self-harden-evaluation.md ; test -s docs/self-harden-evaluation.md ; echo rc=$?
rc=0
$ rm docs/self-harden-evaluation.md ; test -s docs/self-harden-evaluation.md ; echo rc=$?
rc=1
```

也就是说：**一个字节的垃圾也能让这道门变绿**。报告的内容不是机器可判的，
所以**真正的验收是（人）读这份文件**，而 `test -s` 只保证「有这么个东西」。
本节点**不**为这句话加任何自动化承诺——写在这里是为了不让一个空门冒充「评估已通过」。

### 3.8 U8 其它未验证项（逐条写清「为什么没验」）

| 项 | 为什么未验证 |
| --- | --- |
| 全量测试的**重跑** | 效率纪律 §0：全量只在收尾跑一次。本评估跑了 2 次（一次为核 README 的 `567 passed`、一次为收尾门），两次都是 `Ran 567 tests … OK`，没有第三次 |
| 除需求点名的 14 个之外还有没有空 help | 只复核了那 14 个（旧 14/14 空 → 现 0/14），没做全动词普查 |
| `node.collect` 契约对**历史** 4 个节点（§2.7）的追溯 | 事件流不可改；`n1/n2/n5/n6` 的 `result_collected` 永远是 `null`，只能当已知缺口读 |
| `requires` 的「未满足 ⇒ 不就绪」拒绝路径 | 本树没有触发过（见 U3 末尾） |
| 跨树 `awaits` / `tree.close` 作为完成信号 | 本树没有跨树等待；`close` 未跑通（见 U4） |
| 各节点自己的验收命令（如 `python3 -m unittest … -p test_metrics.py`） | 它们的证据已落在 `node.done` 事件里；本评估没有逐个重跑，只跑了全量一次 |
| `n16-hostnext-deferred` 的 `state` 取值在人读态的一致性 | 本树当前没有「全被挡」的场景可测；只读到 `running` 与 `ready` 两种 state |

---

## 4 复跑清单（读的人自己验）

```bash
cd <仓>
export DISFLOW_STATE_DIR=<仓外目录>                 # 仓内 state dir 会让 worktree 功能不可用（§2.3）

# 树级读数
python3 -m disflow.cli board self-harden           # [dev] 14/14 · [verify] 0/2
python3 -m disflow.cli metrics self-harden         # 节点 16（14/2）· 点数 42（38/4）· 理想线到 deadline
python3 -m disflow.cli metrics self-harden --json  # ideal 末点 day == deadline 且值 == 0
python3 -m disflow.cli host-next self-harden       # 纯读：跑前后 events.jsonl/state.json 的 sha256 不变
python3 -m disflow.cli close self-harden --dry-run # rc=2：缺 done 的 evaluate 与 reflect

# 独立判据
python3 scripts/disflow_selfgate_probe.py          # 5/5 · ~3.8s

# 文档与数字守卫 + 全量
python3 -m unittest discover -s tests -t tests -p test_docs.py
python3 -m unittest discover -s tests -t tests     # Ran 567 tests … OK（~58s）

# 「改前 vs 改后」复跑（把任一节点的 merge commit 的父提交取出即可）
D=$(mktemp -d); git archive <merge>^ | tar -x -C "$D"
cd <中立目录> && PYTHONPATH="$D" DISFLOW_STATE_DIR=<state> python3 -m disflow.cli metrics self-harden
```

> 提醒（本评估踩过的坑）：拿快照跑旧代码时，**cwd 不能是仓根**，否则 `sys.path[0]` 会
> 遮蔽 `PYTHONPATH`、静默跑成当前代码（§3.5）。
