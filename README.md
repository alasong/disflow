# disflow

任务分发流引擎：**看板是一等实体**（固定列结构、空也在；树/图是板内维度），分发器只推不跑。

## 5 分钟上手：看它自己排顺序，并拒收一次假的完成

零第三方依赖（只用 Python 标准库）。在**一个空目录**里试，不碰你自己的仓库。

```bash
cd "$(mktemp -d)"                              # 一个临时目录
export PYTHONPATH=/改成你 clone 的 disflow 路径
export DISFLOW_STATE_DIR="$PWD/.disflow"       # 仓内 state dir：上手够用，但 worktree 会被硬拒（见「量化、迭代、worktree」）
```

**准备**——注意两点：`.gitignore` 与**配置文件**。状态目录必须被忽略，否则它会以
「未跟踪目录」的身份被改动归因当成“本节点引入的改动”。配置文件（可选）必须
**在第一次 `start` 之前**就位（或提交进仓库）——`start` 会拍下基线快照，
之后才出现的文件会被作用域门算成“本节点引入的改动”（实测：会报
`改动超出 touch_plan：['disflow.json']`）。

```bash
# disflow-walkthrough:prepare
git init -q .
printf '.disflow/\n' > .gitignore
printf 'Hello\n' > greeting.txt

# 配置是**独立文件**（不在 .disflow/ 里）：策略该进版本库、可 review，
# 而状态目录删掉重置是合法动作——配置不该被那个动作带走。
cat > disflow.json <<'JSON'
{
  "acceptance_timeout": 300
}
JSON

# 看板定义另住一个文件（板 = 阶段 = 节点 stage 的取值闭集）：
# 它会长大（动态建板），变更节奏也与引擎参数不同，所以不塞进 disflow.json。
cat > boards.json <<'JSON'
{
  "boards": [
    {"id": "req", "label": "需求"},
    {"id": "design", "label": "设计"},
    {"id": "dev", "label": "开发"},
    {"id": "verify", "label": "验证"}
  ]
}
JSON
```

**跑一遍**（下面的输出都是**真实输出**，照抄就能得到同样的结果）：

```bash
# disflow-walkthrough:run
d() { python3 -m disflow.cli "$@"; }        # 后面都用 d 代替那个长命令

cat > demo-children.json <<'JSON'
[
 {"id":"A","goal":"改 greeting.txt","touch_plan":["greeting.txt"],
  "acceptance":{"cmd":"python3 -c \"print(open('greeting.txt').read().strip())\"","expect":"contains:Hi"}},
 {"id":"B","goal":"补一条测试","touch_plan":["test_greeting.py"],"requires":["A"],
  "acceptance":{"cmd":"python3 -m unittest test_greeting -q","expect":"exit0"}}
]
JSON

# ① 建树：只声明“谁依赖谁”，不写顺序
d init demo --goal "把 greeting 从 Hello 改成 Hi，并补一条测试"
d expand demo root --children "$(cat demo-children.json)"

# ② 它自己算出首波（B 被 A 阻塞）
d drive demo

# ③ 这就是派给 worker 的全部契约（写面 + 验收命令）
d brief demo A

# ④ 先交一次**假的**完成：什么都没改就 done
#    （宿主循环契约：done 前先 collect 落盘 worker 结果；无它 CLI 直接拒收）
d start demo A --holder me
d collect demo A --result '{"status":"ok","summary":"假装做完了","changed_files":[]}'
d done demo A

# ⑤ 真把活干了，再 collect + done
printf 'Hi\n' > greeting.txt
d collect demo A --result '{"status":"ok","summary":"greeting 改成 Hi","changed_files":["greeting.txt"]}'
d done demo A

# ⑥ 依赖自动解除
d drive demo

# ⑦ 三视图同源（同一投影的不同投影面）
d board demo
d graph demo

# ⑧ 关树：先看它要什么（收益验证 + 经验沉淀）
d close demo
d context demo root --set '{"closure":"not_applicable: 演示仓，不跑收益验证与沉淀"}'
d close demo                                    # 逃生口给了具体理由就放行
d trees
d doctor                                        # 自检全绿
```

四件值得看的事：

| 看什么 | 在哪一步 |
| --- | --- |
| **顺序由它算**：我只说了 `B requires A`，`drive` 只把 A 放进首波 | ② |
| **门会拒收假的完成**：验收不匹配就 `done` → 拒收（退出码非 0），理由逐条列出；若只是「没改任何东西」，门只给 ⚠ 空洞通过警告、仍 exit 0，不拒收 | ④ |
| **证据引擎自算**：`done` 后 `introduced` / `size` / `acceptance` 由引擎采集，不是手抄 | ⑤ |
| **看板/树/图不是第二份存储**：它们都读同一份投影（看板 = 阶段列 × 列内树层级 × 状态分组）；宿主**运行视图**（`.agents/skills/disflow/lanes_watch.sh`）用它当骨架、复用**同一布局**，再叠加 pi 运行目录里的**实时进展** | ⑦ |
| **配置是独立文件且看得见**：`doctor` 报出实际取值与配置文件路径 | ⑧ |

