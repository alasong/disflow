#!/bin/sh
# disflow 宿主循环（**仓外编排**，不改引擎 —— 故不触 FR-16 机检门）。
#
# 为什么要有它：disflow 只推不跑（`dispatch.py`：「spawn 归宿主」；FR-16/FR-17 引擎自身不常驻）。
# 于是「把任务挂上板」之后**不会自动发生任何事**——必须有人反复跑
#   drive → brief → start → 派活 → collect → done
# 本脚本就是那个「有人」：一个常驻的宿主循环，把上面六步压成自动重复。
#
# 分工（严格遵守，别无其它）：
#   - **引擎**（disflow）算「该派谁」，纯只读，不 spawn、不常驻；
#   - **本脚本**（宿主）负责触发、循环、调用派发器；
#   - **派发器**（`--dispatch <cmd>`，默认 `scripts/disflow-dispatch-subagent.sh`）
#     负责把 brief 变成一个真干活的 subagent —— 与引擎解耦，可替换。
#
# 用法：
#   scripts/disflow-host-loop.sh <tree> [选项]
#
# 选项：
#   --interval N      两轮之间的间隔秒数（默认 15）。用轮询而非事件：引擎是文件式的，
#                     没有可订阅的事件总线（FR-17 的三种触发都在宿主侧，这里选「显式+轮询」）。
#   --max-rounds N    最多跑几轮后退出（默认 0 = 不限，常驻）。测试/一次性用 1。
#   --once            等价 --max-rounds 1（跑完一轮就退，适合手动推进一次）。
#   --dispatch CMD    派发器命令（相对本仓根），默认 scripts/disflow-dispatch-subagent.sh
#   --dry-run         只打印「这一轮会派谁」，不真派、不落事件。
#   -h, --help        显示本说明。
#
# 退出码（**唯一真相**，绝不吞）：
#   0  树已 complete（全部节点 done）或到达 --max-rounds
#   1  循环自身出错（host-next 失败 / 派发器失败 / 状态不可解析）
#   2  树卡死（stuck：既无就绪也无在跑，却仍有 pending）—— 需要人来解，循环停下
#
# 停法：Ctrl-C，或 `bg_kill`（若用 bg_run 起的）。本脚本**不自我保活、不 fork daemon**，
# 它就是宿主进程本身——这是它与「引擎内置 daemon」的关键区别（后者会踩 FR-16）。
set -eu

# ── 0) 定位仓根 + 派发器（复用 shim 的解链纪律：软链下去掉的是链接所在目录，不是仓根） ──
self=$0
if command -v readlink >/dev/null 2>&1 && readlink -f "$0" >/dev/null 2>&1; then
    self=$(readlink -f "$0")
