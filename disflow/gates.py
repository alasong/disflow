"""disflow · 门与证据。

门的判据：验收命令跑过 + 期望匹配（FR-18）；证据元数据由引擎自算（FR-19）；
拒收给缺项清单 + 契约片段（FR-20）。
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess

from .config import DEFAULTS, Config
from .vocab import CLOSURE_ENTRIES, SEDIMENT_ENTRIES


def _resolve_timeout(acceptance: dict | None, cfg: Config) -> float:
    """验收超时优先级：**节点声明 > 项目配置默认**。

    为何节点要能声明：今天 `120s` 是模块常量 ⇒ 跑 >2 分钟的验收**这道门永远过不去**，
    唯一出路是写 `waiver` —— 那等于把门关掉。"""
    declared = (acceptance or {}).get("timeout")
    if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared > 0:
        return float(declared)
    return float(cfg.acceptance_timeout)


def weak_reason(cfg: Config, reason: str) -> str | None:
    """理由是不是「写了但等于没写」。返回拒收文案；具体理由返回 None。

    融合自 kaa 的 `gates.weak_reason` —— 本仓原缺此判据，破例通道（`waiver`/
    `no_result`/`scope_waiver`/`size_waiver`）默许任意非空字符串，于是「无」「不需要」
    就能把门关掉。这里**不禁止破例**（禁掉会逼人绕过门、造出更坏的假绿），
    只要求理由**具体到能复查**。

    判据两条：① 去空白后整串命中词表（大小写不敏感）；② 长度 < 2 或纯标点 ——
    后者防「。」「??」这类符号串钻空子。
    """
    text = str(reason or "").strip()
    if not text:
        return None                      # 空理由由各自的破例通道自己判（此处不越权）
    words = {w.strip().lower() for w in (cfg.weak_reasons or ()) if w.strip()}
    if not words:
        return None                      # 词表空 = **整个判据关掉**（可配，不是写死）
    if text.lower() in words:
        return (f"理由过于笼统：{text!r} 命中敷衍词表 {sorted(words)}。"
                f"请写清「具体是什么情况、为何豁免、影响面」——理由要具体到能复查。")
    if len(text) < 2 or not any(ch.isalnum() for ch in text):
        return f"理由过短或无实质内容：{text!r}（至少要能让人看懂是什么情况）"
    return None


def claim_mismatch(cfg: Config, summary: str, cmd: str) -> str | None:
    """结果里「声称做了测试」而验收命令不像测试 —— 返回对账信号；否则 None。

    融合自 kaa 的 `gates._claim_patterns` + `test_claim_words` 对账。本仓原缺此判据：
    worker 写 `summary: "测试通过"`，而 `acceptance.cmd` 是 `true` —— 两条各自合法，
    门却看不出它们**不是一回事**。

    **信号而非拒收**（与 `vacuous_reasons` 同一纪律）：词表不可能完备，硬拒收会误杀
    合法形态。本函数只产出文案，由调用方写进 `evidence`，**不参与任何拒绝判定**。
    """
    text = str(summary or "")
    if not text.strip():
        return None
    tests = [w for w in (cfg.test_claim_words or ()) if w and w.lower() in text.lower()]
    passes = [w for w in (cfg.claim_pass_words or ()) if w and w.lower() in text.lower()]
    if not tests or not passes:
        return None
    lower_cmd = str(cmd or "").lower()
    if any(w.lower() in lower_cmd for w in (cfg.test_claim_words or ()) if w):
        return None
    return (f"声称「{'/'.join(tests)} 通过」而验收命令不含测试词：{cmd!r}"
            f"——两者不是一回事，请把真正的测试命令写进 acceptance.cmd")


def _check_expect(returncode: int, output: str, expect: str) -> tuple[bool, str]:
    if expect == "exit0":
        return returncode == 0, f"exit={returncode}（期望 0）"
    if expect.startswith("contains:"):
        needle = expect[len("contains:"):]
        ok = returncode == 0 and needle in output
        return ok, f"exit={returncode}；期望包含 {needle!r}"
    if expect.startswith("regex:"):
        pattern = expect[len("regex:"):]
        ok = returncode == 0 and re.search(pattern, output) is not None
        return ok, f"exit={returncode}；期望匹配 /{pattern}/"
    raise ValueError(f"acceptance.expect 非法：{expect!r}（支持 exit0 | contains:… | regex:…）")


def _goal_tokens(text: str, cfg: Config) -> set[str]:
    """把一句目标切成**可比对的内容词**：英文按词、中文按 2-gram，去掉停用词与单字。

    为何中文用 2-gram 而不是分词：本仓**零第三方依赖**（NFR-1），没有分词器；
    2-gram 不需要词典、跨领域可用，代价是会把「需求分析」切出「需求/求分/分析」——
    对本用途（有没有**共同词**）完全够用。
    """
    import re

    stop = {w.lower() for w in (cfg.goal_stopwords or ())}
    tokens: set[str] = set()
    for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text):
        low = word.lower()
        if low not in stop:
            tokens.add(low)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            continue
        if run not in stop:
            tokens.add(run)
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            # 2-gram 也要过停用词表：跨词边界的碎片（如「做一」「一个」）不是内容词。
            # 实测踩到：不过滤时「做一个需求分析」与「做一个日志清理」共享「做一」
            # 就被判为对齐 ⇒ 门形同虚设。
            if gram not in stop:
                tokens.add(gram)
    return tokens


def goal_overlap(cfg: Config, left: str, right: str) -> list[str]:
    """两段目标文本的**共同内容词**（升序）；任一侧切不出内容词 → 空（无从判定，不越权）。

    为何「空」而不报「不相关」：与 `_goal_tokens` 的信息不足边界一致 —— 短目标
    （「A1」）经停用词过滤后为空词集，不是「无关」。

    **不作建树/自检的主判据**：实测权威反例（`mldev` / `mlxfix`）经本切词后只共享 1 个
    通用动词「产出」—— 词面判据在它为之而写的那个 bug 上必然失效。本函数只供
    `cmd_init` 当**附加信息**打印（不参与是否警告）；主判据是结构信号（已 closed 的树 /
    已声明的 `awaits`）。
    """
    shared = _goal_tokens(str(left or ""), cfg) & _goal_tokens(str(right or ""), cfg)
    return sorted(shared)


def goal_align(cfg: Config, goal: str, claim_text: str) -> str | None:
    """产出声明是否跟节点目标**相关**（漂移门 1）—— 无关→拒收文案；相关→None。

    为何需要：`scope_check` 拦「改了不该改的文件」、`acceptance` 拦「产出不对」，
    **两者都不拦跑题** —— 验收命令写 `true` + 改动落在 `touch_plan` 内，两道门全绿，
    但内容跟目标毫无关系。「做完了但没用」属于漂移，必须在门上拦。

    判据：`goal` 与 `claim_text`（worker 的 summary / 是否真做了）的**内容词交集非空**。
    为何取「非空」而不取比例：worker 的 summary 本来就是一句话，要求词覆盖度会大量误拒。
    目标为空 ⇒ 无从比对，放行（不越权，与 `weak_reason` 的空理由同款）。
    """
    goal_text = str(goal or "").strip()
    claim = str(claim_text or "").strip()
    if not goal_text or not claim:
        return None                       # 缺一边就无从比 —— 不越权
    goal_tokens = _goal_tokens(goal_text, cfg)
    claim_tokens = _goal_tokens(claim_text, cfg)
    # 任一侧**切不出内容词** ⇒ 无从判定，放行（不越权）。
    # 实测踩到：短目标（`A1` / `做 A`）经停用词过滤后词集为空 ⇒ 交集必空 ⇒ 即使
    # 目标与声称**逐字相同**也被判跑题（门变成了噪声，把 10 个合法测试打红）。
    # 正确边界：「信息不足」与「跑题」是两回事 —— 前者不该拒收。
    if not goal_tokens or not claim_tokens:
        return None
    if goal_tokens & claim_tokens:
        return None
    return (f"产出声明与节点目标无共同内容词（疑似跑题）。目标：{goal_text[:60]!r}；"
            f"声称：{claim[:60]!r}——若确属正常（如纯重构），声明 goal_waiver 破例并写清理由")


def _py_file_issues(path: str) -> list[str]:
    """单个 .py 文件的底线问题（漂移门 2 的实现，纯 AST，不执行）。"""
    import ast

    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        return [f"读不了：{exc}"]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"语法错（ast.parse 失败）：{exc.msg} @line {exc.lineno}"]
    issues: list[str] = []
    # 函数级检查（可变默认参）**单独遍历一次**：原先写在 `ast.walk(tree)` 循环里，
    # 导致每个函数被外层每个 AST 节点各扫一遍 ⇒ 同一问题重复报 24 次（实测）。
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for default in list(fn.args.defaults) + [d for d in fn.args.kw_defaults if d is not None]:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                issues.append(f"line {default.lineno}: {fn.name} 用了可变默认参数（跨调用共享状态）")
    for node in ast.walk(tree):
        # 裸 `except:` 或 `except Exception:` 且体里无 raise —— 宽捕获吞掉真错
        if isinstance(node, ast.ExceptHandler):
            handler_type = node.type
            bare = handler_type is None
            broad = isinstance(handler_type, ast.Name) and handler_type.id in ("Exception", "BaseException")
            has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            if (bare or broad) and not has_raise:
                if bare:
                    kind = "裸 except:"
                elif isinstance(handler_type, ast.Name):
                    kind = f"except {handler_type.id}:"
                else:
                    kind = "宽 except:"
                issues.append(f"line {node.lineno}: {kind} 且未重新抛出（宽捕获掩盖真错）")
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value == True:  # noqa: E712（AST 常量比对）
                    issues.append(f"line {node.lineno}: subprocess 用了 shell=True（命令注入面）")
    return issues


def code_quality(cwd: str, changed: list[str] | None, cfg: Config) -> dict:
    """改动集里的 .py 是否过**代码底线**（漂移门 2）。返回 `{checked, files, issues}`。

    为何需要：disflow 的验收命令可以是「文件包含 X」这类弱判据，语法/危险写法无人管 ——
    改坏了语法而验收命令恰好不看那处，门就全绿。
    为何只查四条（见 `vocab.CODE_QUALITY_SUFFIXES` 旁的常量注释）：它们都是**正确性**问题，
    不是风格 —— 风格门会诱发大量无意义摩擦（kaa 实测：门拦下的多数是无害动作）。
    非 git / 改动集为空 ⇒ `checked=False`（不假装验过，与 `vacuous_reasons` 同款）。
    """
    suffixes = tuple(cfg.code_quality_suffixes or ())
    if not suffixes:
        return {"checked": False, "files": [], "issues": []}
    targets = [p for p in (changed or []) if p.endswith(suffixes)]
    if not targets:
        return {"checked": False, "files": [], "issues": []}
    issues: dict[str, list[str]] = {}
    seen: list[str] = []
    for rel in targets:
        full = os.path.join(cwd, rel)
        if not os.path.isfile(full):
            continue                       # 删除的文件不用查
        seen.append(rel)
        found = _py_file_issues(full)
        if found:
            issues[rel] = found
    if not seen:
        # 改动集里虽有 .py 后缀，但**文件都不可读/不存在**（删除或路径错）⇒ 未真验过。
        # 不假装验过（与 `vacuous_reasons` 同款）。
        return {"checked": False, "files": [], "issues": {}}
    return {"checked": True, "files": seen, "issues": issues}


def verify_real(cfg: Config, summary: str, cmd: str) -> str | None:
    """假证据**硬门**（漂移门 3）：声称跑了测试而验收命令不是测试 → 拒收文案。

    与 `claim_mismatch` 的关系：**同一判据的信号版与硬门版**，共用一套词表（不做第二份）。
    为何要升级成硬门：信号版实测没人看（`claim_mismatch` 只进 evidence）；而「声称测试通过、
    实际命令是 `true`」让「全绿」直接失真，属于必须拦的那一类。
    """
    signal = claim_mismatch(cfg, summary, cmd)
    if signal is None:
        return None
    return (f"{signal}（可声明 claim_waiver 破例，但需写清「为何声称与命令不一致仍可接受」）")


def run_acceptance(acceptance: dict | None, cwd: str,
                   cfg: Config = DEFAULTS) -> tuple[bool, str, dict]:
    """跑验收命令。返回 (ok, why, meta)。命令按 argv 解析，不走 shell。

    超时：`acceptance.timeout`（节点声明）> `cfg.acceptance_timeout`（项目配置）。
    显式豁免：`acceptance.waiver` 非空时直接放行，并把理由记进证据（可审计的破例，不是静默放行）。
    """
    waiver = str((acceptance or {}).get("waiver") or "").strip()
    if waiver:
        return True, f"显式豁免（waiver）：{waiver}", {"waiver": waiver}
    if not acceptance or not acceptance.get("cmd"):
        return False, "缺少 acceptance.cmd（节点未声明验收命令）", {}
    cmd = acceptance["cmd"]
    expect = acceptance.get("expect", "exit0")
    timeout = _resolve_timeout(acceptance, cfg)
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        return False, f"acceptance.cmd 无法解析为 argv：{exc}", {"cmd": cmd}
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"验收命令超时（>{timeout:g}s）：{cmd}", {"cmd": cmd, "timeout": timeout}
    except OSError as exc:
        return False, f"验收命令无法执行：{exc}", {"cmd": cmd}
    output = (proc.stdout or "") + (proc.stderr or "")
    ok, why = _check_expect(proc.returncode, output, expect)
    meta = {"cmd": cmd, "expect": expect, "exit": proc.returncode,
            "timeout": timeout, "output_tail": output[-cfg.output_tail:]}
    return ok, why, meta


def head_sha(cwd: str, cfg: Config = DEFAULTS) -> str | None:
    """当前 HEAD 的 sha；非 git 仓 / 尚无提交 / 命令失败 → None（不抛、不假装）。

    `start` 时记下它，`done` 时据此取「已提交」的改动——worker（无论在 worktree 里还是
    原地派发）都可能已 `git commit`，此时只读 `git status` 是空的。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=cfg.git_timeout)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _quote_path_off(cwd: str) -> list[str]:
    """git 的公共前缀参数 —— **关掉路径转义**。

    为何必须关：git 默认 `core.quotepath=true`，会把非 ASCII 路径写成
    `"docs/0010-kaa\350\236\215\345\220\210.md"`（带引号 + 八进制字节）。
    实测后果：`test_changed.py` 的改动→测试映射**对中文文件名全部失败** ⇒ 保守退化全量
    （一次 87s 而非 35s）；门的 `touch_plan` 比对同理会误判（拿转义串跟真路径比）。
    判据：本仓文档大量中文文件名，这不是边角情况。
    """
    return ["git", "-C", cwd, "-c", "core.quotepath=false"]


