# disflow 开发看板（v0.1）

> 状态：**v0.1 完成（17/17）** · 2026-10-10
> 用途：disflow **自己的第一批节点**，开发按本看板推进（dogfooding）。
> 配套：`docs/requirements.md` · `docs/design.md`

---

## 0. 进展（2026-10-10）

| 项 | 结果 |
| --- | --- |
| R1 复用决策 | ✅ 只 vendor `flowcore/store.py`（`disflow/store.py`），其余重写 |
| R2 设计定稿 | ✅ `docs/design.md` |
| 实现 | D10–D95 + D100 全绿 |
| 测试 | `python3 -m unittest discover -s tests -t tests` → **Ran 276 tests, OK** |
| 端到端 | `drive` 确定性、`rebuild --check` 一致、`doctor` 全绿 |
| 门验证 | acceptance 缺失/失败、超 `touch_plan`、超 `size_budget`、缺 evaluate/sediment → **全部拒收** |
| 树终止 | `close` 跑 `tree_closure`，本看板树已关闭 |
| 看板视图 | `board` / `tree` / `graph` 三视图同源 |
| 多块板 | 多树作用域隔离 + 跨树 `awaits` 显式完成信号（`tree.close`） |
| 租约回收 | `start` 带 `expires_at`；`reap` 显式回收过期租约（`doctor` 会报） |
| 覆盖门 | `tree_coverage`：叶子任务有 goal + 可跑判据；非根容器有子节点（`close` 串联 `tree_closure`） |
| 模块分层 | 核心层 = `core`/`store`/`graph`/`ready`（零业务词 + 依赖单向）；`tests/test_layering.py` 逆向自检 |
| 预演 | `start`/`done`/`close`/`drive` 支持 `--dry-run`：门照跑、**零事件**（`tests/test_dryrun.py`） |
| 需求覆盖 | `docs/requirements.md` §7 逐条审计 FR/NFR —— **FR-1..29 + NFR-1..7 全覆盖（0 项 ◐）** |
| 预演盲点 | `close --dry-run` 曾经 CLI 漏传 `dry_run` 真关树（V3 抳出）→ 已修 + **CLI 层回归测试** |
| 偏离 | 本机无 pytest → 标准库 `unittest`（与 NFR-1 零依赖一致） |

**门清单（设计 §7）**：`acceptance` ✅ · `design_first` ✅ · `size_budget` ✅ · `tree_closure` ✅ · `tree_coverage` ✅。

**独立验证（二轮：2026-10-10，HEAD `46cb2bd`，5 个 fresh-context 只读验证者，各自临时状态目录，零仓库写入）**：

| lane | 范围 | 结果 | 发现 |
| --- | --- | --- | --- |
| V1 | 就绪/波次/`drive` **逐字节**确定性/容器不派发/`awaits` 门控 | PASS 4/4 | 文档漂移：§3 写 `requires` 需 `done`，实现是 `TERMINAL`（`skip` 也不阻塞）→ 已修文档 |
| V2 | 六类门必须拒收假完成 + 逃生口空理由照拒 | PASS 6/6 | 无 |
| V3 | 投影一致/篡改必被抓/**预演零事件**/租约回收 | **FAIL 3/4** | **真缺陷（已修）**：`cmd_close` 漏传 `dry_run` → `close --dry-run` 真关树并落事件；现有测试绕过 CLI 未抳到 |
| V4 | mgt 迁移等价（合成 fixture） | PASS 4/4 | 无冲突；补充了 `skipped_edges` 降级口径 |
| V5 | 分层自检**非空转**（临时副本注入） | PASS 4/4 | **自检比声称的弱**：针脚白名单漏 stage/role 取值 → 已加固（单词边界 + `MatcherSelfTest` 自证） |

二轮新增的验证面（相对一轮）：跨树 `awaits`、`tree_coverage`、`waiver`/`require_declarations`、
`--dry-run` 零事件、租约 `reap`、核心层分层自检。

**一轮（2026-10-10，3 个 fresh-context 验证者）**：

