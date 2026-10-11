#!/usr/bin/env python3
"""迭代期只跑「改动相关」的测试子集——默认打印醒目横幅，绝不假装是全量。

为什么要有它（宿主实测，不是估计）：全量 `python3 -m unittest discover -s tests -t tests`
= **58.6s / 582 用例**，耗时集中在 6 个文件（test_worktree 12.1s · test_worktree_gate 5.5s ·
test_selfgate_probe 3.9s · test_docs 3.8s · test_typecheck 3.2s · test_rescope 2.6s），
占 53%；其余 33 个文件共约 27s，主因是**大量子进程**（单次 `python3 -m disflow.cli --help`
= 67ms：裸解释器 18ms + import disflow 34ms）。迭代期反复跑全量是纯等。

**映射判据（显式可读，不猜、不静默跳过）**：
  1. 命名约定：改 `disflow/<stem>.py`（或 `scripts/<stem>.py`）⇒ 并上 `tests/test_<stem>.py`（若存在）；
  2. 引用面：**grep 全库测试文件**，凡文本里出现 `disflow.<stem>` / `from disflow import … <stem>`
     / `scripts/<stem>.py` 的测试文件都进子集（改动可能破坏任何引用它的测试）；
  3. 文档改动（`*.md`）⇒ 并上文档守卫（`test_docs.py` / `test_readme_walkthrough.py`）；
  4. **固定元测试**（声明类全局不变量）：`test_docs.py` / `test_layering.py` /
     `test_declarations.py` / `test_typecheck.py` —— 与改动无关地总是跑。
  5. 无法映射的改动路径（如 `LICENSE`）：**不猜**，打印后**保守改跑全量**（fail-closed，绝不假绿）。

取改动与门**同源**：`disflow.gates.git_changed_files`（`git status --porcelain` ∪
`git diff --name-only <base>`）。若 worker 已 `git commit`，`git status` 会空 ⇒ 用
`--base <开工点 sha>` 补上已提交的改动（否则会漏测 ⇒ 假绿，故本脚本在改动集为空时改跑全量）。

跑法：
  python3 scripts/test_changed.py                        # 子集（默认，带「不是全量」横幅）
  python3 scripts/test_changed.py --jobs 8               # 并发跑选中的测试文件（默认 1=串行；0=自动核数）
  python3 scripts/test_changed.py --changed disflow/views.py   # 注入式假改动（测试/排查用）
  python3 scripts/test_changed.py --base <sha>            # 并上相对 <sha> 已提交的改动
  python3 scripts/test_changed.py --all                  # 全量（等价于既有 discover 命令）
  python3 scripts/test_changed.py --dry-run              # 只打印计划与命令，不执行
  python3 scripts/test_changed.py --json                 # 计划以 JSON 输出（给守卫/自动化读）
退出码：子集/全量模式下透传 `unittest` 的退出码；`--dry-run` 恒 0。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import TypedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(ROOT, "tests")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from disflow.gates import git_changed_files  # noqa: E402  （取改动与门同源）

# 固定元测试：断言**全仓全局不变量**（文档不漂移 / 分层 / 声明契约 / 全仓类型检查），
# 与「改了哪个模块」无关，故任何子集都要包含。
META_TESTS = (
    "test_docs.py",
    "test_layering.py",
    "test_declarations.py",
    "test_typecheck.py",
)

# 文档改动要看的守卫：README 的代码块会被 `test_readme_walkthrough` 真跑。
DOC_TESTS = ("test_docs.py", "test_readme_walkthrough.py")

# 少数特殊文件 → 固定关联的测试文件（不靠命名/引用面推断的那些）。
# 资产登记目录：`assets/registry.json` 与生成的 `index.jsonl` 由 `test_assets_scan.py` 对账。
ASSETS_TESTS = ("test_assets_scan.py",)


def _is_local_tool_path(rel: str) -> bool:
    """仓根**点目录** = 本地产物 / 别的工具的状态（`.pi/` `.workbuddy-ai/` `.ruff_cache/`…），
    不是本仓源码。

    不判为 unmapped：否则每多一个工具状态目录，迭代期就被迫改跑全量
    （实测 2026-10-11：`.workbuddy-ai/` 与新增 `assets/` 一起把子集打回全量 78s）。
    `.github/` 白名单放行——它在语义上是仓内配置，不是本地状态。
    """
    head = rel.split("/", 1)[0]
    return head.startswith(".") and head not in (".github",)


SPECIAL_FILES = {
    "pyrightconfig.json": ("test_typecheck.py",),
}

# 全量命令：`--all` **逐字等价**于既有 discover 命令（README 里保留可用的那条）。
FULL_COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests"]


class Plan(TypedDict):
    """一次映射的完整读数（纯数据，给横幅 / JSON / 守卫测试共用）。"""

    selected: list[str]
    reasons: dict[str, str]
    uncovered: list[str]
    unmapped: list[str]
    all: list[str]
    fallback_full: bool


def all_test_files(tests_dir: str = TESTS_DIR) -> list[str]:
    """`tests/` 下全部 `test_*.py` 文件名（排序，稳定可断言）。"""
    return sorted(
        name for name in os.listdir(tests_dir)
        if name.startswith("test_") and name.endswith(".py")
    )


def _test_source(name: str, tests_dir: str = TESTS_DIR) -> str:
    with open(os.path.join(tests_dir, name), encoding="utf-8") as handle:
        return handle.read()


def _reference_patterns(stem: str, kind: str) -> list[str]:
    """某模块名在测试文件里出现的形状（**显式列出**，不靠子串猜想）。

    `kind="disflow"`：`disflow.<stem>` 或 `from disflow import … <stem>`。
    `kind="script"`：`scripts/<stem>.py` 或 `<stem>.py`（脚本被当文件路径引用）。
    """
    escaped = re.escape(stem)
    if kind == "disflow":
        return [rf"\bdisflow\.{escaped}\b",
                rf"\bfrom disflow import\b[^\n]*\b{escaped}\b"]
    return [rf"scripts/{escaped}\.py", rf"\b{escaped}\.py\b"]


def _add_module_tests(selected: dict[str, str], tests: list[str], stem: str,
                      rel: str, kind: str) -> None:
    """把「改了模块 <rel>」映射到的测试文件加进 `selected`（含判据理由）。"""
    candidate = f"test_{stem}.py"
    if candidate in set(tests):
        selected.setdefault(candidate, f"命名约定 {candidate} ← 改了 {rel}")
    patterns = _reference_patterns(stem, kind)
    for name in tests:
        text = _test_source(name)
        if any(re.search(pattern, text) for pattern in patterns):
            selected.setdefault(name, f"引用了 {rel}（grep 命中）")


def map_changed_to_tests(changed: list[str],
                         tests_dir: str = TESTS_DIR) -> Plan:
    """改动路径 → 相关测试子集（纯函数，可注入假改动，便于守卫测试）。

    返回 `{"selected": [...], "reasons": {test: 判据}, "uncovered": [...],
           "unmapped": [...], "all": [...], "fallback_full": bool}`。
    `fallback_full` 为真表示「有改动无法映射 ⇒ 保守跑全量」（绝不静默跳过）。
    """
    tests = all_test_files(tests_dir)
    testset = set(tests)
    selected: dict[str, str] = {}
    unmapped: list[str] = []

    for raw in changed:
        rel = raw.strip()
        # 删 `./` 前缀要用**前缀**判，不能用 `lstrip("./")` —— 后者按字符集剥，
        # 会把 `.workbuddy-ai/…` 的头点也吃掉，使点目录识别失效（实测 2026-10-11）。
        if rel.startswith("./"):
            rel = rel[2:]
        if not rel:
            continue
        base = os.path.basename(rel)

        if rel.startswith("tests/"):
            if base in testset:
                selected.setdefault(base, f"改的就是它：{rel}")
            else:                       # base.py / __init__.py 等共享测试基建
                for name in tests:
                    selected.setdefault(name, f"共享测试基建改动：{rel}")
            continue

        if rel.startswith("disflow/") and base.endswith(".py"):
            stem = base[:-3]
            if stem == "__init__":      # 包级改动：影响面不可枚举 ⇒ 全体
                for name in tests:
                    selected.setdefault(name, f"包级改动：{rel}")
                continue
            _add_module_tests(selected, tests, stem, rel, kind="disflow")
            continue

        if rel.startswith("scripts/") and base.endswith(".py"):
            _add_module_tests(selected, tests, base[:-3], rel, kind="script")
            continue

        if _is_local_tool_path(rel):     # 本地工具状态，不是本仓源码：不映射、不算 unmapped
            continue

        if rel.startswith("assets/"):
            for name in ASSETS_TESTS:
                if name in testset:
                    selected.setdefault(name, f"资产登记改动：{rel}")
            if base.endswith(".md"):
                for name in DOC_TESTS:
                    if name in testset:
                        selected.setdefault(name, f"资产文档改动：{rel}")
            continue

        if rel in SPECIAL_FILES:
            for name in SPECIAL_FILES[rel]:
                if name in testset:
                    selected.setdefault(name, f"特殊文件关联：{rel}")
            continue

        if base.endswith(".md"):
            for name in DOC_TESTS:
                if name in testset:
                    selected.setdefault(name, f"文档改动：{rel}")
            continue

        unmapped.append(rel)

    for name in META_TESTS:             # 固定元测试总是跑
        if name in testset:
            selected.setdefault(name, "固定元测试（声明类，与改动无关）")

    # 有无法映射的改动 ⇒ 不猜、不静默跳过：计划里直接把 selected 扩成全库，
    # 与 `fallback_full` 一致（横幅读到的覆盖数就是真会跑的集合）。
    fallback = bool(unmapped)
    selected_list = list(tests) if fallback else sorted(selected)
    uncovered = [] if fallback else [name for name in tests if name not in selected]
    return {
        "selected": selected_list,
        "reasons": selected,
        "uncovered": uncovered,
        "unmapped": sorted(set(unmapped)),
        "all": tests,
        "fallback_full": fallback,
    }


def detect_changed(base: str | None) -> list[str] | None:
    """与门同源的改动集。非 git 仓返回 None（不假装有读数）。"""
    return git_changed_files(ROOT, base_sha=base)


def build_command(selected: list[str], *, run_all: bool = False) -> list[str]:
    """构造 `unittest` 调用。`run_all` 时**逐字等于**全量命令（用例数因此等价）。"""
    if run_all:
        return list(FULL_COMMAND)
    return [sys.executable, "-m", "unittest", *(name[:-3] for name in selected)]


def resolve_jobs(n: int) -> int:
    """`--jobs 0` = 自动（CPU 核数）；其余原样（≥1）。

    为何不默认自动：并发对**子进程密集**的套件收益有限（实测 8 并发只到 2.1×，
    有效并行 ~3.3 核），且会拉高 CPU 争用——默认保持串行、可预测，由人显式开并发。
    """
    return (os.cpu_count() or 1) if n == 0 else n


def parallel_commands(selected: list[str]) -> list[list[str]]:
    """每个测试文件一个 `unittest` 调用（供 `--jobs>1` 并发跑）。

    按**文件**而非按用例并行：每个文件独立进程，各自 `DISFLOW_STATE_DIR`、各自 import，
    互不串味（用例级并行会共享 `os.environ`，实测会串味）。
    """
    return [[sys.executable, "-m", "unittest", name[:-3]] for name in selected]


def run_parallel(selected: list[str], jobs: int) -> int:
    """并发跑每个测试文件并聚合退出码；任一失败 → 1。失败者的完整输出照抄到 stdout。"""
    from concurrent.futures import ThreadPoolExecutor

    env = test_env()

    def one(cmd: list[str]) -> tuple[str, int, str]:
        proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True)
        return cmd[-1], proc.returncode, proc.stdout + proc.stderr

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(one, parallel_commands(selected)))
    failed = [(name, out) for name, rc, out in results if rc != 0]
    for name, out in failed:
        print(f"\n===== FAILED: {name} =====")
        print(out)
    print(f"\n[并行 jobs={jobs}] {len(results)} 个测试文件 · "
          f"{len(results) - len(failed)} 通过 · {len(failed)} 失败")
    return 1 if failed else 0


def test_env() -> dict[str, str]:
    """子集按模块名调用需要 `tests/` 在 `PYTHONPATH`（`from base import …`）。"""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [TESTS_DIR, ROOT] + ([existing] if existing else [])
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _banner(plan: Plan, *, run_all: bool, fallback: bool) -> str:
    """醒目的「这是子集，不是全量」横幅 + 覆盖读数 + 收尾提示。"""
    lines = ["=" * 72]
    if run_all:
        lines.append("✅ 这是全量（等价于既有 discover 命令）")
    else:
        lines.append("⚠️  这是子集，不是全量（SUBSET, NOT THE FULL SUITE）")
    lines.append(f"    覆盖 {len(plan['selected'])} 个测试文件 / 全库 {len(plan['all'])} 个"
                 f" / 未覆盖 {len(plan['uncovered'])} 个")
    uncovered = plan["uncovered"]
    if uncovered:
        lines.append("    未覆盖：" + ", ".join(uncovered))
    if fallback:
        lines.append("⚠️  有改动文件无法映射到测试（不猜、不静默跳过）⇒ 保守改跑全量：")
        for rel in plan["unmapped"]:
            lines.append(f"      - {rel}")
    if not run_all:
        lines.append("    ⚠️ 收尾必须跑全量：python3 -m unittest discover -s tests -t tests")
    lines.append("=" * 72)
    return "\n".join(lines)


def _plan_lines(plan: Plan) -> str:
    reasons = plan["reasons"]
    out = [f"  {name:<32} ← {reasons[name]}" for name in plan["selected"]]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="迭代期只跑改动相关的测试子集（绝不假装全量）")
    parser.add_argument("--all", action="store_true", help="跑全量（等价于既有 discover 命令）")
    parser.add_argument("--base", default=None,
                        help="开工点 sha/ref：并上 `git diff --name-only <base>` 的已提交改动")
    parser.add_argument("--changed", default=None,
                        help="注入式假改动（逗号分隔路径），覆盖 git 检测（测试/排查用）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划与命令，不执行")
    parser.add_argument("--json", action="store_true", help="计划以 JSON 输出")
    parser.add_argument("--jobs", type=int, default=1,
                        help="并发跑测试文件的进程数（默认 1；0=自动核数）")
    args = parser.parse_args(argv)
    jobs = resolve_jobs(args.jobs)

    if args.all:
        tests = all_test_files()
        plan = Plan(selected=tests, reasons={}, uncovered=[], unmapped=[],
                    all=tests, fallback_full=False)
        command = build_command([], run_all=True)
        if args.json:
            print(json.dumps({"mode": "all", "command": command}, ensure_ascii=False))
        else:
            print(_banner(plan, run_all=True, fallback=False))
            print("$ " + " ".join(command))
        if args.dry_run:
            return 0
        if jobs > 1:
            print(f"[并行] jobs={jobs}（每个测试文件一个进程）")
            return run_parallel(all_test_files(), jobs)
        return subprocess.run(command, cwd=ROOT).returncode

    if args.changed is not None:
        changed: list[str] | None = [p for p in args.changed.split(",") if p.strip()]
    else:
        changed = detect_changed(args.base)
    if changed is None:
        print("✗ 不是 git 仓（或 git 不可用）⇒ 无法判定改动，本脚本不假装有读数；"
              "请改用全量：python3 -m unittest discover -s tests -t tests", file=sys.stderr)
        return 2
    if not changed:
        print("⚠️  未检测到改动（若你已 git commit，请用 --base <开工点 sha> 指定）"
              "⇒ 保守改跑全量（绝不假绿）", file=sys.stderr)
        changed = []

    plan = map_changed_to_tests(changed)
    run_all = bool(plan["fallback_full"]) or not changed
    if run_all:
        command = build_command([], run_all=True)
    else:
        command = build_command(plan["selected"])

    if args.json:
        print(json.dumps({
            "mode": "all" if run_all else "subset",
            "changed": changed,
            "selected": plan["selected"],
            "uncovered": plan["uncovered"],
            "unmapped": plan["unmapped"],
            "command": command,
        }, ensure_ascii=False, indent=2))
        if args.dry_run:
            return 0
    else:
        print(_banner(plan, run_all=run_all, fallback=bool(plan["fallback_full"])))
        if not run_all:
            print("改动 → 测试：")
            print(_plan_lines(plan))
            print()
        print("$ " + " ".join(command))
        if args.dry_run:
            return 0

    if jobs > 1:
        run_list = list(all_test_files()) if run_all else plan["selected"]
        print(f"[并行] jobs={jobs}（每个测试文件一个进程）")
        return run_parallel(run_list, jobs)
    return subprocess.run(command, cwd=ROOT, env=test_env()).returncode


if __name__ == "__main__":
    raise SystemExit(main())
