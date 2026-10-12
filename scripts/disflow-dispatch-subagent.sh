#!/bin/sh
# disflow 派发器：把「一个就绪节点的 brief」变成一个**真干活的 worker**，并跑完收尾三步。
#
# 这是宿主循环（`disflow-host-loop.sh`）与「谁真正干活」之间的**可替换接缝**：
#   - 循环只看引擎的 plan，不关心 worker 是什么；
#   - 本脚本负责 ④ 派活 + ⑤ worker 干 + ⑥ collect/done，即 SKILL §2 的七步里除
#     ① drive / ② brief（循环已给）之外的全部。
#
# 为何是这个形状（不是「循环里直接 spawn」）：
#   引擎是**文件式、无事件总线**的（FR-17）。worker 跑多久不确定（可能几分钟），
#   若循环同步等 worker 结束，就退化成串行、且一轮只能推进一层 —— 而引擎的
#   写面门/租约本来就是为了支持「同时多个在跑」。故本脚本**异步**拉起 worker，
#   把 collect/done 的收尾也交给 worker 自己（它手里就有 brief 里的命令），
#   循环立刻回去算下一轮。
#
# 契约（**worker 必须遵守**，由 brief 注入，见 SKILL §3）：
#   1. 只改 brief.touch_plan 里声明的路径；
#   2. 收尾跑 brief 里的 `collect`（status/summary/changed_files 三必填）再 `done`；
#   3. 卡住 → status 置 `blocked` 并停，**不要自己拍板改范围**；
#   4. **不许 git commit**（原地派发时宿主统一提交）。
#
# 用法：
#   scripts/disflow-dispatch-subagent.sh <tree> <node> <plan.json>
#   （plan.json = host-next --json 的输出；本脚本从 plan[].node == <node> 取 brief）
#
# 环境变量：
#   DISFLOW_WORKER_CMD   覆盖 worker 启动命令（默认 `pi -p`）。**测试用**：给一个
#                        假命令即可验证接线而不真烧 token。
#   DISFLOW_WORKER_LOG    worker 日志目录（默认仓外状态目录下的 workers/）。
#
# 退出码：0 已成功**拉起** worker（不代表 worker 干完）；非 0 = 拉起失败。
#   ⚠️ 「拉起成功」≠「活干完了」——后者看 `disflow host-next <tree>` 的 progress。
#   这是异步派发的固有语义，绝不能把 rc=0 读成「节点 done」。
set -eu

self=$0
if command -v readlink >/dev/null 2>&1 && readlink -f "$0" >/dev/null 2>&1; then
    self=$(readlink -f "$0")
fi
script_dir=$(CDPATH='' cd -- "$(dirname -- "$self")" && pwd)
root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
shim=$root/scripts/disflow-shim.sh