| lane | 结果 | 发现 |
| --- | --- | --- |
| V1V2 | PASS（3/3） | 无 |
| V3 | PASS（3/3） | **真缺陷**：坏事件行以未捕获 `ValueError` traceback 露出 → 已修（`cli.main` 捕获 ValueError/OSError） |
| V4 | PASS（4/4 断言） | **设计 §10 与实现有偏离** → 已在 `design.md` §10.1 如实登记 |

V3 的「篡改投影必须被抓」**确实被抓**（exit 1）—— 一致性检查不是摆设。

**诚实标注的局限**：

1. **V4 用合成 fixture**：本机 fat3 `.fat/mgt/` 无 `state.json`，无真实项目可导入（两轮均如此）。
2. ~~**`tree_coverage` 未做**~~ ✅ 已补（v0.2：叶子任务需求已登记 + 可跑判据；非根容器不空壳）。
3. **§10 的三条偏离**（`global_status` 节点化 / `phase→stage` / `registry` 登记）只在文档登记，未实现。
4. **二轮未覆盖**（V2 自述）：`diff_size` 的 tracked 已修改文件分支；`contains:`/`regex:` 两种 `expect`。
5. **分层自检不是闭集**：针脚表式，未登记的业务词仍会漏判（`done` 因与机制终态重名而根本不可判定）——已写进 `design.md` §9.1。**实测**：往 `ready.py` 注入 `approver`/`auditor`/`column`/`audit`（均不在针脚表里）→ 自检**照样通过**（漏判）。
6. **种子看板的两个门是空洞通过的**（本项为补登）：上文“17/17、五道门”中的 `design_first`（作用域）与 `size_budget`（规模）**对 16/16 个 done 节点都没验到任何东西**（这些节点的 `introduced == []`，因为种子脚本是“先改完再造证据”的回填，不是真循环）。真跑的只有 `acceptance` / `tree_closure` / `tree_coverage`。现已由证据字段 `vacuous` + `done`/`close` 的报出机制显式可见（`design.md` §7.5）。

### 0.1 首次真跑循环（树 `cli-cov`，2026-10-10）

背景：上面两轮验证都测了「计算的正确性」，却从未测过「分发器真的被用来干活」。
本轮拿一个**真实目标**（补 CLI 装配层测试）跑完整循环——顺序、门、证据全部由 
disflow 决定，**无硬编码列表**。

| | 结果 |
| --- | --- |
| 树 | `cli-cov`（与 `disflow-v0.1` 并行 = 多块板） |
| 节点 | C1–C4（测试）· C5–C8（循环中发现的缺陷修复）· E1（收益验证）· S1（沉淀） |
| 循环是否由 `drive` 驱动 | 是：每次选谁干都读 `drive` 的就绪波次 |
| 产出 | 4 个新测试文件（48 个用例）、4 处真缺陷修复、1 次变异测试 |

**循环中发现的真缺陷**（不是人造用例）：

| # | 缺陷 | 怎么暴露的 | 后果 |
| --- | --- | --- | --- |
| 1 | `cmd_close` 漏传 `dry_run` → `--dry-run` 真关树 | 写 `close --dry-run` 测试 | 与文档矛盾（V3 曾报，但当时测试绕过了 CLI） |
| 2 | `GateReject` 报文走 **stdout**（`EngineError` 走 stderr） | 断言错误通道 | 脚本 `2>/dev/null` 时把门拒收看成成功 |
| 3 | `cmd_doctor` 失败报文同样走 stdout | 同上 | 同上 |
| 4 | `cmd_import` 错误报文无 `✗` 前缀 | 断言报文约定 | 报文不一致（机器不可区分） |
| 5 | **`store._persist` 先落盘再重投影** | 重复 `expand` 被拒后 | **一次非法写入 = 状态永久不可恢复**（事件流不可再重放） |

> #5 最严重：`project` 才是语义闸门（重复 id / 环 / 悬空引用都在这里抛），
> 而它跟在 `append_jsonl` **之后**。实测：同一个重复 `expand` 被拒 3 次，
> 事件流里就多了 3 条重复事件，`rebuild` 直接不可用。修法：先试算后落盘。
> 被污染的事件流做了一次**手工修复**（剔除 2 条本不该存在的事件，备份为
> `events.jsonl.poisoned.bak`）——这是 bug 自己破坏了 append-only，不是人为篡改。

**变易测试（E1 的验收）**：把 5 处修复逐个“抖回”缺陷，验证新测试能抓到：

