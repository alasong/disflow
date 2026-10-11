#!/bin/sh
# disflow 启动器：**在任意项目里跑 disflow**，不必每次手设两条环境变量。
#
# 为什么要有它（实测需求）：跨项目用法的最小形态是「PYTHONPATH 指向 clone 的 disflow +
# DISFLOW_STATE_DIR 配到仓外」——两条都要记、都要设，忘了第二条 worktree 就不可用
# （`doctor` 会报，但报在跑起来之后）。本脚本把这两条**自动推导**。
#
# 用法（放进 PATH 后）：
#   disflow-shim doctor
#   disflow-shim init 我的树 --goal "..."
#   disflow-shim host-next 我的树
#
# 状态目录推导（**按 git 仓分**，不按会话分）：
#   $DISFLOW_HOME（默认 ~/.disflow-projects）/<仓名>-<短哈希>/
#   —— 用仓的 root 路径算哈希，故**同名不同路径的项目不会撞**；同一项目多次调用**稳定复用**。
#   —— 放在**仓外**是硬要求（见 disflow/worktree.py:767）：worktree 要建在仓外，
#      状态目录落在仓内会被 git 追踪 ⇒ 污染改动集。
#
# 退出码：原样透传 disflow 的（0 通过 / 1 不过 / 2 环境不满足）。
set -eu

# 0) 解析软链：`$0` 指向的可能是链接，而不是真脚本。
#    为何必须有这一步（实测踩到，后果是「装了启动器却报找不到包」）：
#    文档教的是 `ln -s <仓>/scripts/disflow-shim.sh ~/bin/disflow`。
#    若直接 `dirname "$0"`，得到的是 **~/bin**（链接所在目录），再取上一级
#    ⇒ `$HOME/disflow`——一个不存在的位置。必须在推导仓根**之前**先解链。
#    POSIX 没有 `readlink -f`，所以：优先用它；不行就手动循环解链（常见场景够用）。
self=$0
if command -v readlink >/dev/null 2>&1 && readlink -f "$0" >/dev/null 2>&1; then
    self=$(readlink -f "$0")
else
    n=0
    while [ -L "$self" ] && [ "$n" -lt 40 ]; do
        target=$(readlink "$self")
        case $target in
            /*) self=$target ;;
            *)  self=$(dirname -- "$self")/$target ;;
        esac
        n=$((n + 1))
    done
fi

# 1) 定位 disflow 代码。优先 $DISFLOW_SRC；否则取本脚本所在仓的根（scripts/ 的上一级）。
if [ -n "${DISFLOW_SRC:-}" ]; then
    src=$DISFLOW_SRC
else
    # 取本脚本所在目录的父目录（即 clone 的 disflow 仓根）。已是**解链后的**路径。
    # 用 subshell 清空 CDPATH，避免它把相对路径打印出来污染取值。
    script_dir=$(CDPATH='' cd -- "$(dirname -- "$self")" && pwd)
    src=$(CDPATH='' cd -- "$script_dir/.." && pwd)
fi
if [ ! -d "$src/disflow" ]; then
    echo "✗ 找不到 disflow 包：$src/disflow 不存在" >&2
    echo "  修法：设 DISFLOW_SRC 指向 clone 的 disflow 仓根" >&2
    exit 2
fi

# 2) 推导仓外状态目录：按**目标项目的 git 仓根**算稳定哈希。
#    非 git 目录 → 退回「当前目录的绝对路径」算（仍然可用，只是不享跨子目录复用）。
proj=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
home=${DISFLOW_HOME:-$HOME/.disflow-projects}
# 短哈希：优先 shasum，退回 cksum，再退回 md5（跨平台，不强依赖某个工具）。
if command -v shasum >/dev/null 2>&1; then
    key=$(printf '%s' "$proj" | shasum -a 1 | cut -c1-8)
elif command -v cksum >/dev/null 2>&1; then
    key=$(printf '%s' "$proj" | cksum | cut -d' ' -f1)
else
    key=$(printf '%s' "$proj" | md5 2>/dev/null | cut -c1-8)
fi
name=$(basename -- "$proj")

# 3) 跑。`exec` 让退出码原样透传（脚本不吞 rc —— 吞了就是假绿）。
PYTHONPATH="$src${PYTHONPATH:+:$PYTHONPATH}" \
DISFLOW_STATE_DIR="$home/$name-$key" \
exec "$(command -v python3)" -m disflow.cli "$@"
