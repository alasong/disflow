"""disflow · 门与证据。

门的判据：验收命令跑过 + 期望匹配（FR-18）；证据元数据由引擎自算（FR-19）；
拒收给缺项清单 + 契约片段（FR-20）。
"""
from __future__ import annotations

import hashlib
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


def _file_digest(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
    except OSError:
        return ""                      # 读不到 ⇒ 与「指纹不同」同义，保守判为已改
    return digest.hexdigest()


def fingerprint(cwd: str, relpath: str) -> str:
    """单条改动路径的**内容指纹**。目录（`git status` 会把未跟踪目录折叠成 `dir/`）
    递归按 `(相对路径, 内容指纹)` 计入；读不到返回 `""`。"""
    full = os.path.join(cwd, relpath.rstrip("/"))
    if os.path.isdir(full):
        parts: list[str] = []
        for base, _dirs, files in os.walk(full):
            for name in files:
                sub = os.path.join(base, name)
                parts.append(f"{os.path.relpath(sub, full)}:{_file_digest(sub)}")
        return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()
    return _file_digest(full)


def snapshot(cwd: str) -> dict[str, str] | None:
    """开工基线：**当前脏文件 → 内容指纹**。非 git 仓返回 None（不假装有基线）。"""
    changed = git_changed_files(cwd)
    if changed is None:
        return None
    return {path: fingerprint(cwd, path) for path in changed}


def newly_changed(cwd: str, baseline: dict | list | None) -> list[str] | None:
    """`done` 时归因：**相对基线内容真的变了**的路径（新出现 / 内容变 / 被删）。

    为何不用「按路径做差集」：同一文件被前一个节点弄脏后，本节点再改它时，
    差集看不见（两边的集合都有它）⇒ `introduced=[]` ⇒ **scope 门静默失效**。
    指纹能看见。

    兼容旧事件：基线是**路径列表**时，值记为 `None`（未知指纹）⇒ 一律判为已改（保守）。
    """
    changed = git_changed_files(cwd)
    if changed is None:
        return None
    base: dict = (dict.fromkeys(baseline) if isinstance(baseline, list)
                  else dict(baseline or {}))
    out: list[str] = []
    for path in sorted(set(changed) | set(base)):
        if fingerprint(cwd, path) != base.get(path):
            out.append(path)
    return out


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


def _numstat(cwd: str, args: list[str], paths: list[str]) -> dict[str, int] | None:
    """跑一次 `git diff <args> --numstat`，返回 `{路径: 增+删}`；命令本身失败返回 None。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "diff", *args, "--numstat", "--", *paths],
                              capture_output=True, text=True, timeout=30)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    per_file: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        deleted = int(parts[1]) if parts[1].isdigit() else 0
        per_file[parts[2]] = added + deleted
    return per_file


def _numstat_vs_head(cwd: str, paths: list[str]) -> dict[str, int] | None:
    """改动量读数：优先 `git diff HEAD`（**工作区 + 暂存区** vs 最后一次提交）。

    为何不用裸 `git diff`：它比的是「工作区 vs 暂存区」——**已暂存的改动对它不可见**，
    会静静地落到下面的「数整文件行数」兜底分支。实测：200 行文件改 1 行并 `git add`，
    裸 `git diff` 返回空 ⇒ 被判 200 行（真缺陷，B3 补测时查出）。

    仓里还没有提交（`HEAD` 不存在）时 `git diff HEAD` 失败，则合并
    「未暂存（`git diff`）+ 已暂存（`git diff --cached`）」——两者不相交，相加即工作区 vs HEAD。
    """
    head = _numstat(cwd, ["HEAD"], paths)
    if head is not None:
        return head
    unstaged = _numstat(cwd, [], paths)
    staged = _numstat(cwd, ["--cached"], paths)
    if unstaged is None and staged is None:
        return None                     # 不是 git 仓：不假装有读数
    merged: dict[str, int] = {}
    for part in (unstaged or {}, staged or {}):
        for path, count in part.items():
            merged[path] = merged.get(path, 0) + count
    return merged


def _is_tracked(cwd: str, path: str) -> bool:
    """路径是否被 git 跟踪（在索引里）。非 git 仓 / 命令失败 → False。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "ls-files", "--error-unmatch", "--", path],
                              capture_output=True, text=True, timeout=10)
    except OSError:
        return False
    return proc.returncode == 0


def diff_size(cwd: str, introduced: list[str] | None) -> dict | None:
    """`introduced` 文件的行数变动总计：已跟踪走 `git diff --numstat`，未跟踪数行。

    非 git 仓 / 无基线 → None（不假装有读数）。
    """
    if introduced is None:
        return None
    if not introduced:
        return {"total": 0, "per_file": {}}
    per_file = _numstat_vs_head(cwd, introduced)
    if per_file is None:
        return None
    total = sum(per_file.values())
    for path in introduced:
        if path in per_file:
            continue
        if _is_tracked(cwd, path):
            # git **知道**这个文件，只是相对 HEAD 净改动为 0（否则会出现在 numstat 里）。
            # 不能因“numstat 没这一条”就数整文件——那会把「暂存后又改回原状」算成整文件行数。
            per_file[path] = 0
            continue
        # 未跟踪的新文件：git 算不出改动量，按它的全部行数计
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