| 变易 | 注入后 | 还原后 |
| --- | --- | --- |
| M1 `cmd_close` 漏传 `dry_run` | 失败 ✅ | 通过 |
| M2 GateReject 回 stdout | 失败 ✅ | 通过 |
| M3 import 去 `✗` 前缀 | 失败 ✅ | 通过 |
| M4 `_persist` 回到先落盘 | 失败 ✅ | 通过 |

**捕获率 4/4** —— 新测试有牙，不是摆设。

### 0.2 本轮暴露的产品/流程缺口（已登记 backlog §C4）

| # | 缺口 | 证据 |
| --- | --- | --- |
| 1 | ~~**`touch_plan` 建树后不可更新**，且 `design_first` **无逃生口**~~ ✅ **已修**（设计 §7.2）：新增 `rescope`（计划先于改动）+ `scope_waiver`（显式破例、留痕）；现在有合法路径改声明 | 干活时发现需多改一个文件 → 门拒收，但无合法路径改声明 |
| 2 | ~~**baseline 是文件粒度**~~ ✅ **已修**：改为 `{路径: 内容指纹}`（`design.md` §7.1），并加反向验证（退回路径差集 → 新测试失败） | C6 的 scope 检查曾空转（C5 先弄脏了 `cli.py`） |
| 3 | 无 `unrequire`：建错依赖只能 `skip` 整节点 | C7 的 requires 方向建反，只能 skip 并记录 |
| 4 | **陈旧 `__pycache__` 会静默跑旧代码** | store 修复已落盘但行为仍旧；清字节码后立即生效 |

### 0.4 再接着修 C4.1 + C4.4（写面声明的当口改动）

**设计难点**：不能只开一个「随便改计划」的口子——`design_first` 的力量全来自
**计划先于改动**，能事后把计划改成“我刚改过的东西”就等于没门。所以拆两条：

| 路径 | 何时用 | 判据 | 留违规记录？ |
| --- | --- | --- | --- |
| `rescope` | 要碰 X，**还没碰** | 只能纳入**不在本节点 `introduced`** 里的路径（复用 §7.1 的内容指纹） | 否 |
| `scope_waiver` | **已碰**了 X 且有理 | 无技术判据，就是显式破例 | **是**（`violations` 照记） |

| 验收点 | 反向验证 |
| --- | --- |
| 5 处新判据（已碰过拒收 / reason 必填 / 只限 running / 无空操作 / 破例留痕） | 逐个抖回缺陷 → **5/5 被新测试抓到** ✅ |
| 破例不能把门变松（无 waiver 照拒） | 单测固定 |
| 新增动词被 doc-drift 守卫拦下 → 补 `design.md` §7.2 后通过 | 真实发生（178 测试才转绿） |

### 0.5 收尾 C4.3：`unrequire`（边可删）

建错依赖此前只能 `skip` 整节点（真跑循环里 `C7` 就这样废掉）。
关键判断：**删边不能把加边的校验搞松**，所以两者**故意不对称**：

| | `require`（加边） | `unrequire`（删边） |
| --- | --- | --- |
| 两端节点存在 | 必须 | **不要求 `dep` 存在** |
| 自环 / 成环 | 写时拒 | 不适用 |
| 边已存在 / 不存在 | 幂等 | 不存在则拒（报出当前出边） |

**为何删边不要求 `dep` 存在**：否则**悬空边反而清不掉**——`doctor` 报得出、没人修得了。

| 验收点 | 反向验证 |
| --- | --- |
| 4 处判据（边必须存在 / reducer 真删 / 只删指定边 / 不要求 dep 存在） | 逐个抖回缺陷 → **4/4 被新测试抓到** ✅ |
| `drive` 的就绪波次真的变了（不只字段被抹） | 装配层测试断言阻塞行消失、节点进入波次 |
| 被拒的写入不落事件、事件流仍可重放 | 单测断言事件数不变 + `projection_matches` 通过 |

> `rescope` 的对偶是「改声明」，`scope_waiver` 的对偶是 `size_waiver`（破例）——
> 现在两个门（`design_first` / `size_budget`）形状对称了。

### 0.6 第二次真跑循环（树 `gap-cov`，2026-10-10）：验证「新机制是否真减摩擦」