两个视图各答一半（**数据源分离、布局同源**）：`board` 是**纯 disflow 事实**的一次性快照
（阶段列 + 列内树层级 + 状态分组，含 pending/skipped/failed）；`lanes_watch.sh` 以**同一棵树的
全部节点**为条目（一列一块，按 `stage` 归列），再把宿主 pi 运行目录
（lane 名 = 节点 id）join 上来，在**在跑**节点上就地显示实时进展（`◈` 目标 / `⌘` 最后命令 /
`↳` worker 自报进展）。`board` 不知道 worker 在跑哪条命令，pi 不知道树的结构 —— 所以是**两个**；
区分依据是**数据源与刷新节拍**，不是布局（布局只有 `views.board_layout` 一处实现）。

人读态**屏幕维度**的两条：

- **终态行不打 goal**：`✓ 完成` / `— 跳过` 行只给 `标记 + id + 状态`（一行几百字没法扫）；其余行
goal 截断到 80 字（`views.truncate_goal` **唯一实现**）；**完整 goal 在 `board --json`**。看板默认只列运行中 + 待完成（终态与空列在表头两行报数，`--all` / `--all-columns` 展开）；运行视图**按阶段列**分别列运行中 + 待完成（`--all` 连终态）。
- **默认刷新像 top**：`lanes_watch.sh` 每轮**清屏再画**（`\033[H\033[2J` + 整屏重绘），屏幕只有一份；
**代价是没有回滚**（要看历史用 `once`：零控制序列、可直接重定向）；`--all` 连终态、`-v` 给命令与自报进展。

第 ④ 步的真实输出：

```text
✗ 门拒收 A：
  - 验收未通过：exit=0；期望包含 'Hi'
契约片段：
{ ... "touch_plan": ["greeting.txt"], "size_budget": 400, ... }
```

第 ⑤ 步过后，节点证据长这样（`disflow status demo --json` 或直接读 `$DISFLOW_STATE_DIR/state.json`）：

```text
introduced = ['greeting.txt']      # 只归因本节点开工后**内容真的变了**的文件
size       = 1 行                   # 相对最后一次提交的净改动
vacuous    = {}                     # 没有“空洞通过”的门
```

> `vacuous` 为空表示两道门**真的验到了东西**；若它为 `design_first`/`size_budget`，
> 说明那些门没东西可验（“门全过” ≠ “门都验了”）——见 `docs/design.md` §7.5。

关树时若直接 `d close demo` 会先被拒——它要求树里有**收益验证**与**经验沉淀**，
或者树根声明一个带理由的逃生口（`design.md` §7）。上面的第 ⑧ 步把两个分支都走了。

想用它在**真活**上驱动一个循环（我跑过两遍的那个），配方见
`docs/dev-board.md` §0.1 与 §0.6：`drive` → `brief` → **`start`** → 干活 → `collect` → `done`。
循环里两条别抄漏：**每轮先 `start`**（领租约 + 拍开工基线，不 start 门就在空基线上跑）；
**worker 不许自己 `git commit`，提交由宿主统一做**（worker 自己提交会让门看不见改动——见 `.agents/skills/disflow/SKILL.md` §3/§5）。

## 量化、迭代、worktree

三个动词，各对应一类「声明 vs 派生」的边界。

**量化**：点数由**人**在 `expand` 时声明（`points` 是唯一入口，引擎不猜）：

```bash
python3 -m disflow.cli expand demo root --children '[{"id":"a","goal":"做A","points":3,"acceptance":{"cmd":"true","expect":"exit0"}}]'
python3 -m disflow.cli metrics demo            # 点数/已完成/剩 + 速度 + 预计完工 + 燃尽
python3 -m disflow.cli metrics demo --json     # 机器可读（summary + burndown）
```

**没给 `points` 的节点不进任何分母**——`metrics` 报「未声明」而**不是**替它编一个 0
（把「没估」当 0 会把速度抬高、预计完工压低）。`points` 是人的**声明**，`velocity` / `burndown`
是**派生**；派生只吃事件流里的完工时刻，**不读时钟**（同一份事件在任何时刻、任何机器都算出同一个数）。

**迭代**：`deadline` 是树级声明，用来给燃尽图一条理想线：

```bash
python3 -m disflow.cli deadline demo 2026-12-31   # 设（回显剩余天数）
python3 -m disflow.cli deadline demo -            # 清空
```

