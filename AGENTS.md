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