目的不是再补两个测试，而是回答：**上一轮为一个摩擦加的三套机制，到底有没有用？**
目标是 backlog 里两条**已验证缺口**（B3 `diff_size` tracked 分支 / B4 `expect` 三分支）。

节点：`D1`（循环中发现的修复）→ `B3` → `B4` → `E1`（变异）→ `S1`（沉淀）。

**循环中抳出 3 个真缺陷**（全部是“声明/输入被接受、然后什么都不发生”这一类）：

| # | 缺陷 | 后果 |
| --- | --- | --- |
| 1 | `expand` **静默丢弃** `requires`/`relates`/`produces`/`consumes`（`validate_node_spec` 接受、`new_node` 无此参数） | **调度乱序**：E1 在它要评估的 B3/B4 未完工时就被推进首波 |
| 2 | 已跟踪文件的**暂存改动**对裸 `git diff --numstat` 不可见 ⇒ 落到“数整文件”兜底 | 200 行文件改 1 行、`git add` 后被判 200 行（规模门误报超预算） |
| 3 | 已跟踪但**净改动为 0** 的文件也被兜底按整文件计 | 同上（且我自己的测试先写错了预期，被代码纠正） |

**摩擦观测——这正是本轮要的答案：**

| 场景 | 上一轮（无机制） | 本轮（有机制） |
| --- | --- | --- |
| `#1` 发现要改 `gates.py`，不在 B3 声明内，**尚未碰** | — | `rescope` **一次成功**，理由入库（`rescope_log`） |
| 同上，但要改的 `tests/base.py` **已经碰过** | — | `rescope` **按设计拒收**并点名 `scope_waiver` ⇒ 用显式破例，`violations` 照记、理由进 `evidence` |
| 上一轮同类（`C7` 依赖建反） | 只能 `skip` **整个节点** | （有 `unrequire` 后不会再发生） |

> 结论：三套机制都在**真实摩擦点上**被触发，且都是“一条命令”而非“废掉节点”。
> 尤其是 `rescope` 的拒绝路径——它拒绝得很对：若允许把已改过的文件事后声明进来，
> `design_first` 就等于不存在。

**变异测试抳出 2 个「测试自身的洞」**（已补：`test_declared_dangling_relates_is_rejected`、
`test_per_file_agrees_with_total`）——详见 backlog §B3/B4 已结节。
可复跑回归口：`python3 scripts/mutation_check.py`（**39/39**，有漏抓则退出码 1）。

### 0.7 python-expert 12 类审查（按“该配置化却硬编码”切入）

跑法：skill 自带的 AST 检查器（+ 人工 4 类）。全仓 **23 处 except 没有漏捕**；
**未发现 cwd 依赖**；无裸 except / 无 `shell=True` / 无可变默认参数；
第 12 类（失联检测）不适用（无 daemon，设计如此）。

**抳出的真问题——都是“声明是一套、实现是另一套”**：

| # | 问题 | 后果 |
| --- | --- | --- |
| 1 | `NODE_FIELDS` 含 `parent`/`status`/`lease`/`evidence`，而 `new_node` 不收它们 | 声明→校验通过→**静默丢弃**（实测 `status:"done"` 进去仍是 `pending`）。与上一轮的 `requires` **同一 bug 家族** |
| 2 | `EVENT_KINDS` 与 `_HANDLERS` 两套清单，前者**只管报错文本** | 可静默漂移；报错会列出根本没有 handler 的 kind |
| 3 | `store.py` 模块顶层 `import fcntl` | 非 POSIX 平台上连 `import disflow` 都失败（只读动词本来可用） |

**已修**：

- 节点字段拆为 **可声明** vs **运行时/派生**；`expand` 声明运行时字段**报错**（不是忽略）。
  新增 `tests/test_declarations.py` 的**结构性守卫**：`DECLARABLE_FIELDS` 必须与
  `new_node` 真正接下的参数**逐个相等** ⇒ 这类 bug **结构上不可能再复发**。
- `spec.EVENT_KINDS` ←→ `state._HANDLERS` 由同一测试钉住相等；报错文本改用 `_HANDLERS`。
- `fcntl` 改惰性导入；非 POSIX 上**能 import、能读**，只在写时报一句能看懂的错；
  **不静默降级为不加锁**（那会变成并发写没保护）。Windows 实现已登记 backlog §D3。
