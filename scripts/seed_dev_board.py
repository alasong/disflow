#!/usr/bin/env python3
"""把 `docs/dev-board.md` 的开发看板种进 disflow 自己的状态目录（dogfooding）。

每个「已完成」节点的 `done` 会**真跑**它的验收测试 —— 证据由引擎自算，不手抄。
用法：`python3 scripts/seed_dev_board.py [tree]`（默认 tree=disflow-v0.1）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from disflow.engine import Engine, EngineError, GateReject  # noqa: E402

TREE = "disflow-v0.1"
GOAL = "交付 disflow v0.1：树+图+只推不跑分发器的最小闭环"

TEST_CORE = "python3 -m unittest discover -s tests -t tests -p test_core.py"
TEST_STORE = "python3 -m unittest discover -s tests -t tests -p test_store.py"
TEST_ENGINE = "python3 -m unittest discover -s tests -t tests -p test_engine.py"
TEST_VIEWS = "python3 -m unittest discover -s tests -t tests -p test_views.py"
TEST_IMPORT = "python3 -m unittest discover -s tests -t tests -p test_import_mgt.py"
TEST_E2E = "python3 -m unittest discover -s tests -t tests -p test_e2e.py"
TEST_GATES = "python3 -m unittest discover -s tests -t tests -p test_gates.py"
REBUILD_CHECK = "python3 -m disflow.cli rebuild --check"


def acc(cmd: str | None) -> dict | None:
    return {"cmd": cmd, "expect": "exit0"} if cmd else None


# (id, goal, stage, role, requires, acceptance)
NODES: list[tuple[str, str, str, str, list[str], str | None]] = [
    ("D10", "存储层：事件流 + 原子投影（vendor flowcore/store.py）", "dev", "dev", [], TEST_STORE),
    ("D20", "数据模型：Node/Edge + 校验", "dev", "dev", [], TEST_CORE),
    ("D30", "依赖图：requires/relates + 写时环检测", "dev", "dev", ["D20"], TEST_CORE),
    ("D40", "ready() 纯函数 + 波次打包", "dev", "dev", ["D20", "D30"], TEST_CORE),
    ("D50", "上下文投影 brief()", "dev", "dev", ["D20", "D30"], TEST_CORE),
    ("D60", "分发器 drive/brief/collect（只推不跑）", "dev", "dev", ["D10", "D40", "D50"], TEST_ENGINE),
    ("D70", "门与证据（acceptance + 作用域）", "dev", "dev", ["D10", "D20"], TEST_ENGINE),
    ("D80", "视图层 board/tree/graph", "dev", "dev", ["D20", "D30", "D40"], TEST_VIEWS),
    ("D90", "mgt 导入器", "dev", "dev", ["D20", "D30"], TEST_IMPORT),
    ("D95", "CLI 装配 + doctor", "dev", "dev", ["D60", "D70"], TEST_ENGINE),
    ("V1", "端到端：含跨子树依赖的树，drive 确定性", "verify", "qa", ["D60"], TEST_E2E),
    ("V2", "证据门：构造一次假完成，必须被拒", "verify", "qa", ["D70"], TEST_ENGINE),
    ("V3", "state rebuild --check 一致", "verify", "qa", ["D10"], REBUILD_CHECK),
    ("V4", "从 mgt 导入项目，status 等价（合成 fixture）", "verify", "qa", ["D90"], TEST_IMPORT),
    ("E1", "收益验证：v0.1 相对 mgt 的改进可复现", "verify", "qa", ["D95"], TEST_E2E),
    ("S1", "经验沉淀：本轮回写的判据固化为门", "verify", "pm", ["D70"], TEST_GATES),
    ("PM1", "批准发布 v0.1", "verify", "pm", ["V1", "V2", "V3", "V4", "E1", "S1"], None),
]

# 开工模板（entry）—— tree_closure 门靠它识别 evaluate / reflect|standardize
ENTRY: dict[str, str] = {"E1": "evaluate", "S1": "standardize"}

# 已实现且验证通过、可以 done 的节点（其余留 pending，供后续 drive 推进）
DONE_NODES = ["D10", "D20", "D30", "D40", "D50", "D60", "D70", "D80", "D90", "D95",
              "V1", "V2", "V3", "V4", "E1", "S1"]

# 写面（供波次打包；同波不重叠者才并行）
TARGETS: dict[str, list[str]] = {
    "D10": ["disflow/store.py"],
    "D20": ["disflow/model.py", "disflow/spec.py"],
    "D30": ["disflow/graph.py"],
    "D40": ["disflow/ready.py"],
    "D50": ["disflow/context.py"],
    "D60": ["disflow/dispatch.py"],
    "D70": ["disflow/gates.py"],
    "D80": ["disflow/views.py"],
    "D90": ["disflow/import_mgt.py"],
    "D95": ["disflow/cli.py", "disflow/engine.py", "disflow/state.py"],
    "V1": ["tests/test_e2e.py"],
    "V2": ["tests/test_gate_reject.py"],
    "V3": ["tests/test_rebuild.py"],
    "V4": ["tests/test_import_mgt.py"],
    "E1": ["tests/test_e2e.py"],
    "S1": ["docs/design.md"],
    "PM1": ["docs/"],
}


def main() -> int:
    tree = sys.argv[1] if len(sys.argv) > 1 else TREE
    engine = Engine()
    if tree in engine.trees():
        print(f"✗ 树已存在：{tree}（先清空状态目录或换名）", file=sys.stderr)
        return 1

    engine.init_tree(tree, GOAL)
    engine.expand(tree, "root", [
        {"id": node_id, "goal": goal, "stage": stage, "role": role,
         "entry": ENTRY.get(node_id, ""),
         "touch_plan": TARGETS.get(node_id, ["."]), "acceptance": acc(cmd)}
        for node_id, goal, stage, role, _deps, cmd in NODES
    ])
    for node_id, _goal, _stage, _role, deps, _cmd in NODES:
        for dep in deps:
            engine.require(tree, node_id, dep)

    for node_id in DONE_NODES:
        try:
            engine.start(tree, node_id, "seed")
            engine.collect(tree, node_id, {"status": "ok", "summary": "POC 已实现",
                                           "changed_files": []})
            engine.done(tree, node_id, {"seed": True})
            print(f"✓ {node_id} done（验收真跑）")
        except (EngineError, GateReject) as exc:
            print(f"✗ {node_id} 无法 done：{exc}", file=sys.stderr)
            return 1

    result = engine.drive(tree)
    print(f"\n就绪波次：{result['ready_wave']}  卡死：{result['stuck']}")

    try:
        engine.close(tree)
        print(f"✓ {tree} 已关闭（tree_closure 门通过）")
    except (EngineError, GateReject) as exc:
        print(f"✗ 关闭未通过：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