if [ $# -ne 3 ]; then
    echo "用法：$self <tree> <node> <plan.json>" >&2
    exit 1
fi
tree=$1
node=$2
plan_json=$3

if [ ! -f "$plan_json" ]; then
    echo "✗ 找不到 plan 文件：$plan_json" >&2
    exit 1
fi

# ── 1) 造 worker 的 task ───────────────────────────────────────────────────
# brief 是**原样给**的契约（SKILL §2：「别自己改写或补背景」）。把它 + 收尾指令
# 拼成一份自包含的 task 落盘，worker 读它即可 —— 不依赖循环进程的上下文。
task_file=$(mktemp "${TMPDIR:-/tmp}/disflow_${tree}_${node}_task.XXXXXX")
# ⚠️ **不能**在这里 trap 删 task_file：worker 是**异步**后台进程，它启动后还要读这个文件
# （默认 worker 用 `-- "$(cat "$task_file")"` 内联，但覆盖模式下假命令是**读文件**）。
# 立刻删 = 与 worker 抢文件（实测：`set -u` 下 worker 读到空内容 → unbound variable）。
# 故 task_file **故意留在临时目录**（进程退出后由系统回收），生命周期交给 worker。
# 同理：`plan_json` 属**调用方**（host-loop）所有，本脚本只读、绝不删它。

python3 - "$plan_json" "$tree" "$node" "$task_file" "$shim" <<'PY'
import json, sys
plan_path, tree, node, out_path, shim = sys.argv[1:6]
with open(plan_path, encoding="utf-8") as fh:
    payload = json.load(fh)

entry = None
for item in payload.get("plan") or []:
    if item.get("node") == node:
        entry = item
        break
if entry is None:
    # 不静默：节点不在 plan 里 = 循环给了个不该派的节点（多半是被写面挡下或已终态）
    print(f"✗ 节点 {node} 不在本轮 plan 里（就绪={[e['node'] for e in payload.get('plan') or []]}）",
          file=sys.stderr)
    sys.exit(1)

brief = entry["brief"]
# 引擎给的命令硬编码裸 `disflow`（`engine.py:_host_entry`），它只在 shim 已入 PATH 时可用。
# worker 是**独立进程**，不保证继承那个 PATH（实测：`/bin/sh: disflow: command not found`，
# rc=127，节点永远不推进）。故这里把裸 `disflow` 换成**绝对路径的 shim** —— worker 自包含，
# 不依赖宿主 PATH。shim 会自己推导 PYTHONPATH/state dir，故绝不能换回 `python3 -m disflow.cli`
# （那样会丢掉 state dir 推导，落到进程 cwd 的默认布局）。
collect_cmd = entry["collect_cmd"].replace("disflow ", f"{shim} ", 1)
done_cmd = entry["done_cmd"].replace("disflow ", f"{shim} ", 1)
# `done_cmd` 里引擎给的 `--decl '{\"evidence\": …}'` **不是合法 JSON**（`…` 是省略号），
# 照抄必炸。而 `done` 的证据是**引擎自算**的（`cmd_done`：跑 acceptance 命令），`--decl` 可省。
# 故这里直接去掉它 —— 不留一个“看着能跑其实不能”的命令给 worker 踩。
import re as _re
done_cmd = _re.sub(r"\s*--decl '.*'\s*$", "", done_cmd)
task = f"""你是 disflow 的一个 worker。干完下面这一个节点，然后停。

# 节点
树 {tree} · 节点 {node}（第 {entry['wave']} 波）· stage={entry.get('stage')} · goal: {entry.get('goal')}

# 你的 brief（原样遵守，这就是全部输入；不需要知道整棵树）
{json.dumps(brief, ensure_ascii=False, indent=2)}

# 收尾（**先替换占位符，再照抄**）
1. 干完后，把结果落盘：
   {collect_cmd}
   —— 尖括号 `<...>` 是**占位符，必须换成真值**（`<一句话>` → 你的一句话小结；
      `<路径>` → 你真改过的文件路径列表）；否则 JSON 里留着尖括号是假数据。
      status 只能是 ok / failed / **blocked**；卡住就问、置 blocked 然后停，
      **不要自己拍板改范围**。
2. 然后跑门：
   {done_cmd}
   —— 门会**自己算证据**（它去跑 acceptance 命令），不需要你传 `--decl`。
      通过才算 done；被拒收会列缺什么 —— 按缺项补齐后重跑。

# 硬约束
- 只改 touch_plan 里声明的路径。
- **不要 git commit**（原地派发时宿主统一提交）。
- 有疑问：写进 collect 的 summary 且 status=blocked，然后停。
"""
with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(task)
PY

# ── 2) 拉起 worker（异步、后台、不阻塞循环） ────────────────────────────────
# 日志落在仓外状态目录，便于事后复盘（worker 的会话也可能自己存，两者互补）。
# 取值走**同一权威来源**（`store.state_dir`：`[state].env` 指名的环境变量优先，否则 `[state].dir`）
# —— 不自己猜路径：猜错了日志会散落到仓内，污染改动集（与 state dir 落仓内同一类事故）。
# `doctor` **没有**打印状态目录的旗标（实测：只有 -h），故不能拿它取。
state_dir=$(python3 -c 'from disflow.vocab import STORAGE; from disflow import store; print(store.state_dir(STORAGE))' 2>/dev/null || true)
[ -n "$state_dir" ] || state_dir=${TMPDIR:-/tmp}
log_dir=${DISFLOW_WORKER_LOG:-$state_dir/workers}
mkdir -p "$log_dir"
log_file=$log_dir/${tree}_${node}_$(date +%Y%m%d-%H%M%S).log

# 默认 worker = pi 非交互模式。`--no-session` 不必要（留会话便于复盘，故不传）。
# `-nc`（不读 AGENTS.md/CLAUDE.md）：worker 的上下文应来自 brief，而不是项目常驻文件——
# 这正是「每任务 fresh context」的落地（SKILL §2：把当前会话历史塞给它等于扔掉唯一收益）。
worker_cmd=${DISFLOW_WORKER_CMD:-pi}

if [ "${DISFLOW_WORKER_CMD:-}" = "" ]; then
    set -- "$worker_cmd" -p --no-context-files -- "$(cat "$task_file")"
else
    # 覆盖模式：把 task 文件路径作为参数交给假命令（测试用），命令自己决定怎么读。
    # shellcheck disable=SC2086
    set -- $worker_cmd "$task_file"
fi

# `nohup ... &` + 立即返回 = 异步派发。worker 的 stdout/stderr 全进日志。
nohup "$@" >"$log_file" 2>&1 &
worker_pid=$!

# ⚠️ `${node}` 的花括号**不可省**：紧跟一个全角「（」时，bash 会把那个字节吃进变量名
# （实测 `${node}（` ✓ / `$node（` ✗ → `node�: unbound variable`，`set -u` 下直接死）。
echo "已拉起 ${node}（pid=${worker_pid}）· 日志 $log_file"
echo "  ⚠️ 拉起≠完成：进度看 $shim host-next $tree"
exit 0
