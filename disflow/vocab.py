"""disflow 机制数据（路径 / 状态集 / 阶段集）。业务名词只出现在这里与 node 模板。

⚠ **术语**：本模块名为 `spec`，但此处的「spec」是**本实例的机制数据（vocabulary + 内建默认）**，
**不是** ADR 0006 的 **Spec（交付规格 / 一类交付单元实体，登记在项目资产里）**。
存储布局的常量叫 `STORAGE`（不再叫 `SPEC`），避开与该实体撞名。
"""
from __future__ import annotations

# 机制词汇从核心层再导出（NFR-2：核心不依赖业务层，方向是 vocab → core）
from .core import KINDS, STORED_STATUS, TERMINAL  # noqa: F401  （向后兼容再导出）

# 存储机制：状态目录与文件名（store 层按此取路径）
STORAGE: dict = {
    "state": {
        "dir": ".disflow",
        "env": "DISFLOW_STATE_DIR",
        "events": "events.jsonl",
        "projection": "state.json",
        "checkpoints": "checkpoints",
    }
}

# 看板定义（stage）的**内建默认**。**权威来源已搬到独立文件 `boards.json`**
# （见 `disflow/boards.py`）：`Config.boards` 从那里读，`Config.stages` 由它派生。
# 这里保留一份是为了「零配置可用」——两个文件都不存在时用这套。
#
# **四个阶段**（需求 → 设计 → 开发 → 验证），与设计原文的阶段图同形。
# `done` **不是板**：它是节点**状态**（`core.TERMINAL`），与阶段是两个轴——留着它会造出一个
# 永远为空、且与状态同名的板（大半节点状态是 done，板里却是 0）。凡是「干完了」用 status 表达。
# （`boards.py` 把这条变成机检：`RESERVED_IDS`，写进 boards.json 即拒收。）
STAGES: tuple[str, ...] = ("req", "design", "dev", "verify")

# 板的人读标签的**内建默认**（同上，权威来源是 boards.json 的 `label`）。
# 缺项**回落成板 id 本身** —— 自定义板不会因为少一条标签就崩，也不逼使用者补一份标签表。
# 机器态（`--json`）不带标签：标签是**渲染**的事，不是事实。
STAGE_LABELS: dict[str, str] = {
    "req": "需求",
    "design": "设计",
    "dev": "开发",
    "verify": "验证",
}

# `stage` 的**两个内建默认**（都只在上面这套内建板里有意义；自定义 boards.json 时由
# `Config.entry_stage` / `Config.default_stage` 从板序**派生**，见 `disflow/config.py`）。
#
# 为何要分开两个名字：它们回答的是两个不同的问题 ——
#   - `ENTRY_STAGE`：**root 容器**落在哪块板。root 是树的自身目标，按流程语义该在**入口**。
#   - `DEFAULT_STAGE`：**任务**没声明 `stage` 时落在哪块板。内建流程里是「开发」
#     （大多数节点是干活的），这个选择**不是**从板序能推出来的 —— 所以它是个显式策略值。
# 两者的**唯一权威**是 `boards.json`；这里这份是「两个文件都不存在」时的回落。
# 注意：`state.py` 的 reducer 只把这两个名字当**重放旧事件流的兜底**（早期 `tree.init`
# 不带 stage 字段），改它们会改变历史事件的重放结果 —— 不要为了让新配置生效而改这里。
ENTRY_STAGE: str = STAGES[0]
DEFAULT_STAGE: str = "dev"

# 负责方（role）—— 与 stage 独立
ROLES: tuple[str, ...] = ("dev", "qa", "pm")

# 规模预算（门 `size_budget`）
SIZE_BUDGET_DEFAULT: int = 400

# ── 理由质量：「写了但等于没写」的敷衍词表（融合自 kaa 的 `weak_reason_blocklist`）────
# 为何需要：破例通道（`acceptance.waiver` / `done --no-result` / `scope_waiver`…）默认接受
# **任意**非空理由 —— 于是最省事的合规姿势就是写「无」/「不需要」。本仓实测过同型缺陷：
# 「判据写成提示的效力：319 次提及、零行为改变」（`AGENTS.md`）。
# 判据不是「禁掉破例」（那会逼人绕过门，造出更坏的假绿），而是**要求理由具体到能复查**：
# 整串等于词表（或过短）⇒ 拒收并要求重写。
WEAK_REASON_DEFAULT: tuple[str, ...] = (
    "无", "不需要", "无需", "不适用", "没有", "n/a", "N/A", "无此项", "略", "同上", "待定",
)

# ── 声称与命令的对账（融合自 kaa 的 `test_claim_words` / `claim_pass_words`）────
# 为何需要：worker 在 `summary` 里写「测试通过」，而 `acceptance.cmd` 其实是一条恒真命令
# （`true` / `echo hi`）—— 两条都在各自的位置，门却看不出它们**不是一回事**。
# 判据：声称命中了「测试」词表 **且** 命令不含测试词 ⇒ 记入证据作**信号**（见 `claim_mismatch`）。
# 为何是信号而不是硬拒收：词表不可能完备（新框架天天有），硬拒收会把合法形态误杀 ——
# 与 `vacuous_reasons` 同一条纪律：**记录 + 报出，不改变门的结果**。
TEST_CLAIM_WORDS: tuple[str, ...] = (
    "测试", "用例", "单测", "test", "pytest", "unittest", "vitest", "jest", "mocha",
)
CLAIM_PASS_WORDS: tuple[str, ...] = ("通过", "全绿", "全部成功", "pass", "passed", "green", "ok")