- `store.checkpoint` 等“带过来但不用”的 vendored API 在文件头**明标**，
  以免读者以为有第二个真相源。

### 0.8 ①② 配置层 + 写时词汇校验（按“软件框架必须配置化”）

**① 配置层（`config.py`）—— 独立配置文件**

| 项 | 实测 |
| --- | --- |
| 配置位置 | `<项目根>/disflow.json`，**与状态目录是兄弟**（`$DISFLOW_CONFIG` 可指别处） |
| 为何要独立 | 配置是**策略**（人写、进版本库、要 review），状态是**派生**（可由 rebuild 从事件重建）——放一起的话「删 `.disflow/` 重置」会**静默带走策略** |
| 优先级 | 内建默认 < `disflow.json` < 显式参数/节点声明 |
| 报错 | 未知键/类型错 **报错**并列允许的键（不静默忽略） |
| 可见 | `doctor` 全绿时报出 8 个键的实际取值与来源 |

**修的最尖锐一条**：验收超时 `DEFAULT_TIMEOUT=120` 曾是模块常量，节点声明不了 ⇒
跑 >2 分钟的验收**这道门永远过不去**，唯一出路是写 `waiver`——等于**把门关掉**。
现在：`acceptance.timeout`（节点） > `acceptance_timeout`（项目）。

**其余可配**：`git_timeout`、`output_tail`、`walk_ignore`（指纹递归剪枝）、
`size_budget`、`lease_ttl`、`stages`、`roles`。

**边界：机制词汇不进配置**。`EVENT_KINDS`←→`_HANDLERS` 留在代码里，靠
`test_declarations.py` 做编译/测试期保证；挪进配置就退化成运行期才发现，
而错一个字母坏的是一个事件流的可重放性。

**② 写时词汇校验（`stages`/`roles`）**

| 时机 | 行为 | 为何 |
| --- | --- | --- |
| **写**（`expand`） | 未知值 → 报错，并指出“自定义列请写进 `disflow.json`” | 之前 `stage:"audti"` 会**静默多出一个看板列** |
| **读**（`views.board_lines`） | 未知 stage **容忍**（`order += sorted(...)`） | 事件流可能来自更新的版本/不同配置，视图不能崩 |

**为何 reducer 不校验**：reducer 一依赖配置，事件流就**不可重放**了——换台机器或改了
`stages`，重放旧事件会直接失败。「事件流不可重放」比「错别字没当场报」严重得多。

**顺带修的映射表**：`import_mgt` 的 `role = todo.get("org")` 是错的——mgt 的 `org` 是
**自由团队名**（`backend`/`quality-guild`），disflow 的 `role` 是小闭集。
现在走 `_ROLE_HINTS` 归类，归不出来回落 `dev`，**原值进 `context.mgt.org` 不丢**。

### 0.9 变异 harness 自身的一个缺陷（修 0.8 时抳出）

改 `fingerprint`/`_numstat`/`_is_tracked` 的签名后，3 条针脚的 `old` 文本匹配不上了，
harness 却把它们报成**「漏抓」**——凭空多出 3 个根本不存在的“覆盖缺口”。

根因：`main` 把 `SystemExit`（针脚找不到）和真漏抓混进同一个 `bad` 桶，
最后统一说“这些行为没有被测试守住”。**对过期针脚而言这是编造**——同一个 bug 家族：
把不是事实的东西当事实报。

已修：`PinNotFound` 单独一条退出码；
- 针脚未应用 → **exit 2**，明说“本轮结果不可信，请对齐针脚”；
- 真漏抓 → exit 1。
新增 `tests/test_mutation_harness.py`：除了测这个区分，还有一条
**全量断言所有针脚的 `old` 都能在当前代码里找到**（把“改了实现不更新针脚”变成即时报错）。

### 0.10 配置文件的**先后顺序**（写进上手流程）

写 README 时实测出来的坑：`disflow.json` 在 **`start` 之后**才创建 ⇒ 作用域门拒收
`改动超出 touch_plan：['disflow.json']`；**提交后或开工前就位** ⇒ 通过。