else
    n=0
    while [ -L "$self" ] && [ "$n" -lt 40 ]; do
        target=$(readlink "$self")
        case $target in
            /*) self=$target ;;
            *)  self=$(CDPATH='' cd -- "$(dirname -- "$self")" && pwd)/$target ;;
        esac
        n=$((n + 1))
    done
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$self")" && pwd)
root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
shim=$root/scripts/disflow-shim.sh

if [ ! -x "$shim" ]; then
    echo "✗ 找不到 disflow 启动器：${shim}（应为可执行）" >&2
    echo "  修法：chmod +x \"$shim\"" >&2
    exit 1
fi

# ── 1) 参数 ────────────────────────────────────────────────────────────────
tree=""
interval=15
max_rounds=0
dispatch="$root/scripts/disflow-dispatch-subagent.sh"
dry_run=0

while [ $# -gt 0 ]; do
    case $1 in
        -h|--help) sed -n '2,40p' "$self" | sed 's/^# \{0,1\}//'; exit 0 ;;
        --interval)   interval=${2:?--interval 需要秒数}; shift 2 ;;
        --max-rounds) max_rounds=${2:?--max-rounds 需要轮数}; shift 2 ;;
        --once)       max_rounds=1; shift ;;
        --dispatch)   dispatch=${2:?--dispatch 需要命令}; shift 2 ;;
        --dry-run)    dry_run=1; shift ;;
        -*) echo "✗ 未知选项：$1（--help 看用法）" >&2; exit 1 ;;
        *)  if [ -z "$tree" ]; then tree=$1; else
                echo "✗ 多余的参数：$1（只接一个 tree 名）" >&2; exit 1
            fi
            shift ;;
    esac
done

if [ -z "$tree" ]; then
    echo "✗ 必须给 tree 名：scripts/disflow-host-loop.sh <tree>" >&2
    exit 1
fi

# 派发器存在性**在循环前**查：否则第一轮派活时才发现，白跑一圈。
if [ "$dry_run" -eq 0 ] && [ ! -x "$dispatch" ]; then
    echo "✗ 派发器不可执行：$dispatch" >&2
    echo "  修法：chmod +x \"$dispatch\"，或用 --dispatch 指定别的，或 --dry-run 只看计划" >&2
    exit 1
fi

# 宿主统一提交（见循环里的调用点）：把**已 done** 节点在 `touch_plan` 里声明的路径提交。
#
#  为何必须做（实测）：worker 不许 commit，改动就留在工作区；下一个节点的 `done` 把
#  base_sha 之后的**所有**未提交改动当「本节点引入」，于是上一节点的产出成了「超出
#  touch_plan」的违规 ⇒ 链条卡在第二个节点。这不是门太严，是宿主漏了它该做的那一步。
#
#  只 add 声明面（不是 `git add -A`）：把无关文件卷进提交 = 宿主自己越界。
_commit_done_nodes() {
    _tree=$1
    repo=$(git rev-parse --show-toplevel 2>/dev/null) || return 0

    # 取「已 done 且 touch_plan 非空」的节点路径——由下面的 python 子进程一并做。
    # 交给 python 做 add+commit：`git add` 要逐个路径（只声明面），而 POSIX sh 的
    # 管道 while 在**子 shell** 里、`read` 又没有 `-d`——用 sh 写这段只会堆出一堆
    # 绕开 shell 语义的补丁。python 子进程直接跑完，只剩一个退出码要判。
    DISFLOW_SHIM="$shim" python3 - "$_tree" "$repo" <<'PY'
import json, os, subprocess, sys
shim = os.environ["DISFLOW_SHIM"]
tree, repo = sys.argv[1], sys.argv[2]

# 取「已 done 且 touch_plan 非空」的节点路径（去重保序）。
r = subprocess.run([shim, "status", tree, "--json"], capture_output=True, text=True)
if r.returncode != 0:
    sys.exit(0)                       # 状态取不到：不在此处报错（下一轮 host-next 会报）
try:
    payload = json.loads(r.stdout)
except json.JSONDecodeError:
    sys.exit(0)
paths, seen = [], set()
for node in (payload.get("nodes") or {}).values():
    if node.get("status") != "done":
        continue
    for p in node.get("touch_plan") or []:
        if p not in seen:
            seen.add(p); paths.append(p)

# 只 add 真实存在的路径（touch_plan 可含本节点未创建的路径）。
for p in paths:
    if os.path.exists(os.path.join(repo, p)):
        subprocess.run(["git", "-C", repo, "add", "--", p], capture_output=True)

# 无待提交内容（已提交过）→ 安静跳过。
if subprocess.run(["git", "-C", repo, "diff", "--cached", "--quiet"],
                  capture_output=True).returncode == 0:
    sys.exit(0)
ok = subprocess.run(
    ["git", "-C", repo, "commit", "-q", "-m", f"disflow({tree}): 已 done 节点的产出"],
    capture_output=True, text=True)
if ok.returncode == 0:
    print("  已提交已 done 节点的产出（宿主统一提交）")
else:
    # 提交失败（如 git 缺 user.email）**不静默**：它是下一节点被拒的根因，藏着就得靠猜。
    print(f"  ⚠️ 宿主提交失败：{(ok.stderr or '').strip()[:200]}", file=sys.stderr)
PY
    return 0
}

# ── 2) 循环 ────────────────────────────────────────────────────────────────
round=0
while :; do
    round=$((round + 1))

    # ① 算「该派谁」——纯只读。JSON 落临时文件：既喂给下面解析，也留证据给排错。
    #    ⚠️ `XXXXXX` 必须是**最后一段**（BSD mktemp 实测：`x.XXXXXX.json` 会原样造出
    #    字面文件名，第二次就撞「File exists」）—— 故后缀只能靠 `.json` 前的 X 序列保证。
    plan_json=$(mktemp "${TMPDIR:-/tmp}/disflow_${tree}_hostnext.XXXXXX")
    trap 'rm -f "$plan_json"' EXIT INT TERM

    if ! "$shim" host-next "$tree" --json >"$plan_json"; then
        echo "✗ 第 $round 轮：host-next 失败（见上方 stderr）" >&2
        exit 1
    fi

    # ② 解析状态 + 就绪节点清单。用 python3 解析（与引擎同一条 NFR-1 零依赖纪律，
    #    不引 jq）；`-c` 里只用标准库。
    #    输出三行：state / 就绪节点（空格分隔）/ 进度。
    parsed=$(python3 - "$plan_json" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    payload = json.load(fh)
state = payload.get("state") or ""
plan = payload.get("plan") or []
nodes = " ".join(entry["node"] for entry in plan)
progress = payload.get("progress") or {}
print(state)
print(nodes)
print(f"{progress.get('done', '?')}/{progress.get('tasks', '?')}")
PY
)
    state=$(printf '%s\n' "$parsed" | sed -n '1p')
    ready_nodes=$(printf '%s\n' "$parsed" | sed -n '2p')
    progress=$(printf '%s\n' "$parsed" | sed -n '3p')

    printf '⟳ 第 %s 轮 · 进度 %s · state=%s · 就绪 %s\n' \
        "$round" "$progress" "$state" "${ready_nodes:-（无）}"

    # ②b **宿主统一提交**（SKILL §3 第 4 条 / §10.3）：worker 被明令**不许 commit**，
    #     提交归宿主。这一步**不是可选的**——它被漏掉时下一节点的作用域门会把上一节点的
    #     产出当成「本节点引入的改动」而拒收（实测：节点 a 完成、节点 b 的 done 报
    #     `改动超出 touch_plan：['greeting.txt']`）——即整条链永远推不进第二个节点。
    #     只提交**已 done** 节点的 `touch_plan` 声明面（不 `git add -A`：不把无关文件卷进来）。
    #     非 git 仓 / 无改动 → 安静跳过（不是错）。
    _commit_done_nodes "$tree"

    # ③ 终止条件：完成 / 卡死，都**显式**报，不静默退出。
    case $state in
        complete)
            echo "✓ 树 $tree 已全部完成（complete）"
            exit 0 ;;
        stuck)
            echo "✗ 树 $tree 卡死（stuck）：无就绪、无在跑，但仍有 pending。" >&2
            echo "  这不是循环能解的 —— 需要人介入（改 goal / 破环 / unrequire / retry）。" >&2
            echo "  诊断：$shim drive $tree" >&2
            exit 2 ;;
    esac

    # ④ 有就绪节点 → 逐个派。**串行**派发：同波写面本就互不重叠，但 worker 是异步的，
    #    宿主一次性把整波派出去会造成「下一轮 host-next 时它们还在跑」的正常状态；
    #    这里刻意**不**等待 worker 完成 —— 派完就进下一轮，让引擎的租约/写面门去防冲突。
    if [ -n "$ready_nodes" ]; then
        for node in $ready_nodes; do
            if [ "$dry_run" -eq 1 ]; then
                echo "  [dry-run] 会派 $node"
                continue
            fi
            # 派发器拿到 (tree, node, plan_json)；它自己从 plan 里取该节点的 brief。
            if ! "$dispatch" "$tree" "$node" "$plan_json"; then
                echo "✗ 第 ${round} 轮：派发器失败于 ${node}（rc=$?）" >&2
                exit 1
            fi
            echo "  → 已派 $node"
        done
    else
        # 无就绪但也没完成/卡死 ⇒ waiting（就绪候选被在跑节点写面挡下）或 running。
        # 继续轮询是正确反应（在跑者收尾后下一轮就会放开）。
        echo "  …无就绪（${state}），等待 ${interval}s"
    fi

    # ⑤ 轮次上限（测试 / 一次性推进用）
    if [ "$max_rounds" -gt 0 ] && [ "$round" -ge "$max_rounds" ]; then
        echo "✓ 到达 --max-rounds ${max_rounds}，退出（第 ${round} 轮）"
        exit 0
    fi

    sleep "$interval"
done
