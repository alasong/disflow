# 项目资产索引

> 由 `scripts/assets_scan.py` 生成，**勿手改**；事实源 `assets/registry.json`。
> 无生成时间戳（输出确定性，避免无谓 diff）。
> 资产 16 条 · stale 8 条。

| id | type | tier | trust | path | purpose |
| --- | --- | --- | --- | --- | --- |
| `agents` | doc | asserted | stale | `AGENTS.md` | AI 常驻入口：动手/回答前先读 docs/INDEX.md + assets/INDEX.md（缺口修复见 ADR 0004） |
| `architecture` | doc | asserted | stale | `docs/architecture.md` | 按实现画的架构图（events→state→树/图/看板/分发/自检） |
| `backlog` | doc | asserted | asserted | `docs/v0.2-backlog.md` | v0.2 未完成缺口清单（唯一放行依据） |
| `board-entity` | doc | asserted | stale | `docs/decisions/0005-看板提升为一等实体.md` | 交付单元 Spec：看板升为一等实体（需求→设计→实现→验证→文档 全量交付） |
| `config` | config | asserted | asserted | `pyrightconfig.json` | 类型检查范围与 Python 版本 |
| `design` | doc | asserted | stale | `docs/design.md` | 实现契约：节点字段 / 事件 / 门 / 投影语义（改实现前先看它） |
| `design-draft` | doc | stub | stub | `docs/design-dispatch-tree-graph.md` | 分发器/树/图设计草稿（标注未实现） |
| `fr-gap` | report | asserted | stale | `docs/fr-nfr-gap-report.md` | 需求覆盖机检报告（由 scripts/fr_check.py 生成） |
| `kaa-comparison` | report | asserted | stale | `docs/kaa-comparison.md` | disflow vs kaa 机制对照：规模/各自独有/差距结论（实测，用于回答「差多少」） |
| `lens-config` | config | asserted | asserted | `.pi-lens.json` | pi-lens 静态检查规则开关 |
| `pkg` | code | verified | verified | `disflow/` | 引擎包：状态/树/图/门/视图/CLI + web 入口（唯一实现面） |
| `readme` | doc | asserted | asserted | `README.md` | 5 分钟上手 walkthrough + 量化 / worktree 说明 |
| `requirements` | doc | asserted | asserted | `docs/requirements.md` | FR/NFR 需求基线（FR-1..29 + NFR-1..7），一切验收的源头 |
| `scripts` | script | asserted | asserted | `scripts/` | 自检 / 探针 / 报告脚本（fr_check、mutation_check、assets_scan 等） |
| `spec-pdca-template` | doc | asserted | stale | `templates/spec-pdca/README.md` | spec 模板（每个功能一份，内部 PDCA）：拷去别的项目用；含功能模板与项目铁律模板 |
| `tests` | test | verified | stale | `tests/` | 验收测试套件：门拒收 / 投影一致 / 分层自检 / CLI 回归 / web 只读性 |

## 按类别（需求 / 设计 / 实现 / 验证 / 过程…）

| 类别 | 资产 |
| --- | --- |
| config | `config`, `lens-config` |
| design | `architecture`, `design`, `design-draft` |
| documentation | `agents`, `kaa-comparison`, `readme` |
| implementation | `pkg`, `scripts` |
| process | `board-entity`, `spec-pdca-template` |
| requirement | `backlog`, `fr-gap`, `requirements` |
| verification | `tests` |

## stale（依据物已变，结论可能失真）

| id | 依据 stale_basis | 说明 |
| --- | --- | --- |
| `agents` | `design`, `pkg` | AI 常驻入口：动手/回答前先读 docs/INDEX.md + assets/INDEX.md（缺口修复见 ADR 0004） |
| `architecture` | `pkg`, `design` | 按实现画的架构图（events→state→树/图/看板/分发/自检） |
| `board-entity` | `requirements`, `pkg` | 交付单元 Spec：看板升为一等实体（需求→设计→实现→验证→文档 全量交付） |
| `design` | `requirements` | 实现契约：节点字段 / 事件 / 门 / 投影语义（改实现前先看它） |
| `fr-gap` | `pkg`, `scripts` | 需求覆盖机检报告（由 scripts/fr_check.py 生成） |
| `kaa-comparison` | `pkg`, `design` | disflow vs kaa 机制对照：规模/各自独有/差距结论（实测，用于回答「差多少」） |
| `spec-pdca-template` | `readme`, `pkg` | spec 模板（每个功能一份，内部 PDCA）：拷去别的项目用；含功能模板与项目铁律模板 |
| `tests` | `requirements` | 验收测试套件：门拒收 / 投影一致 / 分层自检 / CLI 回归 / web 只读性 |

## specs（一等交付单元：产出按角色挂在 Spec 下）

| spec | 角色覆盖 | 缺角色 | 说明 |
| --- | --- | --- | --- |
| `board-entity` | 5/5 | — | 交付单元 Spec：看板升为一等实体（需求→设计→实现→验证→文档 全量交付） |

## premises 图（依据 → 依赖它的资产）

| 依据 | 依赖它的资产 |
| --- | --- |
| `design` | `agents`, `architecture`, `kaa-comparison` |
| `pkg` | `agents`, `architecture`, `board-entity`, `design`, `fr-gap`, `kaa-comparison`, `readme`, `scripts`, `spec-pdca-template`, `tests` |
| `readme` | `spec-pdca-template` |
| `requirements` | `board-entity`, `design`, `tests` |
| `scripts` | `fr-gap` |
