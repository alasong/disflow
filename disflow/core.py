"""disflow · 机制词汇（**核心层**，NFR-2：只放通用机制，不放业务名词）。

判据沿用 flowcore 的「lib 零业务判据名」：核心层（本模块 + `store.py` / `graph.py` / `ready.py`）
不得出现业务词——视图列的取值、负责方的取值、门名、开工模板名、写面声明字段、
验收声明字段、树的终止/覆盖逃生口、迁移源与自检命令名……那些只住在 `vocab.py` 与节点模板里。

`tests/test_layering.py` 用「逆向自检」守住这条线：扫描核心文件，命中业务词即失败。
"""
from __future__ import annotations

# 节点分型（HTN 的 compound / primitive）
KINDS: tuple[str, ...] = ("container", "task")

# 存储状态：就绪（ready）是**派生量**，不在这里
STORED_STATUS: tuple[str, ...] = ("pending", "running", "done", "failed", "skipped")

# 终态（非终态即「还活着」）
TERMINAL: tuple[str, ...] = ("done", "skipped")

PENDING = "pending"
RUNNING = "running"