def _committed_files(cwd: str, base_sha: str, cfg: Config = DEFAULTS) -> list[str]:
    """相对 `base_sha` **已提交**的改动路径（`git diff --name-only <sha>`）。

    失败（sha 非法 / 非 git 仓）返回空表——不假装有读数，也不让已取到的工作区改动集消失。"""
    try:
        proc = subprocess.run([*_quote_path_off(cwd), "diff", "--name-only", base_sha],
                              capture_output=True, text=True, timeout=cfg.git_timeout)
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def git_changed_files(cwd: str, cfg: Config = DEFAULTS, *,
                      base_sha: str | None = None) -> list[str] | None:
    """工作区改动文件（含未跟踪）。非 git 仓返回 None（不假装有证据）。

    `base_sha` 给定时并上 `git diff --name-only <sha>`（**已提交**的改动）：worker（无论在
    worktree 里还是原地派发）都可能把活干完就 `git commit`，此时 `git status --porcelain`
    是空的——不并这一项 ⇒ 提交型 worker 的改动集永远为空 ⇒ 门空洞通过（fail-open）。
    `base_sha` 是 `start` 记的 HEAD。
    """
    try:
        probe = subprocess.run(["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
                               capture_output=True, text=True, timeout=cfg.git_timeout)
    except OSError:
        return None
    if probe.returncode != 0:
        return None
    try:
        # `-uall`（而非默认 `-unormal`）：未跟踪**目录**要被展开成里面的**文件**。
        # 为何必须（实测踩到）：`touch_plan` 声明的是文件（`tests/test_calc.py`），
        # 而默认模式对新目录只报 `?? tests/` ⇒ 与 `touch_plan` 对不上 ⇒ **假拒收**
        # （改对了也判「超出 touch_plan：['tests/']」）。展开后两边同粒度。
        status = subprocess.run([*_quote_path_off(cwd), "status", "--porcelain", "-uall"],
                                capture_output=True, text=True, timeout=cfg.git_timeout)
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
    if base_sha:
        return sorted(set(files) | set(_committed_files(cwd, base_sha, cfg)))
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


def fingerprint(cwd: str, relpath: str, cfg: Config = DEFAULTS) -> str:
    """单条改动路径的**内容指纹**。目录（`git status` 会把未跟踪目录折叠成 `dir/`）
    递归按 `(相对路径, 内容指纹)` 计入；读不到返回 `""`。

    递归时跳过 `cfg.walk_ignore` 里的目录名（默认含 `.git`/`node_modules`/`__pycache__`）：
    否则 `touch_plan` 写了一个包含依赖目录的目录 ⇒ 哈希整棵依赖树（慢、且毫无信息量）。
    """
    full = os.path.join(cwd, relpath.rstrip("/"))
    if os.path.isdir(full):
        ignore = set(cfg.walk_ignore)
        parts: list[str] = []
        for base, dirs, files in os.walk(full):
            dirs[:] = [d for d in dirs if d not in ignore]      # 就地剪枝，不进去
            for name in files:
                sub = os.path.join(base, name)
                parts.append(f"{os.path.relpath(sub, full)}:{_file_digest(sub)}")
        return hashlib.sha256("\n".join(sorted(parts)).encode()).hexdigest()
    return _file_digest(full)


def snapshot(cwd: str, cfg: Config = DEFAULTS) -> dict[str, str] | None:
    """开工基线：**当前脏文件 → 内容指纹**。非 git 仓返回 None（不假装有基线）。"""
    changed = git_changed_files(cwd, cfg)
    if changed is None:
        return None
    return {path: fingerprint(cwd, path, cfg) for path in changed}


def newly_changed(cwd: str, baseline: dict | list | None,
                  cfg: Config = DEFAULTS, *, base_sha: str | None = None) -> list[str] | None:
    """`done` 时归因：**相对基线内容真的变了**的路径（新出现 / 内容变 / 被删）。

    为何不用「按路径做差集」：同一文件被前一个节点弄脏后，本节点再改它时，
    差集看不见（两边的集合都有它）⇒ `introduced=[]` ⇒ **scope 门静默失效**。
    指纹能看见。

    `base_sha` 透传给 `git_changed_files`：worktree 里已提交的改动否则看不见（见该函数）。

    兼容旧事件：基线是**路径列表**时，值记为 `None`（未知指纹）⇒ 一律判为已改（保守）。
    """
    changed = git_changed_files(cwd, cfg, base_sha=base_sha)
    if changed is None:
        return None
    base: dict = (dict.fromkeys(baseline) if isinstance(baseline, list)
                  else dict(baseline or {}))
    out: list[str] = []
    for path in sorted(set(changed) | set(base)):
        if fingerprint(cwd, path, cfg) != base.get(path):
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
                  cfg: Config = DEFAULTS, *, base_sha: str | None = None) -> tuple[bool, str, dict]:
    """采集证据：验收结果 + git 改动集 + 作用域检查。"""
    ok, why, meta = run_acceptance(acceptance, cwd, cfg)
    changed = git_changed_files(cwd, cfg, base_sha=base_sha)
    evidence = {
        "acceptance": meta,
        "changed_files": changed,
        "git": changed is not None,
        "acceptance_ok": ok,
        "acceptance_why": why,
    }
    return ok, why, evidence


