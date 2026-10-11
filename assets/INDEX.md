# 项目资产索引

> 由 `scripts/assets_scan.py` 生成，**勿手改**；事实源 `assets/registry.json`。
> 无生成时间戳（输出确定性，避免无谓 diff）。
> 资产 13 条 · stale 1 条。

| id | type | tier | trust | path | purpose |
| --- | --- | --- | --- | --- | --- |
| `agents` | doc | asserted | asserted | `AGENTS.md` | AI 常驻入口：动手/回答前先读 docs/INDEX.md + assets/INDEX.md（缺口修复见 ADR 0004） |
| `architecture` | doc | asserted | asserted | `docs/architecture.md` | 按实现画的架构图（events→state→树/图/看板/分发/自检） |
| `backlog` | doc | asserted | asserted | `docs/v0.2-backlog.md` | v0.2 未完成缺口清单（唯一放行依据） |
| `config` | config | asserted | asserted | `pyrightconfig.json` | 类型检查范围与 Python 版本 |
| `design` | doc | asserted | asserted | `docs/design.md` | 实现契约：节点字段 / 事件 / 门 / 投影语义（改实现前先看它） |
| `design-draft` | doc | stub | stub | `docs/design-dispatch-tree-graph.md` | 分发器/树/图设计草稿（标注未实现） |
| `fr-gap` | report | asserted | stale | `docs/fr-nfr-gap-report.md` | 需求覆盖机检报告（由 scripts/fr_check.py 生成） |
| `lens-config` | config | asserted | asserted | `.pi-lens.json` | pi-lens 静态检查规则开关 |
| `pkg` | code | verified | verified | `disflow/` | 引擎包：状态/树/图/门/视图/CLI（唯一实现面） |
| `readme` | doc | asserted | asserted | `README.md` | 5 分钟上手 walkthrough + 量化 / worktree 说明 |
| `requirements` | doc | asserted | asserted | `docs/requirements.md` | FR/NFR 需求基线（FR-1..29 + NFR-1..7），一切验收的源头 |
| `scripts` | script | asserted | asserted | `scripts/` | 自检 / 探针 / 报告脚本（fr_check、mutation_check、assets_scan 等） |
| `tests` | test | verified | verified | `tests/` | 验收测试套件：门拒收 / 投影一致 / 分层自检 / CLI 回归 |

## 按类别（需求 / 设计 / 实现 / 验证 / 过程…）

| 类别 | 资产 |
| --- | --- |
| config | `config`, `lens-config` |
| design | `architecture`, `design`, `design-draft` |
| documentation | `agents`, `readme` |
| implementation | `pkg`, `scripts` |
| requirement | `backlog`, `fr-gap`, `requirements` |
| verification | `tests` |

## stale（依据物已变，结论可能失真）

| id | 依据 stale_basis | 说明 |
| --- | --- | --- |
| `fr-gap` | `scripts` | 需求覆盖机检报告（由 scripts/fr_check.py 生成） |

## premises 图（依据 → 依赖它的资产）

| 依据 | 依赖它的资产 |
| --- | --- |
| `design` | `agents`, `architecture` |
| `pkg` | `agents`, `architecture`, `design`, `fr-gap`, `readme`, `scripts`, `tests` |
| `requirements` | `design`, `tests` |
| `scripts` | `fr-gap` |
