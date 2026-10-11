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

跑法：`python3 scripts/finish.py [--jobs N] [--dry-run]`
退出码：0 = 全量通过（资产 stale 只报不拦）；1 = 全量不过；2 = 环境不满足。

**测试恒并行**（`--jobs 0` 默认 = 自动核数，走 `test_changed.py --all` 的**每文件一进程**）：
本机 10 核实测串行 75–115s → 并行 ~28s。**不提供串行开关**（刻意）：串行模式曾是
「数字口径」的第二个来源，而两个来源就意味着一处必须区分二者——实测那处（`_actual_test_count`）
**连错三轮**（把 858 写成 26 / 25），每轮都是**静默写错 README**（比报错更坏）。
取消第二来源后，取数只剩一条路径，那类 bug 整类消失。
数字的权威来源是 `tests/test_docs.py` 的 `loader.discover().countTestCases()`（装载数，与并发无关）。
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

_SCAN = "scripts/assets_scan.py"
_PARALLEL = "scripts/test_changed.py"


def parallel_test_argv(jobs: int, *, changed: bool = False) -> list[str]:
    """并行测试 argv。`changed=True` 时只跑**改动相关子集**（迭代用，非收尾）。

    为何要有 `--changed`（实测需求）：本门默认全量 ~36s，而其中 **94%** 是 3 个
    worktree 测试文件（真建 git 仓 + 真建 worktree，每用例 69ms 起的 git 进程开销）。
    迭代期每改一点就跑全量是**纯浪费**；但收尾**必须**全量（本仓纪律，不得放开）。
    故分成两个档：默认全量（不变），`--changed` 走子集。
    子集**自带响亮横幅**（`test_changed.py` 打印「这是子集，不是全量」+ 未覆盖清单）。
    """
    base = [sys.executable, _PARALLEL, *( [] if changed else ["--all"]), "--jobs", str(jobs)]
    return base


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


def _actual_test_count(output: str) -> int | None:
    """从输出里取**真实用例数**。取不到→None（不猜）。

    认 **`[total-cases=N]` 唯一标记**——`test_changed.run_parallel` 显式报出的总数。

    为何要标记、而不从人读文本推（实测连错三轮，每次都**静默写错 README**）：
    `run_parallel` 只把**失败文件**的完整输出照抄到 stdout，成功文件没有 `Ran` 行；
    而串行输出里又可能混进**某个测试自己 spawn 的子进程**产物（它也打
    `[并行 jobs=2] … 25 用例`，与真摘要行**格式完全一样**，只有数字不同）。
    文本层面无法可靠区分 ⇒ 三轮分别错成 26 / 25 / （取不到）。
    自 `scripts/finish.py` 取消串行开关后只剩一条路径，这类歧义整类消失。

    回落：无标记时可当串行 `discover` 的输出（取**唯一**那条 `Ran N tests`）。
    多条 = 混进子进程产物，拿不准 ⇒ None（宁可不同步，也不静默写错）。
    """
    marked = re.search(r"\[total-cases=(\d+)\]", output)
    if marked:
        return int(marked.group(1))
    ran = re.findall(r"^Ran (\d+) tests?", output, re.MULTILINE)
    return int(ran[0]) if len(ran) == 1 else None