它是**声明不是闸门**：逾期**不拒任何动词**，只是被显式报出来（`metrics` / `trees` 里会写
「逾期 N 天」）。非法日期在**写入时**就响亮拒收（退出码非 0、不落事件），不会静默当成「没有截止」。

**worktree**：每节点一个独立 git 工作区（`<state dir>/worktrees/<tree>/<node>`），
把并发 worker 从物理上隔开：

```bash
python3 -m disflow.cli worktree plan demo         # 只给计划，零副作用
python3 -m disflow.cli worktree apply demo        # 真建（可逆、幂等；跑两次结果一样）
python3 -m disflow.cli worktree list demo         # 对账：leak / missing / orphan / residual
python3 -m disflow.cli worktree reap demo         # 只打印命令，**不执行**
```

`apply`（**创建**）真跑 git——创建可逆、错了能删了重来；`reap`（**删除**）是**纯计划**：
只列出判据、理由、以及宿主该跑的命令，**永不执行**。分界不是「一律不执行」，而是**可逆 vs 不可逆**——
删除会连带丢掉未提交改动、被 `.gitignore` 忽略的文件、未合并的提交，是唯一不可逆动作，
所以引擎不替人删数据。宿主读完计划后自己跑那两条命令，**顺序要紧：先删工作区、再删分支**
（工作区还在时 git 会拒删它占用的分支）；分支一律用 `-d`（安全删），`-D` 会制造不可达提交。

> **限制（上手例里就踩得到）**：上面「5 分钟上手」把 `DISFLOW_STATE_DIR` 放在示例仓**内部**
> （`$PWD/.disflow`）——这个布局下 `worktree` **不可用**：worktree 落点在仓内会被硬拒（在仓内
> 会污染 `git status`，且一次 `git clean -xdf` 会把状态目录连同全部 worktree 一起删掉）。
> 要 `worktree` 就把 state dir 配到仓外（`[state].dir` 或 `DISFLOW_STATE_DIR`）；`doctor` 会
> **主动**报出这条（「需人判断」，不计入退出码）。

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
- **看板是一等实体**（固定列结构、空也在；板定义随事件流持久化）；树/图是板内维度；多块板 = 多棵作用域隔离的树。
- **引擎只推不跑**，无常驻 daemon。

## 用法

