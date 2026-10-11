#!/usr/bin/env python3
"""收尾门：**全量测试（硬门）+ 资产健康报告（只读 advisory）**。

为什么要有它（不是洁癖）：
`assets_scan.py check` 是本仓唯一能机检「资产与其依据物是否失配」的判据，但它从没进任何
必走路径——本轮实测：改了 `requirements`/`pkg`/`design` 却忘了 `assets_scan`，资产 stale
无人知。把它接成收尾必看的一步，才谈得上「资产能被正常消费与维护」。

**为什么 `check` 是只读 advisory、不是硬门**（别再改回硬门）：
`stale` 按 git 事实当场复算，且 `premises` 是**粗粒度**（`scripts` 整个目录 / `pkg` 整个包）——
`assets/README.md` 自己声明「浅层改动也会触发 stale（**宁可误报**）」。因此几乎每次代码改动
都会令一批下游资产 stale；把 `check` 当硬门 = 常态误报 = 被无视。它是**维护队列**：报出
「哪些资产的依据变了、值得回看」，由人/agent 决定是否重确认（`plan` 定序、`impact` 反查）。
**硬**的结构保证（索引↔registry 一致 / 悬空依据 / `verified` 缺证据 / `premises` 成环）在
`tests/test_assets_scan.py` 里，由全量套件覆盖——收尾门跑全量即覆盖它。
历史快照 `dev-board`/`lessons` 曾挂在活 `pkg`/`tests` 上而**永久** stale，已按
`assets/README.md` 的「资产/档案」判据归为**档案、移出登记**（jev conf 0.77）——此后 stale 都是
因**当前**改动而起，不再是噪声基线。
`scan`（刷新索引）会写盘，且其中嵌了 `dirty`/`last_commit` 等 git 事实 ⇒ 提交后一跑就产生 diff；
故本门**不写盘**：`scan` 是**提交前**的动作，不是收尾动作。

跑法：`python3 scripts/finish.py [--dry-run]`
退出码：0 = 全量通过（资产 stale 只报不拦）；1 = 全量不过；2 = 环境不满足。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

_TEST_ARGS = ["-m", "unittest", "discover", "-s", "tests", "-t", "tests"]
_SCAN = "scripts/assets_scan.py"


def test_argv() -> list[str]:
    """全量测试的 argv（收尾口径 = 与 README 的 `unittest discover` 逐字一致）。"""
    return [sys.executable, *_TEST_ARGS]


def assets_argv(action: str) -> list[str]:
    """`assets_scan.py` 的 argv：`check`（只读报告）/ `scan`（刷新索引，会写盘）/ `plan`（刷新序）。"""
    if action not in ("scan", "check", "plan"):
        raise ValueError(f"未知 action：{action!r}（支持 scan / check / plan）")
    return [sys.executable, _SCAN, action]


def _run(argv: list[str]) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout + proc.stderr


def _tests_summary(output: str) -> str:
    """从 unittest 输出取一行摘要（`Ran N tests` + `OK|FAILED`）；取不到就如实说。"""
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    verdict = "OK" if re.search(r"^OK", output, re.MULTILINE) else "FAILED"
    return f"{verdict}（{ran.group(1) if ran else '?'} tests）"


def _stale_lines(check_output: str) -> list[str]:
    """从 `check` 输出里抽出 stale 明细行（`  - id ← basis`）与摘要行（`⚠ …`）。"""
    return [ln for ln in check_output.splitlines()
            if ln.startswith(("  - ", "⚠"))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="收尾门：全量测试（硬）+ 资产健康（只读报告）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不跑")
    args = parser.parse_args(argv)

    steps = [("全量测试", test_argv()), ("assets check", assets_argv("check"))]
    if args.dry_run:
        for label, cmd in steps:
            print(f"{label}: {' '.join(cmd)}")
        return 0

    if not (ROOT / "disflow").is_dir():
        print("✗ 不在 disflow 仓根：找不到 disflow/", file=sys.stderr)
        return 2

    log = pathlib.Path(tempfile.mkstemp(prefix="disflow-finish-", suffix=".log")[1])
    rc = 0
    with log.open("w", encoding="utf-8") as handle:
        code, output = _run(test_argv())
        handle.write(f"\n===== 全量测试 =====\n{output}\n")
        print(f"[1/2] 全量测试 … {_tests_summary(output)}")
        if code != 0:
            rc = 1
            for line in output.splitlines():
                if line.startswith(("FAIL:", "ERROR:")):
                    print(f"        {line}")

        code, output = _run(assets_argv("check"))
        handle.write(f"\n===== assets check =====\n{output}\n")
        if code == 0:
            print("[2/2] assets check … 无 stale")
        else:
            # 只报不拦：stale 含粗粒度前提的误报（见模块 docstring），是维护队列不是失败。
            print("[2/2] assets check … stale（**advisory**，不拦；刷新见 AGENTS.md 循环）")
            for line in _stale_lines(output):
                print(f"        {line}")
    print(f"\nrc={rc} · 全量日志：{log}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