def sync_readme_count(count: int) -> str | None:
    """把 README 里的测试数改成真实值。返回**旧值**（无变化则 None）。

    为何要有它（实测踩到）：README 声明一个测试数，而 `tests/test_docs.py` 有一条门
    钉住「声明数 == 当场装载数」。后果是**每加一个测试就要手改 README**——
    本轮撞了 5 次。纯机械劳动，且手改错一个数字就红。

    只改那**一个位置**（与门的正则同形），不碰 README 其余内容。
    找不到恰好一处 → 返回 None（契约变了不猜，门会如实报红）。
    """
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(python3 -m unittest discover -s tests -t tests\s+#\s*)(\d+)(\s+passed)")
    found = pattern.findall(text)
    if len(found) != 1:
        return None
    old = int(found[0][1])
    if old == count:
        return None
    path.write_text(pattern.sub(lambda m: f"{m.group(1)}{count}{m.group(3)}", text),
                    encoding="utf-8")
    return str(old)


def _tests_summary(output: str) -> str:
    """取一行摘要：**并行**（每文件一进程）或**串行**（`Ran N tests` + `OK|FAILED`）两种格式；
    取不到就如实说。"""
    par = re.search(r"\[并行 jobs=\d+\] (\d+) 个测试文件 · (\d+) 通过 · (\d+) 失败",
                    output)
    if par:
        total, passed, failed = par.groups()
        return f"{'OK' if failed == '0' else 'FAILED'}（{total} 文件 · {passed} 通过 · {failed} 失败）"
    ran = re.search(r"^Ran (\d+) tests?", output, re.MULTILINE)
    verdict = "OK" if re.search(r"^OK", output, re.MULTILINE) else "FAILED"
    return f"{verdict}（{ran.group(1) if ran else '?'} tests）"


def _stale_lines(check_output: str) -> list[str]:
    """从 `check` 输出里抽出 stale 明细行（`  - id ← basis`）与摘要行（`⚠ …`）。"""
    return [ln for ln in check_output.splitlines()
            if ln.startswith(("  - ", "⚠"))]


# 失败摘要的**输出上限**：一次最多展开这么多行详情。
# 为何要上限：失败很多时会刷屏，而**读者要的是“能不能一眼看出什么错了”**。
# 截掉的部分**不丢** —— 全量输出一直在日志文件里（`rc=… · 全量日志：<path>`）。
_FAILURE_HEAD = 15


def _failure_excerpt(output: str, limit: int = _FAILURE_HEAD) -> list[str]:
    """从测试输出里抽**失败详情**（不是只有名字）—— 从 `FAIL:`/`ERROR:` 起
    连同 traceback 一起，直到下一条失败或分隔线。

    为何要它（实测踩到）：原先只打 `FAIL: <名字>`，读者知道哪个用例红了、**不知道红在哪里**
    ⇒ 只能重跑一遍并手动 grep（本机全量 90–103s，跑两遍是纯浪费）。
    本函数把“读短的那份就能定位”变成默认行为，日志仍是短的超集。
    """
    lines = output.splitlines()
    out: list[str] = []
    truncated = False
    i = 0
    while i < len(lines) and not truncated:
        line = lines[i]
        if line.startswith(("FAIL:", "ERROR:")):
            out.append(line)
            i += 1
            # 跟着 traceback 走：直到一条 ==== 分隔线（unittest 的几条串的分界）或下一条失败
            while i < len(lines) and not lines[i].startswith("===="):
                if lines[i].startswith(("FAIL:", "ERROR:")):
                    break
                if len(out) >= limit - 1:      # 留一行给“截断”提示（上限含提示行）
                    truncated = True
                    break
                out.append(lines[i])
                i += 1
            out.append("")
            continue
        i += 1
    if truncated:
        out.append("…（摘要到此截断；完整输出见下方日志文件）")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="收尾门：全量测试（硬）+ 资产健康（只读报告）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令，不跑")
    parser.add_argument("--jobs", type=int, default=0,
                        help="测试并发进程数：0=自动核数（默认）；N=每文件一进程（本机 10 核实测 ~28s）")
    parser.add_argument("--sync-readme", action="store_true",
                        help="把 README 声明的测试数同步为真实值（消除「每加一个测试就要手改 README」）")
    parser.add_argument("--changed", action="store_true",
                        help="**迭代模式**：只跑改动相关的测试子集（默认全量 = 收尾口径）。"
                             "子集自带「这不是全量」横幅；收尾仍必须跑一次不带本参数的。")
    args = parser.parse_args(argv)

    test_cmd = parallel_test_argv(args.jobs, changed=args.changed)
    steps = [("全量测试", test_cmd), ("assets check", assets_argv("check"))]
    if args.dry_run:
        for label, cmd in steps:
            print(f"{label}: {' '.join(cmd)}")
        return 0
    label = "改动子集" if args.changed else "全量测试"

    if not (ROOT / "disflow").is_dir():
        print("✗ 不在 disflow 仓根：找不到 disflow/", file=sys.stderr)
        return 2

    log = pathlib.Path(tempfile.mkstemp(prefix="disflow-finish-", suffix=".log")[1])
    rc = 0
    with log.open("w", encoding="utf-8") as handle:
        code, output = _run(test_cmd)
        handle.write(f"\n===== 全量测试 =====\n{output}\n")
        print(f"[1/2] {label}（jobs={args.jobs}）… {_tests_summary(output)}")
        if args.sync_readme:
            # **不要求全绿**：README 声明的是「当场装载多少个测试」，而 `Ran N tests`
            # 在红着时同样是真实装载数。初版要求 `code == 0` ⇒ 形成**死锁**：
            # 「数字不对 → 测试红 → 不同步 → 数字还是不对」（实测踩到）。
            real = _actual_test_count(output)
            if real is not None:
                changed = sync_readme_count(real)
                if changed:
                    print(f"        README 测试数同步：{changed} → {real}")
                    if code != 0:
                        # **必须明说**：同步发生在测试**之后**，所以「数字不同步」导致的
                        # 那次红不会因本次同步而变绿——要再跑一次才是真读数。
                        # 不说的话读者会以为同步没用（实测踩到，白跑一轮 36s）。
                        print("        ↳ 本次测试是**同步前**跑的；若红仅因这个数字，"
                              "重跑一次本命令即绿")
        if code != 0:
            rc = 1
            # 报**摘要 + 失败详情**（不只是名字）：读者读这一段就能定位，不必重跑一遍。
            for line in _failure_excerpt(output):
                print(f"        {line}" if line else "")

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
