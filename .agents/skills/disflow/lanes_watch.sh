#!/bin/sh
# 运行视图（宿主侧）—— 判据历史：v1 用文件 mtime（两个方向都会骗），v2 起改用 status.json 的 state。
# 位置：跟宿主手册（.agents/skills/disflow/）一起走；启动器按自身目录找 .py，不写死路径。
DIR=$(dirname -- "$0")
exec python3 "$DIR/lanes_watch.py" "$@"