def vacuous_reasons(*, acceptance: dict | None, touch_plan: list[str],
                    introduced: list[str] | None, size: dict | None) -> dict[str, str]:
    """哪些门**没有东西可验**（空洞通过）—— 记进证据，别让“全过”被读成“都验了”。

    空洞**不是失败**：节点本来就可能不需要改文件（如 `evaluate` 节点的写面为空）。
    但它是**信息**——对作者来说，“作用域门没验到东西”通常意味着本节点
    没产生可检测的改动；这个信号以前是没有的（真跑循环里 `C6` 的作用域门空转，
    当时没有任何东西告知）。

    只做“记录 + 报出”，**不改变任何门的通过/拒收结果**。
    """
    reasons: dict[str, str] = {}
    if str((acceptance or {}).get("waiver") or "").strip():
        reasons["acceptance"] = "显式 waiver：验收命令未执行"
    if introduced is None:
        reasons["design_first"] = "无改动集（非 git 仓）：无从比对"
        reasons["size_budget"] = "无规模读数（非 git 仓）"
        return reasons
    if not touch_plan:
        reasons["design_first"] = "touch_plan 为空：无写面可检查"
    elif not introduced:
        reasons["design_first"] = "本节点未引入任何改动（introduced 为空）"
    if size is None:
        reasons["size_budget"] = "无规模读数"
    elif size.get("total") == 0:
        reasons["size_budget"] = "改动行数为 0"
    return reasons