# ── 漂移门 1：产出是否跟树目标/节点目标相关（goal_align）────────────────────
# 为何需要：scope 门拦「改了不该改的文件」，acceptance 门拦「产出不对」，但**两者都不拦跑题** ——
# 一个节点把验收命令写成 `true` + 改了 touch_plan 里的文件，但改的内容跟目标毫无关系，两道门全绿。
# 判据：声明/摘要必须与节点目标有**共同内容词**（中文按 2-gram，英文按词）。
# 为何是硬门而不是信号：本仓已有 vacuum/claim 两个信号类判据，但漂移属于「做完了但没用」，
# 它不会在证据里留下任何异常痕迹 —— 信号模式下没人会去看。
# 逃生口：`declarations.goal_waiver`（理由经 `weak_reason` 质量门）。
# 停用词与最小词长（过短/过泛的词不参与比对，否则「做」「一个」就能骗过门）。
GOAL_STOPWORDS: tuple[str, ...] = (
    "的", "了", "和", "与", "或", "在", "是", "把", "被", "对", "从", "到",
    "一个", "这个", "那个", "们", "个", "种", "次", "步", "事", "做", "改", "修",
    # 2-gram 层面的套话碎片（跨词边界产生，无区分度）—— 实测踩到：「做一个」
    # 让「做X需求分析」与「做X日志清理」被判为对齐。
    "做一", "一个", "一下", "可以", "需要", "进行", "实现一", "完成一",
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
)

# ── 漂移门 2：Python 代码底线（code_quality）─────────────────────────────
# 为何需要：disflow 的验收命令可以是「文件内容包含 X」这类弱判据，而语法/危险写法无人管。
# 判据（全确定性、纯 AST / 文本，不执行代码）：
#   ① 新增/修改的 .py 必须能 `ast.parse`（语法错 = 改坏了，而 acceptance 未必看得出）
#   ② 无裸 `except:` / 无 `except Exception:` 而吞掉不 raise（宽捕获掩盖真错）
#   ③ 无可变默认参数（`def f(x=[])`：跨调用共享状态，经典阴间 bug）
#   ④ 无 `shell=True`（命令注入面）
# 逃生口：`declarations.code_waiver`（理由经 `weak_reason` 质量门）。
# 为何不查更多（如行长/命名）：那些是风格，不是正确性 —— 风格门会诱发大量无意义摩擦。
CODE_QUALITY_SUFFIXES: tuple[str, ...] = (".py",)

# ── 漂移门 3：假证据拒收（verify_real 的 disflow 版）──────────────────────
# 为何需要：`claim_mismatch` 只**记信号**（本仓已实测：信号没人看）。而「声称测试通过、
# 实际验收命令是 `true`」是**最典型的假证据** —— 它让「全绿」直接失真。
# 判据（升级为硬门）：声称命中测试词表 **且** `acceptance.cmd` 不含测试词 ⇒ **拒收**。
# 逃生口：`declarations.claim_waiver`（理由经 `weak_reason` 质量门）。
# 与 `claim_mismatch` 的关系：同一判据的**信号版与硬门版共用一套词表**（不做第二份）。
VERIFY_HINT_WORDS: tuple[str, ...] = (
    "测试", "用例", "单测", "验证", "test", "pytest", "unittest", "vitest", "jest",
    "mocha", "cargo", "go test", "npm", "make",
)

# 租约有效期：`start` 加锁；超时后由 `reap` **显式**回收（reap 是事件，就绪判定保持纯函数）
LEASE_TTL_SECONDS: int = 900

# 提问纪律（FR-15）：brief 必须一次写全「必填声明 / 提问纪律 / 结果契约」。
# 为何要它：worker 自己拍板的代价是**默默改范围**——而范围是宿主定的。给一条合法通道
# （`collect --result` 的 `status: blocked`）比要求「别乱来」更能执行。
QUESTION_POLICY_DEFAULT: dict = {
    "ask_host": [
        "改 touch_plan / 验收标准 / goal",
        "跨节点的接口约定",
        "与 goal 相冲的取舍",
    ],
    "decide_alone": ["实现细节", "命名", "内部结构"],
    "how": (
        "要问就问：把问题写进 `collect --result` 的 summary 并把 status 置为 'blocked'，"
        "然后停。不要自己拍板改范围——宿主读到 blocked 后会决定（rescope / unrequire / 改 goal），"
        "再由你继续。status 里没有「我猜了一下」这个选项。"
    ),
}

# 树级终止条件门（`tree_closure`）：树里必须有 done 的收益验证 + 经验沉淀
CLOSURE_ENTRIES: tuple[str, ...] = ("evaluate",)
SEDIMENT_ENTRIES: tuple[str, ...] = ("reflect", "standardize")

# 事件 kind 闭集（投影 reducer 只认这些；出现未知 kind 即报错）
EVENT_KINDS: tuple[str, ...] = (
    "tree.init", "tree.goal", "tree.deadline", "tree.boards",
    "node.expand", "node.require", "node.unrequire", "node.relate", "node.unrelate",
    "node.import",
    "node.context",
    "node.stage",
    "node.rescope",
    "asset.produce", "asset.consume",
    "node.start", "node.collect", "node.done",
    "node.fail", "node.retry", "node.skip", "node.reclaim",
    "tree.close", "tree.reopen",
)