```bash
export DISFLOW_STATE_DIR=.disflow
python3 -m disflow.cli init demo --goal "目标"
python3 -m disflow.cli expand demo root --children '[{"id":"a","goal":"做A","acceptance":{"cmd":"true","expect":"exit0"}}]'
python3 -m disflow.cli require demo b a          # 建图（写时拒环）
python3 -m disflow.cli unrequire demo b a        # 建错了就删（删边不要求对端存在）
python3 -m disflow.cli relate demo b a           # 信息边（不参与就绪判定）
python3 -m disflow.cli unrelate demo b a         # 信息边建错了也删得掉
python3 -m disflow.cli drive demo                # 只推不跑：就绪波次 + brief 命令
python3 -m disflow.cli brief demo a              # worker 的完整输入契约
python3 -m disflow.cli start demo a --holder w1
python3 -m disflow.cli collect demo a --result '{"status":"ok","summary":"…","changed_files":[]}'
python3 -m disflow.cli done demo a --decl '{"note":"…"}'   # 跑门；无已落盘 collect ⇒ 拒收（--no-result 显式破例）

# 三种视图（同一投影）
python3 -m disflow.cli board demo          # 按 stage 列 + 列内树层级（默认只列运行中/待完成；
                                          #   --all 展开终态 · --all-columns 铺空列 · --json 全量事实）
python3 -m disflow.cli board demo --by board   # **四块板**：一个阶段一块板（板头报上游完成度）；
                                               #   与 --stage 互斥 · 空板恒铺（结构事实，不折叠）
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

## 配置（两个独立文件）

零配置可用；要改则放独立文件在**项目根**（与状态目录是兄弟，不在它里面）。
**两个文件互不为前提**——只有其中一个也生效，两个都没有就全用内建默认。

```jsonc
// <项目根>/disflow.json —— 引擎参数（超时/预算/租约/忽略集/负责方）
{
  "acceptance_timeout": 300,          // 验收默认超时（秒）；节点可声明 acceptance.timeout 覆盖
  "size_budget": 400,                 // 单节点改动行数预算；节点可声明 context.size_budget
  "lease_ttl": 900,                   // 租约默认秒数；start --ttl 可覆盖
  "git_timeout": 20,                  // git 探测超时（大仓调大）
  "output_tail": 2000,                // 证据里保留的输出尾部字符数
  "walk_ignore": ["node_modules"],    // 指纹递归目录时跳过的名字
  "roles": ["dev", "qa", "pm"],       // 负责方（写时校验）
  "question_policy": { }              // 提问纪律（写进 worker 契约；形状见 spec.py）
}
```

```jsonc
// <项目根>/boards.json —— 看板定义（板 id 即节点 stage 的取值闭集）
{
  "boards": [
    {"id": "req",    "label": "需求"},   // 数组序 = 板的先后（不另设 order 字段）
    {"id": "design", "label": "设计"},
    {"id": "dev",    "label": "开发"},
    {"id": "verify", "label": "验证"}
  ]
}
```

`boards.json` 也可写成纯字符串数组（`{"boards": ["req", "dev"]}`，label 回落成 id）。
条目可选带**看板策略**（看板是一等实体，策略是它的附属）：`"wip": 2`（逐列并发在跑上限，
分发策略 FR-30）、`"acceptance": {"cmd": …, "expect": …}`（逐列默认验收，节点未自带时派生，FR-31）。
`done` **不是板**：它是节点**状态**（`core.TERMINAL`），写进去会被响亮拒收。
板序第一块 = **流程入口**（root 容器落的板）；任务没声明 `stage` 时落 `dev`（是板集里的一块），
没有 `dev` 就落入口板。要精确控制就显式声明 `stage`。

路径可分别用 `DISFLOW_CONFIG` / `DISFLOW_BOARDS` 指到别处。**为什么必须是独立文件**：
配置是**策略**（人写、要版本化、要 review），状态是**派生**（可从事件重建）——放一起的话，
「删掉 `.disflow/` 重置」这个合法动作会**静默带走策略**。板定义再单独一个文件的理由：
它会长大（动态建板），变更节奏与引擎参数不同，混在一起 review 看不清改的是哪件事。

`disflow.json` 的 `stages` 键**已退役**（板定义搬到 `boards.json`）：写了会**响亮报错**
并给迁移指引，**不静默回落** —— 回落就是「同一件事两处定义」，用户会以为配置生效了。

**先后顺序（会撞上）**：这两个文件都是**普通文件**，引擎不特殊对待它们。
`start` 会拍下基线快照，所以它们得**在第一次 `start` 之前**就位（或提交进仓）——
否则作用域门会把它们算成“这个节点引入的改动”（实测：`改动超出 touch_plan：['disflow.json']`）。
之所以不列入白名单：配置变更有时**真是**节点干的活，那就该进 `touch_plan`；
白名单会默默藏掉那种情况。

`disflow doctor` 会把每个键的实际取值**与来源**列出来（两个文件了，来源不再唯一）：

```text
配置（逐键报来源）：
  /path/boards.json
    boards = req=需求 · design=设计 · dev=开发 · verify=验证
  /path/disflow.json
    acceptance_timeout = 300.0
    ...
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
python3 -m unittest discover -s tests -t tests    # 774 passed（零第三方依赖）——收尾全量，保留可用
python3 scripts/finish.py                          # 收尾门：全量 + 资产 scan/check（退出码当门）
python3 scripts/test_changed.py --all --jobs 8    # **收尾推荐**：全量 + 并发（96s→33s，2.9×）
python3 scripts/test_changed.py                   # 迭代期：只跑改动相关的子集（带「这不是全量」横幅）
python3 scripts/test_changed.py --jobs 8          # 迭代期子集 + 并发
python3 scripts/mutation_check.py                 # 变异测试：64/64（验证测试真的有牙）
```

> 迭代期不必每次跑全量：`scripts/test_changed.py` 读改动文件（与门同源：`git status` ∪
> `git diff --name-only <base>`）→ 按**显式判据**映射到相关测试子集 —— ① 命名约定
> `tests/test_<模块>.py`；② grep 全库测试文件里对该模块的文本引用；③ 一组固定元测试
> （`test_docs` / `test_layering` / `test_declarations` / `test_typecheck`）。
> 它**默认不是全量**：打印「覆盖 N / 全库 M / 未覆盖 K（列名）」并提示「收尾必须跑全量」；
> 拿不准或收尾时用 `python3 scripts/test_changed.py --all`（**逐字等价**于上面那条 discover）。
> 映射的守卫在 `tests/test_test_changed.py`：若映射退化成「只跑同名文件」，守卫当场变红。

> 上面那段「5 分钟上手」本身也被测试盯着：`tests/test_readme_walkthrough.py` 会
> **抽取 README 里标记的代码块并真的执行**，再断言本文声明的结果。
> 改文档时若命令漂了，测试会红——不然“照抄可跑”只是一句声称。

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
  config.py       # 项目级配置（独立文件 disflow.json；响亮报错）
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