def resolve_budget(node: dict, cfg: Config = DEFAULTS) -> int:
    """节点预算：`context.size_budget` 优先，否则项目配置默认（再否则出厂默认）。"""
    raw = (node.get("context") or {}).get("size_budget")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return cfg.size_budget


def _numstat(cwd: str, args: list[str], paths: list[str],
             cfg: Config = DEFAULTS) -> dict[str, int] | None:
    """跑一次 `git diff <args> --numstat`，返回 `{路径: 增+删}`；命令本身失败返回 None。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "diff", *args, "--numstat", "--", *paths],
                              capture_output=True, text=True, timeout=cfg.git_timeout)
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


def _numstat_vs_head(cwd: str, paths: list[str],
                     cfg: Config = DEFAULTS) -> dict[str, int] | None:
    """改动量读数：优先 `git diff HEAD`（**工作区 + 暂存区** vs 最后一次提交）。

    为何不用裸 `git diff`：它比的是「工作区 vs 暂存区」——**已暂存的改动对它不可见**，
    会静静地落到下面的「数整文件行数」兜底分支。实测：200 行文件改 1 行并 `git add`，
    裸 `git diff` 返回空 ⇒ 被判 200 行（真缺陷，B3 补测时查出）。

    仓里还没有提交（`HEAD` 不存在）时 `git diff HEAD` 失败，则合并
    「未暂存（`git diff`）+ 已暂存（`git diff --cached`）」——两者不相交，相加即工作区 vs HEAD。
    """
    head = _numstat(cwd, ["HEAD"], paths, cfg)
    if head is not None:
        return head
    unstaged = _numstat(cwd, [], paths, cfg)
    staged = _numstat(cwd, ["--cached"], paths, cfg)
    if unstaged is None and staged is None:
        return None                     # 不是 git 仓：不假装有读数
    merged: dict[str, int] = {}
    for part in (unstaged or {}, staged or {}):
        for path, count in part.items():
            merged[path] = merged.get(path, 0) + count
    return merged


def _numstat_vs_base(cwd: str, base_sha: str | None, paths: list[str],
                     cfg: Config = DEFAULTS) -> dict[str, int] | None:
    """改动量读数：`git diff <base_sha>`（**已提交 + 工作区**）∪ 工作区 numstat。

    为何要 `base_sha`：worker 把活干完就 `git commit` 时 `HEAD` 已含改动 ⇒
    `git diff HEAD` 是 0 ⇒ `size.total=0`、`vacuous=[size_budget]`——**已提交改动恒被判为空**
    （宿主实测：4/4 节点 `introduced` 非空而 `size.total=0`）。与 `git_changed_files` 同一做法：
    并上「相对 `start` 记的 HEAD 的改动」。

    取 **max 而非相加**：`git diff <base_sha>` 比的是 base_sha vs 工作区，**已含**工作区改动，
    与 `git diff HEAD`（工作区 vs HEAD）相加会把它算两遍。`base_sha` 缺省（旧事件）
    与无此参数时逐字节一致——只读工作区。
    """
    workspace = _numstat_vs_head(cwd, paths, cfg)
    if not base_sha:
        return workspace
    committed = _numstat(cwd, [base_sha], paths, cfg)
    if committed is None:
        return workspace
    if workspace is None:
        return committed
    merged = dict(workspace)
    for path, count in committed.items():
        merged[path] = max(merged.get(path, 0), count)
    return merged


def _is_tracked(cwd: str, path: str, cfg: Config = DEFAULTS) -> bool:
    """路径是否被 git 跟踪（在索引里）。非 git 仓 / 命令失败 → False。"""
    try:
        proc = subprocess.run(["git", "-C", cwd, "ls-files", "--error-unmatch", "--", path],
                              capture_output=True, text=True, timeout=cfg.git_timeout)
    except OSError:
        return False
    return proc.returncode == 0


def diff_size(cwd: str, introduced: list[str] | None,
              cfg: Config = DEFAULTS, *, base_sha: str | None = None) -> dict | None:
    """`introduced` 文件的行数变动总计：已跟踪走 `git diff --numstat`，未跟踪数行。

    `base_sha` 透传给 `_numstat_vs_base`：worker 已 `git commit` 时否则恒读 0
    （`git diff HEAD` 看不见已提交改动 ⇒ `size_budget` 恒空洞）。

    非 git 仓 / 无基线 → None（不假装有读数）。
    """
    if introduced is None:
        return None
    if not introduced:
        return {"total": 0, "per_file": {}}
    per_file = _numstat_vs_base(cwd, base_sha, introduced, cfg)
    if per_file is None:
        return None
    total = sum(per_file.values())
    for path in introduced:
        if path in per_file:
            continue
        if _is_tracked(cwd, path, cfg):
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
