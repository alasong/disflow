"""disflow · web 入口（本地只读看板 + 资产表 + 任务下发）。

**定位：薄壳，不是第二实现。** 一切读事实走 `Engine` / `views` / `assets/index.jsonl`，
一切写操作走 `Engine` 的动词（`expand`/`start`/`collect`/`done`…），
**本模块一行都不重写**看板布局、就绪判定、门或锁 —— 那些的单一实现在
`views.board_layout` / `dispatch.drive` / `gates` / `Engine._write`。

为何强调这一点：本仓纪律是「同一判据两份实现 ⇒ 两面分叉」（`docs/design.md` §6）。
web 只是**第三种渲染**（人读态 = 终端文本、机器态 = `--json`），故它消费的必须是
`board --json` 的同一份结构（`views.board_layout` 的返回值）。

**写面的安全性来源（不是本模块自己实现的）**：`Engine._write` 在
`store.lock`（`flock`）内做「读投影 → 校验 → append 事件」，跨进程互斥已由它保证
（`store.lock` → `file_lock(<state dir>/lock)`）。web 的写端点若改成直接改 `state.json`，
就绕过了唯一的写点与门 —— 那是**禁止**的。

**零第三方依赖**（NFR-1）：只用标准库 `http.server` / `json` / `urllib`。
**按需启停**：`python3 -m disflow.web` 才起服务，Ctrl-C 即停 —— 不是常驻后台进程（FR-16）；
不做 WebSocket、不引前端框架（`backlog` §E 非目标）。

用法：
    python3 -m disflow.web [--port 8765] [--host 127.0.0.1] [--cwd <repo>]
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import views
from .engine import Engine, EngineError
from .state import SPEC_ROLES

# 前端资源与 assets 索引都相对**仓根**解析（不是 cwd 猜）——`_repo_root` 由包位置推得。
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)


def _assets_index(repo: str) -> list[dict]:
    """读 `assets/index.jsonl`（机读资产契约，一行一资产）。

    文件缺失 ⇒ 空列表（web 只展示，不生成索引 —— 生成归 `scripts/assets_scan.py scan`）。
    坏行 raise（不静默跳过，与 `store.read_jsonl` 同纪律：坏数据不许伪装成「少一条」）。
    """
    path = os.path.join(repo, "assets", "index.jsonl")
    if not os.path.isfile(path):
        return []
    items: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno} 坏 JSON：{exc}") from exc
    return items


def _trees_payload(engine: Engine) -> dict:
    """每棵树的摘要（看板/运行视图共用的那批读数，同源：`Engine.status`）。"""
    out = []
    for name in engine.trees():
        status = engine.status(name)
        progress = status["progress"]
        out.append({
            "tree": name,
            "goal": status["goal"],
            "closed": status["closed"],
            "progress": progress,
            "ready": status["ready"],
            "running": status["running"],
            "stuck": status["stuck"],
        })
    return {"trees": out}


def _specs_payload(engine: Engine) -> dict:
    """Spec（交付单元）摘要 —— **与 `disflow specs --json` 同源**（同一投影、同一口径）。

    为何 web 要认 Spec（ADR 0006）：Spec 与看板/树**并列**是体系的一部分；
    只在 CLI 可见而在前台看不到，等于体系缺了一块（用户 2026-10-12 点出的正是这个）。

    `missing_roles` 是**报告**不是门（与 `assets_scan check` 同纪律）：
    缺角色只说事实，不由 web 层拒任何东西（本端点**纯读**）。
    """
    state = engine.load()
    out = []
    for spec_id in sorted((state.get("specs") or {})):
        spec = (state["specs"])[spec_id]
        assets = spec.get("assets") or {}
        roles = sorted(set(assets.values()))
        out.append({
            "spec": spec_id,
            "goal": spec.get("goal", ""),
            "closed": bool(spec.get("closed")),
            "assets": assets,
            "delivers": list(spec.get("delivers") or []),
            "roles": roles,
            "missing_roles": [r for r in SPEC_ROLES if r not in set(roles)],
        })
    return {"specs": out, "roles": list(SPEC_ROLES)}


def _board_payload(engine: Engine, tree: str, by: str) -> dict:
    """看板读数 —— **与 `disflow board --json` 同源**（同一 `views` 函数、同一参数口径）。

    `empty_columns=True` / `active_only=False`：机器视图 = 事实全量（要过滤由消费者做，
    与人读态默认折叠不同 —— 这正是 CLI `--json` 的口径，见 `cli.cmd_board`）。
    """
    target = engine.tree(tree)
    tree_boards = engine._tree_boards(target)
    stages = tuple(board.id for board in tree_boards)
    labels = {board.id: board.label for board in tree_boards}
    kwargs = {"stages": stages, "closed": engine.closed_trees(), "tree_name": tree,
              "active_only": False, "empty_columns": True}
    if by == "board":
        return views.boards_layout(target, stages, labels=labels,
                                   **{**kwargs, "empty_boards": True})
    return views.board_layout(target, None, **kwargs)


def _host_next_payload(engine: Engine, tree: str) -> dict:
    """下发任务的依据：本轮该派谁 + 每个节点的完整 brief 与收尾命令（纯读，同 `host_next`）。"""
    return engine.host_next(tree)


# ── 写动词白名单 ──────────────────────────────────────────────────────────────
# **只暴露这几个**，且全部经 `Engine` 的公开方法（= 经唯一写点、锁、门）。
# 为何白名单而不是「把 CLI 全部动词转发」：写动词里有些是不可逆或高副作用的
# （`close`/`reap`/`worktree apply`/`rescope`），把它们开到 web 上等于把风险面扩大，
# 而用户要的是「下发任务」——那只需 expand/start/collect/done/fail/skip/retry。
_WRITE_ACTIONS: dict[str, str] = {
    "expand": "expand",
    "start": "start",
    "collect": "collect",
    "done": "done",
    "fail": "fail",
    "skip": "skip",
    "retry": "retry",
}


def _apply_write(engine: Engine, action: str, payload: dict) -> dict:
    """执行一个写动词。失败一律 `EngineError`/`ValueError` ⇒ 由调用方转 4xx（不吞成 200）。

    每个分支都只是**转发**到 `Engine` 的同名公开方法 —— 校验、锁、门都在那里，
    本函数不复制任何一条。
    """
    tree = str(payload.get("tree") or "").strip()
    if not tree:
        raise ValueError("缺少 tree")
    if action == "expand":
        parent = str(payload.get("parent") or "").strip()
        children = payload.get("children")
        if not parent:
            raise ValueError("缺少 parent")
        if not isinstance(children, list) or not children:
            raise ValueError("children 必须是非空数组（节点定义列表）")
        engine.expand(tree, parent, children, holder=str(payload.get("holder") or ""))
        return {"ok": True, "action": action, "tree": tree, "parent": parent,
                "count": len(children)}
    node = str(payload.get("node") or "").strip()
    if not node:
        raise ValueError("缺少 node")
    if action == "start":
        holder = str(payload.get("holder") or "").strip()
        if not holder:
            # 租约的 holder 是「谁在干活」的追溯键，空着会让 `reap` 无从对账 ⇒ 拒收。
            raise ValueError("缺少 holder（谁领租约；用于租约追溯与 reap 对账）")
        engine.start(tree, node, holder=holder,
                     ttl=payload.get("ttl") if isinstance(payload.get("ttl"), int) else None)
    elif action == "collect":
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("缺少 result（结果契约对象：status/summary/changed_files）")
        engine.collect(tree, node, result)
    elif action == "done":
        declarations = payload.get("declarations")
        if declarations is not None and not isinstance(declarations, dict):
            raise ValueError("declarations 必须是对象（只放破例/豁免声明）")
        engine.done(tree, node, declarations=declarations or None,
                    no_result=str(payload.get("no_result") or ""))
    elif action == "fail":
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("缺少 reason（失败原因必填，便于事后追溯）")
        engine.fail(tree, node, reason=reason)
    elif action == "skip":
        engine.skip(tree, node, reason=str(payload.get("reason") or ""))
    elif action == "retry":
        engine.retry(tree, node)
    return {"ok": True, "action": action, "tree": tree, "node": node}


class Handler(BaseHTTPRequestHandler):
    """极简路由（标准库）。GET = 读事实；POST /api/write = 写动词。"""

    server_version = "disflow-web"
    cwd: str = _REPO_ROOT            # 由 serve() 覆盖

    # ── 回应助手 ──────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 本地工具：禁缓存，避免看板读数被浏览器缓存成旧值。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _fail(self, code: int, message: str) -> None:
        self._json(code, {"error": message})

    def log_message(self, format: str, *args) -> None:  # noqa: A002（标准库回调签名固定）
        if os.environ.get("DISFLOW_WEB_VERBOSE"):
            super().log_message(format, *args)

    # ── GET：读 ───────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802（标准库回调名固定）
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                return self._send(200, _INDEX_HTML.encode("utf-8"),
                                  "text/html; charset=utf-8")
            engine = Engine(cwd=self.cwd)
            if route == "/api/trees":
                return self._json(200, _trees_payload(engine))
            if route == "/api/board":
                tree = (query.get("tree") or [""])[0]
                if not tree:
                    return self._fail(400, "缺少 query 参数 tree")
                by = (query.get("by") or ["column"])[0]
                if by not in ("column", "board"):
                    return self._fail(400, f"by 取值非法：{by!r}（允许 column | board）")
                return self._json(200, _board_payload(engine, tree, by))
            if route == "/api/specs":
                return self._json(200, _specs_payload(engine))
            if route == "/api/assets":
                return self._json(200, {"assets": _assets_index(self.cwd)})
            if route == "/api/host-next":
                tree = (query.get("tree") or [""])[0]
                if not tree:
                    return self._fail(400, "缺少 query 参数 tree")
                return self._json(200, _host_next_payload(engine, tree))
            return self._fail(404, f"未知路径：{route}")
        except EngineError as exc:
            return self._fail(404, str(exc))
        except (ValueError, OSError) as exc:
            return self._fail(500, str(exc))
        except Exception:  # noqa: BLE001（HTTP 边界：未预期异常必须留痕，不能静默断连）
            traceback.print_exc()
            return self._fail(500, "内部错误（见服务端 stderr）")

    # ── POST：写 ──────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/write":
            return self._fail(404, f"未知路径：{parsed.path}")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._fail(400, "Content-Length 非法")
        if length <= 0 or length > 1_000_000:
            return self._fail(400, "请求体为空或过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._fail(400, f"请求体不是合法 JSON：{exc}")
        if not isinstance(payload, dict):
            return self._fail(400, "请求体必须是 JSON 对象")
        action = str(payload.get("action") or "").strip()
        if action not in _WRITE_ACTIONS:
            return self._fail(400, f"action 非法：{action!r}（允许：{sorted(_WRITE_ACTIONS)}）")
        try:
            engine = Engine(cwd=self.cwd)
            return self._json(200, _apply_write(engine, action, payload))
        except EngineError as exc:
            # 门拒收 / 状态非法 / 未就绪 —— 是**用户的错**，4xx（消息里带缺项清单）
            return self._fail(409, str(exc))
        except (ValueError, OSError) as exc:
            return self._fail(400, str(exc))
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return self._fail(500, "内部错误（见服务端 stderr）")


def serve(host: str = "127.0.0.1", port: int = 8765, cwd: str | None = None) -> None:
    """起服务（阻塞）。默认只绑 `127.0.0.1` —— 这是**本地工具**，不是网络服务。"""
    Handler.cwd = cwd or os.getcwd()
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"disflow web：http://{host}:{port}/  （cwd={Handler.cwd}）")
    print("只读端点 /api/trees /api/board /api/assets /api/specs /api/host-next；"
          "写端点 POST /api/write")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="disflow.web",
                                     description="disflow 本地 web 入口（看板 + 资产 + 下发任务）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cwd", default=None, help="仓根（默认当前目录）")
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.cwd)
    return 0


_INDEX_HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>disflow</title>
<style>
:root { --bg:#11151c; --panel:#171d26; --line:#242c39; --fg:#dbe3ee; --dim:#7c8798;
        --ok:#3fb950; --run:#d29922; --bad:#f85149; --ready:#58a6ff; --stale:#a371f7; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
header { padding:10px 16px; border-bottom:1px solid var(--line); display:flex;
         gap:16px; align-items:center; position:sticky; top:0; background:var(--bg); z-index:5; }
h1 { font-size:14px; margin:0; font-weight:600; }
.tabs { display:flex; gap:4px; }
.tab { padding:4px 10px; border:1px solid var(--line); border-radius:5px; cursor:pointer; color:var(--dim); }
.tab.on { color:var(--fg); border-color:var(--ready); }
main { padding:16px; }
.cols { display:flex; gap:12px; overflow-x:auto; align-items:flex-start; }
.col { background:var(--panel); border:1px solid var(--line); border-radius:6px;
       min-width:260px; flex:0 0 auto; max-width:420px; }
.col > h3 { margin:0; padding:8px 10px; border-bottom:1px solid var(--line);
            font-size:12px; font-weight:600; }
.col > h3.bad { color:var(--stale); }
.grp { padding:6px 10px 2px; color:var(--dim); font-size:11px; }
.row { padding:4px 10px 4px 10px; border-radius:4px; display:flex; gap:6px; align-items:baseline; }
.row:hover { background:#1d2531; }
.row .mk { flex:0 0 auto; }
.row .id { color:var(--fg); font-weight:600; }
.row .goal { color:var(--dim); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.g-done .mk { color:var(--ok); } .g-running .mk { color:var(--run); }
.g-ready .mk { color:var(--ready); } .g-failed .mk { color:var(--bad); }
.g-blocked .mk, .g-deferred .mk, .g-skipped .mk, .g-other .mk { color:var(--dim); }
table { border-collapse:collapse; width:100%; }
th,td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--dim); font-weight:600; position:sticky; top:49px; background:var(--bg); }
tr.stale td { background:#1e1630; }
.badge { padding:1px 6px; border-radius:4px; font-size:11px; border:1px solid var(--line); }
.b-verified { color:var(--ok); border-color:var(--ok); }
.b-asserted { color:var(--ready); border-color:var(--ready); }
.b-stub { color:var(--dim); }
.b-stale { color:var(--stale); border-color:var(--stale); }
.mono { color:var(--dim); }
button { background:#1f2a37; color:var(--fg); border:1px solid var(--line);
         border-radius:5px; padding:3px 9px; cursor:pointer; font:inherit; }
button:hover { border-color:var(--ready); }
dialog { background:var(--panel); color:var(--fg); border:1px solid var(--line);
         border-radius:8px; width:min(760px,92vw); }
dialog::backdrop { background:rgba(0,0,0,.6); }
textarea { width:100%; height:220px; background:#0d1117; color:var(--fg);
           border:1px solid var(--line); border-radius:5px; padding:8px; font:inherit; }
pre { background:#0d1117; border:1px solid var(--line); border-radius:5px;
      padding:8px; overflow:auto; max-height:260px; }
.err { color:var(--bad); white-space:pre-wrap; }
details summary { cursor:pointer; color:var(--dim); }
.treebar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }
.treebar button.on { border-color:var(--ready); color:var(--fg); }
.hint { color:var(--dim); }
</style>
</head>
<body>
<header>
  <h1>disflow</h1>
  <div class="tabs">
    <div class="tab on" data-view="board">看板</div>
    <div class="tab" data-view="assets">资产</div>
    <div class="tab" data-view="specs">Spec 交付</div>
  </div>
  <button type="button" id="newTask" title="向当前树的下发新节点（expand）">＋ 下发任务</button>
  <span class="hint" id="status">加载中…</span>
</header>
<main>
  <div id="board">
    <div class="treebar" id="treebar"></div>
    <div class="cols" id="cols"></div>
  </div>
  <div id="assets" style="display:none"></div>
  <div id="specs" style="display:none"></div>
</main>

<dialog id="expdlg">
  <form method="dialog" style="padding:16px">
    <h3 style="margin-top:0">下发任务 · <span id="dlgTree"></span></h3>
    <label class="hint">父节点</label>
    <input id="dlgParent" value="root" style="width:100%;background:#0d1117;color:var(--fg);border:1px solid var(--line);border-radius:5px;padding:6px">
    <p class="hint">子节点 JSON（数组，每项含 id/goal，可带 stage/acceptance/touch_plan）：</p>
    <textarea id="dlgChildren">[
  {"id": "n1", "goal": "做一件事", "acceptance": {"cmd": "true", "expect": "exit0"}}
]</textarea>
    <p><button type="button" id="dlgSubmit">下发（expand）</button>
       <button type="button" onclick="document.getElementById('expdlg').close()">取消</button></p>
    <pre id="dlgOut" style="display:none"></pre>
  </form>
</dialog>

<script>
const $ = (s) => document.querySelector(s);
let CURRENT_TREE = null, VIEW = "board", SHOW_CLOSED = false;

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const body = await r.json().catch(() => ({error: "回应不是 JSON"}));
  if (!r.ok) throw new Error(body.error || ("HTTP " + r.status));
  return body;
}

function setStatus(text, bad) {
  const el = $("#status");
  el.textContent = text;
  el.style.color = bad ? "var(--bad)" : "var(--dim)";
}

async function loadBoard() {
  const {trees} = await getJSON("/api/trees");
  const bar = $("#treebar");
  bar.innerHTML = "";
  if (!trees.length) { setStatus("（无树）"); $("#cols").innerHTML = ""; return; }
  // 默认选中「有在跑任务的树」，而非字母序第一棵。
  // 为何改（实测踩到）：trees 是 sorted() 字母序，mldev/ mlxfix 共用前缀时 mldev 恒排第一；
  // 若 mldev 已关闭（不再变化），用户打开看板看到的是**静态的那棵**，会以为「看板没反应」。
  // 判据：「正在动的那棵」才是打开看板时最想看的；全部静止时再退字母序。
  if (!CURRENT_TREE || !trees.some(t => t.tree === CURRENT_TREE)) {
    // 优先级：有 running/ready 的 → 未关闭的 → trees[0]。
    // 为何要第二档：全部静止时若直选 trees[0]，而它恰好已关闭且按钮被收起，
    // 就会「看板在显示一棵在按钮条上看不见的树」——自相矛盾。
    const live = trees.find(t => (t.running || []).length || (t.ready || []).length);
    const openTree = trees.find(t => !t.closed);
    CURRENT_TREE = (live || openTree || trees[0]).tree;
    // 若最终选中了已关闭的树（全都关了），就自动展开，让它的按钮可见
    if (trees.find(t => t.tree === CURRENT_TREE)?.closed) SHOW_CLOSED = true;
  }
  for (const t of trees) {
    // 已关闭的树默认收起（与 CLI `disflow trees` 同一纪律：做完的不占注意力）。
    // 但**不静默**：被收起的条数在末尾报出，点一下即可展开看它们。
    if (t.closed && !SHOW_CLOSED) continue;
    const b = document.createElement("button");
    b.textContent = `${t.closed ? "●" : "·"} ${t.tree} ${t.progress.done}/${t.progress.tasks}` +
                    (t.stuck ? " ⚠卡死" : "");
    if (t.tree === CURRENT_TREE) b.classList.add("on");
    b.onclick = () => { CURRENT_TREE = t.tree; loadBoard(); };
    bar.appendChild(b);
  }
  const closedCount = trees.filter(t => t.closed).length;
  if (closedCount) {
    const b = document.createElement("button");
    b.textContent = SHOW_CLOSED ? `收起 ${closedCount} 棵已完成` : `＋ ${closedCount} 棵已完成`;
    b.onclick = () => { SHOW_CLOSED = !SHOW_CLOSED; loadBoard(); };
    bar.appendChild(b);
  }
  const board = await getJSON(`/api/board?tree=${encodeURIComponent(CURRENT_TREE)}`);
  renderBoard(board);
  const cur = trees.find(t => t.tree === CURRENT_TREE);
  setStatus(`${cur.tree} · 完成 ${cur.progress.done}/${cur.progress.tasks}` +
            `（${cur.progress.pct}%）· 就绪 ${cur.ready.length} · 运行中 ${cur.running.length}` +
            (cur.stuck ? " · ⚠ 卡死" : ""));
}

function renderBoard(board) {
  const wrap = $("#cols");
  wrap.innerHTML = "";
  for (const col of board.columns) {
    const el = document.createElement("div");
    el.className = "col";
    const h = document.createElement("h3");
    h.textContent = col.header + (col.canonical ? "" : " ⚠非规范");
    if (!col.canonical) h.classList.add("bad");
    el.appendChild(h);
    for (const g of col.groups) {
      if (!g.rows.length) continue;
      const gl = document.createElement("div");
      gl.className = "grp";
      gl.textContent = g.label;
      el.appendChild(gl);
      for (const r of g.rows) {
        const row = document.createElement("div");
        row.className = "row g-" + g.group;
        row.style.paddingLeft = (10 + r.depth * 14) + "px";
        row.title = r.goal || "";
        row.innerHTML = `<span class="mk">${r.mark}</span>` +
                        `<span class="id">${esc(r.node)}</span>` +
                        `<span class="goal">${esc(r.goal || "")}</span>`;
        // 按状态给动作：就绪 → start（先开工拍基线）；运行中 → done。
        // 为何 start 必须能从界面点到：done 的门以 start 拍的基线为准，
        // 跳过 start 会让门在空基线上跑（把开工前就脏的文件算成本节点引入）。
        if (g.group === "ready") {
          const btn = document.createElement("button");
          btn.textContent = "start";
          btn.onclick = () => quickAction("start", r.node);
          row.appendChild(btn);
        } else if (g.group === "running") {
          const btn = document.createElement("button");
          btn.textContent = "done";
          btn.onclick = () => quickDone(r.node);
          row.appendChild(btn);
        }
        el.appendChild(row);
      }
    }
    wrap.appendChild(el);
  }
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

async function quickAction(action, node) {
  // start 必带 holder（后端缺 holder 直接拒收）；web 下发的租约统一记作 "web"，
  // 与 CLI 的 --holder 同名同义，便于 `reap` 对账。
  const holder = action === "start" ? "web" : undefined;
  const extra = action === "start" ? `（holder=${holder}，会拍开工基线）` : "";
  if (!confirm(`对 ${CURRENT_TREE}/${node} 跑 ${action}？${extra}`)) return;
  try {
    const payload = {action, tree: CURRENT_TREE, node};
    if (holder) payload.holder = holder;
    await getJSON("/api/write", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    setStatus(`${node} ${action} 成功`);
    await loadBoard();
  } catch (e) { setStatus(`${node} ${action} 失败：${e.message}`, true); }
}

async function quickDone(node) {
  await quickAction("done", node);
}

async function loadAssets() {
  const {assets} = await getJSON("/api/assets");
  const stale = assets.filter(a => a.trust === "stale");
  setStatus(`资产 ${assets.length} 条 · stale ${stale.length} 条`);
  const rows = assets.map(a => {
    const cls = a.trust === "stale" ? ' class="stale"' : "";
    return `<tr${cls}>
      <td class="mono">${esc(a.id)}</td>
      <td><span class="badge b-${esc(a.trust)}">${esc(a.trust)}</span></td>
      <td class="mono">${esc(a.path)}</td>
      <td>${esc(a.purpose || "")}</td>
      <td class="mono">${(a.premises || []).map(p => typeof p === "string" ? p : p.id).join(", ")}</td>
      <td class="mono">${a.trust === "stale" ? (a.stale_basis || []).join(", ") : ""}</td>
    </tr>`;
  }).join("");
  $("#assets").innerHTML = `<table><thead><tr>
    <th>id</th><th>trust</th><th>path</th><th>purpose</th><th>premises</th><th>stale 依据</th>
  </tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadSpecs() {
  const {specs, roles} = await getJSON("/api/specs");
  const open = specs.filter(s => !s.closed).length;
  setStatus(`Spec ${specs.length} 个 · 开着 ${open} 个`);
  if (!specs.length) {
    $("#specs").innerHTML = '<p class="hint">（无 Spec）——用 <code>disflow spec-init &lt;id&gt; --goal "…"</code> 建一个交付单元。</p>';
    return;
  }
  const blocks = specs.map(s => {
    // 角色格：已覆盖的角色打勾并列出资产；缺的标红（**报告**，不拦任何动作）
    const cells = roles.map(r => {
      const hit = Object.entries(s.assets).filter(([, role]) => role === r).map(([a]) => a);
      return hit.length
        ? `<span class="badge b-verified" title="${esc(r)}">✓ ${esc(r)}</span> <span class="mono">${hit.map(esc).join(", ")}</span>`
        : `<span class="badge b-stale" title="缺这个角色">✗ ${esc(r)}</span>`;
    }).join("<br>");
    return `<div class="treecard">
      <h3>${esc(s.spec)} ${s.closed ? '<span class="badge">已关闭</span>' : ''}</h3>
      <p class="hint">${esc(s.goal || "（未写 goal）")}</p>
      <p>交付 ${s.delivers.length} 条 · 角色 ${s.roles.length}/${roles.length}</p>
      <div>${cells}</div>
    </div>`;
  }).join("");
  $("#specs").innerHTML = blocks;
}

document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("on"));
    tab.classList.add("on");
    VIEW = tab.dataset.view;
    $("#board").style.display = VIEW === "board" ? "" : "none";
    $("#assets").style.display = VIEW === "assets" ? "" : "none";
    $("#specs").style.display = VIEW === "specs" ? "" : "none";
    const loaders = {board: loadBoard, assets: loadAssets, specs: loadSpecs};
    (loaders[VIEW] || loadBoard)().catch(e => setStatus(e.message, true));
  };
});

// 「＋ 下发任务」→ 打开 expand 对话框。
// 为何要显式接线：dialog 与提交逻辑原本都在，唯独没有 showModal ——
// 界面上的入口因此**不存在**（能看见对话框的 DOM，却没有任何路径打开它）。
$("#newTask").onclick = () => {
  if (!CURRENT_TREE) { setStatus("先选一棵树", true); return; }
  $("#dlgTree").textContent = CURRENT_TREE;
  $("#dlgOut").style.display = "none";
  $("#expdlg").showModal();
};

$("#dlgSubmit").onclick = async () => {
  let children;
  try { children = JSON.parse($("#dlgChildren").value); }
  catch (e) { $("#dlgOut").style.display = ""; $("#dlgOut").className = "err";
              $("#dlgOut").textContent = "JSON 解析失败：" + e.message; return; }
  try {
    const res = await getJSON("/api/write", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({action: "expand", tree: CURRENT_TREE,
                            parent: $("#dlgParent").value.trim(), children})
    });
    $("#dlgOut").style.display = ""; $("#dlgOut").className = "";
    $("#dlgOut").textContent = "✓ 下发 " + res.count + " 个节点";
    await loadBoard();
  } catch (e) {
    $("#dlgOut").style.display = ""; $("#dlgOut").className = "err";
    $("#dlgOut").textContent = "✗ " + e.message;
  }
};

loadBoard().catch(e => setStatus(e.message, true));
setInterval(() => {
  if (VIEW === "board") loadBoard().catch(() => {});
  else loadAssets().catch(() => {});
}, 5000);
</script>
</body>
</html>
"""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
