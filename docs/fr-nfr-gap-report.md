# FR / NFR 缺口与机检报告（`docs/requirements.md` §3–§7）

> 由 `scripts/fr_check.py` 生成（`python3 scripts/fr_check.py --report`）。
> 本条报告的头等产出**不是通过率**，而是把 **[未实现]** 与 **[不可机检]** 诚实分出来。

## 总表（29 FR + 7 NFR = 36 条）

| # | 一句话验收标准 | 证据类型 | 证据（可复制命令 / 测试名） |
| --- | --- | --- | --- |
| FR-1 | 状态来自 append-only 事件流 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-1]` 行 |
| FR-2 | rebuild --check 逐字节一致 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-2]` 行 |
| FR-3 | 投影原子替换 + flock 真互斥 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-3]` 行 |
| FR-4 | 每节点至多一个 parent + 祖先链查询 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-4]` 行 |
| FR-5 | 无 contains 边（层级只由 parent 定义） | [机检] | `python3 scripts/fr_check.py` 的 `[FR-5]` 行 |
| FR-6 | stage 与 role 独立 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-6]` 行 |
| FR-7 | context 祖先链投影有限且确定 | [测试] | tests/test_core.py::ContextTest::test_inherited_context_near_overrides_far |
| FR-8 | requires 必须 DAG，写时拒环 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-8]` 行 |
| FR-9 | relates 软边不影响就绪 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-9]` 行 |
| FR-10 | consumes 指向未登记项即拒 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-10]` 行 |
| FR-11 | 唯一 ready(n) 实现 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-11]` 行 |
| FR-12 | ready(n) 覆盖 pending/分型/requires/租约/写面 | [测试] | tests/test_core.py::ReadyTest（test_ready_requires_deps_done / test_container_never_ready / test_lease_blocks_ready） |
| FR-13 | 就绪结果可 --dry-run 输出 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-13]` 行 |
| FR-14 | drive 只推不跑、不 spawn | [机检] | `python3 scripts/fr_check.py` 的 `[FR-14]` 行 |
| FR-15 | brief 写全 + collect 不写 node-done | [机检] | `python3 scripts/fr_check.py` 的 `[FR-15]` 行 |
| FR-16 | 引擎不内置常驻进程 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-16]` 行 |
| FR-17 | 触发方式支持 cron / 事件 / 显式三种，引擎自身不常驻 | [不可机检] | 见下节 |
| FR-18 | done 前必须过验收门 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-18]` 行 |
| FR-19 | 证据元数据由引擎自算 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-19]` 行 |
| FR-20 | 拒收给缺项清单 + 契约片段 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-20]` 行 |
| FR-21 | 三视图同源、不各存一份 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-21]` 行 |
| FR-22 | 多板 = 多棵树，隔离 | [测试] | tests/test_multitree.py::MultiTreeTest::test_trees_are_isolated |
| FR-23 | 跨树依赖降级为 awaits 显式信号 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-23]` 行 |
| FR-24 | brief = 祖先链 context + 前置摘要 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-24]` 行 |
| FR-25 | 同波写面重叠检查、重叠排队 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-25]` 行 |
| FR-26 | 单一写者收口 + 租约防重 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-26]` 行 |
| FR-27 | status 能识别卡死 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-27]` 行 |
| FR-28 | import mgt 从 state/registry 读入 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-28]` 行 |
| FR-29 | 映射关系写进 design.md §10 | [机检] | `python3 scripts/fr_check.py` 的 `[FR-29]` 行 |
| NFR-1 | 零第三方依赖 | [机检] | `python3 scripts/fr_check.py` 的 `[NFR-1]` 行 |
| NFR-2 | 核心语言中立（业务词不进 core） | [测试] | tests/test_layering.py::LayeringTest::test_core_has_no_business_nouns |
| NFR-3 | 文件式：无服务、无数据库 | [机检] | `python3 scripts/fr_check.py` 的 `[NFR-3]` 行 |
| NFR-4 | 可测试：就绪/依赖/门是纯函数，可单测 | [测试] | tests/test_core.py::ReadyTest、tests/test_gates.py::ExpectSemanticsTest |
| NFR-5 | 可断点续跑：崩溃后由事件流恢复 | [测试] | tests/test_store.py::StoreTest::test_events_are_append_only_and_replayable |
| NFR-6 | 规模预算，超预算即拒 | [机检] | `python3 scripts/fr_check.py` 的 `[NFR-6]` 行 |
| NFR-7 | 不静默降级 | [机检] | `python3 scripts/fr_check.py` 的 `[NFR-7]` 行 |

`[机检]` 共 29 条，全部真跑并打印原始观测；`[测试]` 6 条；`[未实现]` 0 条；`[不可机检]` 1 条；另有 `[◐ 部分缺口]` 3 条。

## [未实现]（实现里找不到对应能力——最有价值的输出）

**（空）**：29 FR + 7 NFR 里没有整条未实现的。但下节有 3 条 **◐ 部分缺口**（整条机检通过、子句缺失）。

## [不可机检]（语义判断，不冒充通过）

- **FR-17**：触发方式支持 cron / 事件 / 显式三种，引擎自身不常驻
  - 为什么不可机检：「支持三种触发方式」是**架构判断**：没有一条命令能判定「够不够支持」。可观察的相关事实是 disflow/ 内无内置调度器（`daemon|cron` 命中 0，这条由 FR-16 机检），且 design.md §7 把 cron 明确列为**宿主外部**（`外部 cron | drive + 派发`）。「引擎自身不常驻」那半句是机检的（FR-16），但整条 FR-17 无法机检。

## [◐ 部分缺口]（整条机检通过，但子句在实现里找不到）

- **FR-15 / 提问纪律**
  - 缺什么：brief 的载荷里没有「提问纪律」相关字段（全仓 `提问`/`question` 于 disflow/ 命中 0）。brief 现含 goal/context/from_requires/acceptance/touch_plan，collect 校验结果契约，但「提问纪律」这条子句无对应实现。
  - 若要做，验收点：`brief` 载荷加 `question_policy`（文本或结构化），并有测试断言它出现在 brief 输出里。
- **FR-17 / cron / 事件触发（内置）**
  - 缺什么：disflow/ 内无 cron/事件触发实现（命中 0）；design.md 明确把它列为宿主外部。
  - 若要做，验收点：加 `disflow drive --watch`/触发器适配层——但这会与 FR-16「不常驻」冲突，须先决定边界。
- **FR-22 / 每棵树一个权威分发者**
  - 缺什么：没有「分发者身份」概念，也没有对「同树多分发者」的检测；`flock` 只串行化写入。
  - 若要做，验收点：给 `drive`/`start` 记分发者身份并在 doctor 里报「同树多分发者」。

## [测试]（只有测试覆盖、没有独立命令）

- **FR-7**：context 祖先链投影有限且确定
  - 测试：`tests/test_core.py::ContextTest::test_inherited_context_near_overrides_far`
  - 为什么不可独立机检：「有限且确定」是对一条祖先链的**行为**断言，grep 只能证函数在场，证不了确定性。
- **FR-12**：ready(n) 覆盖 pending/分型/requires/租约/写面
  - 测试：`tests/test_core.py::ReadyTest（test_ready_requires_deps_done / test_container_never_ready / test_lease_blocks_ready）`
  - 为什么不可独立机检：组合条件只能在具体节点图上跑；且「本波写面不重叠」实现在 `dispatch.pack_waves`、**不在** `is_ready` 内（见报告 ◐ 部分缺口）。
- **FR-22**：多板 = 多棵树，隔离
  - 测试：`tests/test_multitree.py::MultiTreeTest::test_trees_are_isolated`
  - 为什么不可独立机检：隔离是两棵树并存时的行为，无单条 grep 能判定。
- **NFR-2**：核心语言中立（业务词不进 core）
  - 测试：`tests/test_layering.py::LayeringTest::test_core_has_no_business_nouns`
  - 为什么不可独立机检：针脚表住在测试文件里；在 fr_check 里另抄一份会造出第二把尺子、并可漂移。
- **NFR-4**：可测试：就绪/依赖/门是纯函数，可单测
  - 测试：`tests/test_core.py::ReadyTest、tests/test_gates.py::ExpectSemanticsTest`
  - 为什么不可独立机检：「可单测」由这些单测通过来证明；没有比「跑它们」更独立的命令。
- **NFR-5**：可断点续跑：崩溃后由事件流恢复
  - 测试：`tests/test_store.py::StoreTest::test_events_are_append_only_and_replayable`
  - 为什么不可独立机检：「进程崩溃后恢复」需要真的杀掉进程再重建；测试用「重放事件流得同一投影」等价替代。

## 负向对照（把实现改坏 → 判据必须变红，证明“绿”不是真空）

做法：临时改 `disflow/*` → 跑 `scripts/fr_check.py` → 贴红 → `git checkout --` 当场还原。

- **FR-2**：把 `store.compare_projection` 开头改成 `return True, []`（投影永判一致）
  - 红输出：`**FAIL** [FR-2] rebuild --check 逐字节一致`；四类计数 失败 1
- **FR-5**：往 `disflow/core.py` 追加 `NODE_EDGE_CONTAINS = "contains"`
  - 红输出：`**FAIL** [FR-5] 无 contains 边（层级只由 parent 定义）`；四类计数 失败 1。**本轮正因此发现初版 `\bcontains\b` 模式在本机真空通过。**
- **FR-14**：往 `disflow/engine.py` 追加 `import subprocess`
  - 红输出：`**FAIL** [FR-14] drive 只推不跑、不 spawn`；四类计数 失败 1
- **FR-18**：把 `gates._check_expect` 的 `exit0` 分支改成恒 `return True`
  - 红输出：`**FAIL** [FR-18] done 前必须过验收门`；四类计数 失败 2（dirty-repo 变体还叠加 FR-20）
- **NFR-1**：往 `disflow/views.py` 追加 `import requests`
  - 红输出：`**FAIL** [NFR-1] 零第三方依赖`；四类计数 失败 9（连带 CLI 检查因 import 失败全红）
- **FR-27**：把 `ready.is_stuck` 的 return 改成恒 `False`
  - 红输出：`**FAIL** [FR-27] status 能识别卡死`；四类计数 失败 1
- **NFR-3**：往 `disflow/views.py` 追加 `import sqlite3`（标准库，只有 NFR-3 逮得到）
  - 红输出：`**FAIL** [NFR-3] 文件式：无服务、无数据库`；四类计数 失败 2

## 本报告的遗漏面（哪些没查、为什么）

- **§1–§2 不在本报告范围**：病根/保留项/目标/非目标共 23 条已由 `scripts/goal_check.py` 机检（22/22），本轮不重复。
- **§5「怎么算系统了」6 条未单列**：与 §3 的 FR 大量重叠（如 §5.1 = FR-2、§5.3 确定性 = 两次 drive 相同），未另起编号机检。跨条组合行为（同一投影两次 `drive` 逐字节相同）目前只有 `tests/test_e2e.py::EndToEndTest::test_drive_is_bit_identical` 覆盖。
- **§6 术语表**：纯定义，无机检对象（`[判断]`：术语是否与代码一致属人读）。
- **`scripts/goal_check.py` 的 p3 在本机真空通过**：其 `\bcontains\b` 模式在本环境 git grep -E 下一条也匹配不到（即使 `contains:` 明明存在）。本轮**未改** goal_check（不合并），仅记录；其余 21 条 goal_check 判据未逐条做负向对照重验。
- **负向对照只做了 7 条 / 29 条机检判据**（brief 要求的下限是 5）：其余 22 条没有做变异验证，其“有牙”是推定，不是实测。
- **NFR-3 只机检了“无服务/无数据库”**；“运行产物与源码分离”靠 `spec.SPEC["state"]["dir"]` 约定，未机检（没有一条命令能判定“分离”）。
- **FR-12 的实现位置与文义有偏差（未判缺陷）**：文义说 `ready(n)` 覆盖“本波写面不重叠”，实际由 `dispatch.pack_waves` 实现，`is_ready` 不含写面。`[判断]`：这是设计选择还是漏实现，需人定夺。

---
以上结论中，凡不能被一条命令复核的句子均以 `[判断]` 标注。

