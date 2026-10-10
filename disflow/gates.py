"""disflow · 门与证据。

门的判据：验收命令跑过 + 期望匹配（FR-18）；证据元数据由引擎自算（FR-19）；
拒收给缺项清单 + 契约片段（FR-20）。
"""
from __future__ import annotations

import os
import shlex
import subprocess

from .spec import CLOSURE_ENTRIES, SEDIMENT_ENTRIES, SIZE_BUDGET_DEFAULT

DEFAULT_TIMEOUT = 120


def _check_expect(returncode: int, output: str, expect: str) -> tuple[bool, str]:
    if expect == "exit0":
        return returncode == 0, f"exit={returncode}（期望 0）"
    if expect.startswith("contains:"):
        needle = expect[len("contains:"):]
        ok = returncode == 0 and needle in output
        return ok, f"exit={returncode}；期望包含 {needle!r}"
    if expect.startswith("regex:"):
        import re
        pattern = expect[len("regex:"):]
        ok = returncode == 0 and re.search(pattern, output) is not None
        return ok, f"exit={returncode}；期望匹配 /{pattern}/"
    raise ValueError(f"acceptance.expect 非法：{expect!r}（支持 exit0 | contains:… | regex:…）")


def run_acceptance(acceptance: dict | None, cwd: str,
                   timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str, dict]:
    """跑验收命令。返回 (ok, why, meta)。命令按 argv 解析，不走 shell。

    显式豁免：`acceptance.waiver` 非空时直接放行，并把理由记进证据（可审计的破例，不是静默放行）。
    """
    waiver = str((acceptance or {}).get("waiver") or "").strip()
    if waiver:
        return True, f"显式豁免（waiver）：{waiver}", {"waiver": waiver}
    if not acceptance or not acceptance.get("cmd"):
        return False, "缺少 acceptance.cmd（节点未声明验收命令）", {}
    cmd = acceptance["cmd"]
    expect = acceptance.get("expect", "exit0")
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return False, f"acceptance.cmd 无法解析为 argv：{exc}", {"cmd": cmd}
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"验收命令超时（>{timeout}s）：{cmd}", {"cmd": cmd, "timeout": timeout}
    except OSError as exc:
        return False, f"验收命令无法执行：{exc}", {"cmd": cmd}
    output = (proc.stdout or "") + (proc.stderr or "")
    ok, why = _check_expect(proc.returncode, output, expect)
    meta = {"cmd": cmd, "expect": expect, "exit": proc.returncode, "output_tail": output[-2000:]}
    return ok, why, meta


