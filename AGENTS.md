# disflow · 项目指令（给 AI 助手的**常驻**入口）

> 本文件由 pi 每个请求加载（工作目录及其父目录的 `AGENTS.md`/`CLAUDE.md`）。规则正文在
> `docs/` 与 `assets/`；这里只放**入口指针**与「先做什么」。**它存在的理由是填补一个真实缺口**
> ——见 `docs/decisions/0004-ai-入口先读索引.md`。

## 0. 动手 / 回答之前：先定位，别先猜

本仓有**两份入口索引**。回答任何「disflow 是什么 / 看板长什么样 / 怎么设计 / 怎么用」
**或**改任何代码之前，先读它们，再决定读哪几段源码：

| 入口 | 谁用 | 内容 |
| --- | --- | --- |
| `docs/INDEX.md` | 人 | 文档清单：`type` / `status` / 一句话 |
| `assets/INDEX.md`（真相源 `assets/registry.json`，机读 `assets/index.jsonl`） | AI/工具 | 资产清单：`path` / `trust` / `premises` / `stale` |

**纪律：不登记 = 永不被读到**（`docs/INDEX.md` 原话）。因此：

- 想找「哪篇文档 / 哪份资产讲这个」→ 先查这两份索引，**不要** `find`/`ls` 整仓猜文件名。
- 索引里标了 **`stale`** 的条目（依据物 `pkg` 变了）→ 它的结论可能已失真，**回源码对**。
- 新增文档必须登记 `docs/INDEX.md`，否则 `tests/test_docs_index.py` 红；
  新增资产走 `assets/registry.json` + `python3 scripts/assets_scan.py scan`。
- 本文件自身由 `tests/test_agent_entry.py` 守卫（存在 + 指向两份索引 + 指针不失效）。

**读某一节别整读**（本仓文档成篇很大：`design.md` 948 行/37 节）。章节级入口 = 一条命令：
`python3 scripts/doc_outline.py <doc> <标题子串>` → `<doc>:<start>-<end>`，再
`read offset=start limit=end-start+1` 只取那一段。机读索引 `docs/outline.jsonl`
（`doc_outline.py --write` 生成、`--check`/`tests/test_doc_outline.py` 守同步）。
pi 侧扩展 `doc-read-guard`（`~/.pi/agent/extensions/doc-read-guard.ts`）会**拦下**对
`docs/**/*.md` / `README.md` 的**整读**（>200 行且未给 `offset`/`limit`）—— 拦不是提示，
是 `{block:true}`。

**资产是拿来「消费」的，不只是「检索」的**——改任何已登记资产（或它的依据物）**走这个循环**
（判据：`playbooks/anchor-workflow.md` + `scripts/assets_scan.py`，**不靠记忆**）：

| 时机 | 命令 | 作用 |
| --- | --- | --- |
| 动手前 | `python3 scripts/assets_scan.py plan` | 改动路径 → **依据先的拓扑刷新序**（哪些资产命中、按什么次序改） |
| 改锚点后 | `python3 scripts/assets_scan.py impact <id>` | 反查**谁依赖它**：所有下游都要重新论证还成不成立（依据断裂是**静默**的，只有 `impact` 看得见） |
| 改完 | `python3 scripts/assets_scan.py scan` | 重生成 `assets/INDEX.md` + `index.jsonl`（防索引漂移） |
| 收尾 | `python3 scripts/finish.py` （默认并行 `--jobs 0`=自动核数；`--jobs 1` 退回串行 discover） | **全量测试（硬门）+ 资产 `check`（只读报告，`stale` 只报不拦）**。`scan`（刷新索引）是**提交前**动作，收尾门不写盘 |
| —（底层动作） | `python3 scripts/assets_scan.py check` | `stale` / 缺证据 → 退出码 1（**当门**）。按 git 事实算：未提交必 stale，故它是**收尾**门，不是每次跑都绿的门 |

`stale` 的含义：依据物比本资产更新（或未提交）⇒ 本资产的结论可能失真，**回源码对**后再收尾 `scan`
重算。历史快照（`dev-board` / `lessons`）持续 stale 是**设计使然**，不是缺陷。

## 1. 一句话地图

disflow = 「**只推不跑**」的任务树分发器：事件流（`events.jsonl`）是唯一真相，投影
（`state.json`）可重放，看板只是视图。角色分工见 `.agents/skills/disflow/SKILL.md`（宿主手册）。

| 想知道 | 去哪 |
| --- | --- |
| 5 分钟上手 / 命令 | `README.md` |
| 实现契约（数据模型 / 就绪 / 门 / 看板语义） | `docs/design.md`（注意可能 stale → 对 `disflow/`） |
| 需求基线（FR/NFR） | `docs/requirements.md` |
| 为什么这么定（决策史） | `docs/decisions/` |
| 宿主怎么派活 / 看板与运行视图的区别 | `.agents/skills/disflow/SKILL.md` |

## 2. 改代码前

- 先读 `docs/design.md` 的对应小节，再对 `disflow/` 源码（`pkg` 是 verified 资产）。
- 测试：`python3 -m unittest discover -s tests -t tests`；**零第三方依赖**（NFR-1）。
- 分层：核心层（`core` / `store` / `graph` / `ready`）零业务词，`tests/test_layering.py` 守。
- 迭代期用 `python3 scripts/test_changed.py` 跑子集；收尾才全量。