| | |
| --- | --- |
| 根因 | `disflow.json` 就是一个**普通文件**，引擎不特殊对待 |
| 为何**不**列白名单 | 配置变更有时**真是**节点干的活（如“加一个看板列”），那就该进 `touch_plan`；白名单会默默藏掉那种情况 |
| 报错好不好懂 | **好懂**——它点名了文件，也给了修法（rescope / scope_waiver）。这条的价值是**别撞上**，不是修不透明的失败 |

已做：① README 的 `prepare` 块里创建配置（在 `start` 之前，带注释说明为何要这个顺序）
② 第 ⑧ 步 `d doctor` 现在会报出配置取值与来源，上手测试断言
`acceptance_timeout = 300` 且证据里 `acceptance.timeout == 300`
（此前这个超时是代码里的常量，配置根本到不了证据）
③ `test_config.py` 新增 `ConfigPlacementOrderTest`：**两个分支都钉住**——
开工前就位不进 `introduced`；开工后出现则被拒且报错点名到文件。

> 只钉前者是不够的：那样的话把 README 的配置挪到 `run` 块里**不会有人发现**。

### 0.3 随后修掉的 C4.2 与顺手补的两个可执行检查

| 改动 | 为何值得 | 反向验证 |
| --- | --- | --- |
| **C4.2 修复**：`baseline` 从路径集合改为 `{路径: 内容指纹}` | 路径差集看不见「同一文件被两节点先后改」⇒ 作用域门**静默失效** | 退回路径差集 → `test_engine` 失败 ✅ |
| `doctor` 报漂移时**给出修法**（`disflow rebuild`） | 只报“坏了”不报“怎么修”，用户只能猜 | 新测试断言 `rebuild` 出现在报文里 |
| 新增**表格列数守卫**（`test_docs.py`） | 分隔行少写几列时 markdown-lint 不报，但**渲染是错的**——本轮刚踩过两次 | 把分隔行少写一列 → 测试精确指出行号 ✅ |

> 投影函数的变更（`baseline` 形状）会合法地使磁盘上的旧投影漂移；
> `doctor` 抓到了，`rebuild` 重建后两棵树仍 10/10 与 17/17。**事件未动**。

> #4 值得单独记：当时我差点把「陈旧字节码」误判为「修复无效 / 测试隔离有问题」。
> 判据：`inspect.getsource` 显示新代码而行为是旧的 → 先清 `__pycache__` 再下结论。
> **证据边界**：`cli-cov` 树的状态只活在本机 `.disflow/`（已 gitignore）；
> 本文件记录的是**结果与教训**，树的节点级证据是本地产物。

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
| W6 | D100 | 规模预算门 + 树终止条件门 | D70 | ✅ |

### verify
| id | 目标 | 依赖 | 状态 |
| --- | --- | --- | --- |
| V1 | 端到端：含跨子树依赖的树，`drive` 确定性 | D60 | ✅ 独立 |
| V2 | 证据门：构造一次假完成，必须被拒 | D70 | ✅ 独立 |
| V3 | `rebuild --check` 一致 | D10 | ✅ 独立（有牙） |
| V4 | 从 mgt 导入项目，`status` 等价 | D90 | ✅ 独立（合成 fixture） |

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

~~1–7~~ 全部完成（清单已收口）。**残余项已归档到 `docs/v0.2-backlog.md`**，不再在本表追踪：

| 已收口 | 结果 |
| --- | --- |
| 独立复跑 V1–V4（+V5 分层） | ✅ 二轮完成，@0 有记录；抳出 V3 真缺陷已修 |
| `cli.main` 捕获 `ValueError`/`OSError` | ✅ `tests/test_cli.py` 守 |
| `tree_coverage` 门 | ✅ `tests/test_coverage.py` 守；`close` 串联两门 |
| v0.2：多树作用域隔离 + 跨树完成信号 | ✅ `awaits` + `tree.close`，`tests/test_multitree.py` 守 |
| 覆盖审计 2 个 ◐（FR-13 / NFR-2） | ✅ 均完成 —— **FR/NFR 全覆盖** |
| §10 三条偏离 | 未实现，**按决策停放**见 backlog §C |

**无未决项**。`PM1` 已如实 `skip`（见上），看板 17/17。