def git_changed_files(cwd: str) -> list[str] | None:
    """工作区改动文件（含未跟踪）。非 git 仓返回 None（不假装有证据）。"""
    try:
        probe = subprocess.run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
                               capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    if probe.returncode != 0:
        return None
    try:
        status = subprocess.run(["git", "-C", cwd, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=20)
    except OSError:
        return None
    if status.returncode != 0:
        return None
    files: list[str] = []
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:                      # 重命名：取新路径
            path = path.split(" -> ", 1)[1]
        files.append(path.strip())
    return sorted(files)


def _covered(path: str, touch_plan: list[str]) -> bool:
    for entry in touch_plan:
        entry = entry.rstrip("/")
        if entry in (".", "", "/"):        # 声明整仓
            return True
        if path == entry or path.startswith(entry + "/"):
            return True
    return False


def scope_check(touch_plan: list[str], changed: list[str] | None) -> dict:
    """design_first（软）：改动集是否落在 touch_plan 内。无声明或无 git → 跳过。

    `touch_plan` 可写目录前缀；写 `.` 表示整仓（用于仓库自身有无关脏改动时）。
    """
    if not touch_plan or changed is None:
        return {"checked": False, "violations": []}
    violations = [path for path in changed if not _covered(path, touch_plan)]
    return {"checked": True, "violations": sorted(violations)}


def evidence_meta(acceptance: dict | None, cwd: str,
                  timeout: int = DEFAULT_TIMEOUT) -> tuple[bool, str, dict]:
    """采集证据：验收结果 + git 改动集 + 作用域检查。"""
    ok, why, meta = run_acceptance(acceptance, cwd, timeout)
    changed = git_changed_files(cwd)
    evidence = {
        "acceptance": meta,
        "changed_files": changed,
        "git": changed is not None,
        "acceptance_ok": ok,
        "acceptance_why": why,
    }
    return ok, why, evidence


def resolve_budget(node: dict) -> int:
    """节点预算：`context.size_budget` 优先，否则出厂默认。"""
    raw = (node.get("context") or {}).get("size_budget")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return SIZE_BUDGET_DEFAULT


def diff_size(cwd: str, introduced: list[str] | None) -> dict | None:
    """`introduced` 文件的行数变动总计：tracked 走 `git diff --numstat`，untracked 数行。

    非 git 仓 / 无基线 → None（不假装有读数）。
    """
    if introduced is None:
        return None
    if not introduced:
        return {"total": 0, "per_file": {}}
    try:
        proc = subprocess.run(["git", "-C", cwd, "diff", "--numstat", "--", *introduced],
                              capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    per_file: dict[str, int] = {}
    total = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        deleted = int(parts[1]) if parts[1].isdigit() else 0
        per_file[parts[2]] = added + deleted
        total += added + deleted
    for path in introduced:
        if path in per_file:
            continue
        full = os.path.join(cwd, path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8", errors="ignore") as handle:
                lines = sum(1 for _ in handle)
        except OSError:
            continue
        per_file[path] = lines
        total += lines
    return {"total": total, "per_file": per_file}


def tree_closure_check(tree: dict) -> tuple[bool, list[str]]:
    """树级终止条件门：树里必须有 done 的收益验证 **且** 经验沉淀。

    逃生口：树根 `context.closure = "not_applicable: <具体理由>"`（理由不能为空）。
    """
    nodes = tree.get("nodes", {})
    root = nodes.get(tree.get("root"))
    closure = ""
    if isinstance(root, dict):
        closure = str((root.get("context") or {}).get("closure") or "")
    if closure.startswith("not_applicable:") and closure.split(":", 1)[1].strip():
        return True, []
    done_entries = {node.get("entry") for node in nodes.values()
                    if node.get("kind") == "task" and node.get("status") == "done"}
    missing: list[str] = []
    if not (set(CLOSURE_ENTRIES) & done_entries):
        missing.append("缺 done 的收益验证节点（entry=evaluate）")
    if not (set(SEDIMENT_ENTRIES) & done_entries):
        missing.append("缺 done 的经验沉淀节点（entry=reflect|standardize）")
    return (not missing), missing


def _waivered(acceptance: dict | None) -> bool:
    return bool(str((acceptance or {}).get("waiver") or "").strip())


def tree_coverage_check(tree: dict) -> tuple[bool, list[str]]:
    """树级覆盖门（kaa `tree_coverage` 的 disflow 版）：分解过的树必须
    每个叶子任务「需求已登记」（goal 非空）且「有可跑判据」（acceptance.cmd 或 waiver），
    且每个非根容器真有子节点（分解不得是空壳）。

    逃生口：树根 `context.coverage = "not_applicable: <具体理由>"`（理由不能为空）。
    """
    nodes = tree.get("nodes", {})
    root_id = tree.get("root")
    root = nodes.get(root_id)
    if isinstance(root, dict):
        raw = str((root.get("context") or {}).get("coverage") or "")
        if raw.startswith("not_applicable:") and raw.split(":", 1)[1].strip():
            return True, []
    missing: list[str] = []
    for node_id, node in sorted(nodes.items()):
        if node.get("status") == "skipped":
            continue
        if node.get("kind") == "container":
            if node_id == root_id:
                continue                       # 树根允许尚未分解
            if not any(child.get("parent") == node_id for child in nodes.values()):
                missing.append(f"{node_id}: 容器没有子节点（分解是空壳）")
            continue
        if not str(node.get("goal") or "").strip():
            missing.append(f"{node_id}: 未登记目标（goal 为空）")
        acceptance = node.get("acceptance")
        if not (acceptance or {}).get("cmd") and not _waivered(acceptance):
            missing.append(f"{node_id}: 无可跑判据（acceptance.cmd）也无 waiver")
    return (not missing), missing
