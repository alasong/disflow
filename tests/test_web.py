"""web 入口测试：只读性 + 写面经 Engine（锁/门）+ 拒收路径。

三条判据（与 `.agents/skills/disflow` 的看板纪律同源）：
1. **只读端点就是只读** —— 打一遍全部 GET，`events.jsonl` / `state.json` **逐字节不变**。
2. **写面不绕过唯一写点** —— `POST /api/write` 走 `Engine`，故未就绪/状态非法的节点
   必须被**拒收**（409），不是静默成功。
3. **事实同源** —— `/api/board` 的载荷 == `views.board_layout` 的返回值（同一实现，
   web 不另算一份分列/分组）。

为何起**真** HTTP 服务（不是 mock handler）：路由、JSON 编解码、状态码、请求体解析
都在被测范围内，mock 掉这些等于只测了个空壳。
"""
from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

from base import DisflowTestCase, acceptance

from disflow import views, web
from disflow.engine import Engine


class _Handler(web.Handler):
    """把仓根指到临时目录的 handler（具名子类，供服务器构造）。

    为何不用 `type("H", (web.Handler,), {...})`：动态造类静态分析器看不见
    构造签名 ⇒ 服务器构造那行被报成类型错（不是噪声：分析器确实推不出来）。
    改成具名子类 + 实例前赋类属性。
    """

    cwd = ""


def _make_server(handler_cls: type[_Handler]) -> ThreadingHTTPServer:
    """绑 127.0.0.1 的**临时端口**（0 = 内核选）—— 并发跑测试不会抢端口。"""
    return ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)


def _decode(ctype: str, raw: bytes) -> Any:
    """按 Content-Type 解码：JSON 端点 → 对象；其余（HTML）→ 文本。"""
    text = raw.decode("utf-8")
    return json.loads(text) if "application/json" in ctype else text


class WebTest(DisflowTestCase):
    """每个用例起一个真服务（绑定 127.0.0.1 的临时端口），用完即关。"""

    def setUp(self) -> None:
        super().setUp()
        self.cwd = os.path.join(self.tmp, "repo")
        os.makedirs(self.cwd, exist_ok=True)
        os.environ["DISFLOW_CONFIG"] = os.path.join(self.cwd, "disflow.json")
        self.addCleanup(os.environ.pop, "DISFLOW_CONFIG", None)
        self.engine = Engine(cwd=self.cwd)
        self.engine.init_tree("t", "web 测试树")
        self.engine.expand("t", "root", [
            {"id": "a", "goal": "做 A", "acceptance": acceptance("A-ok")},
            {"id": "b", "goal": "做 B", "acceptance": acceptance("B-ok")},
        ])
        self.engine.require("t", "b", "a")

        # 前端资源与 assets 都相对仓根解析 —— 本用例把「仓根」指到临时目录（无 assets/ ⇒ 空表）。
        handler_cls = type("_ScopedHandler", (_Handler,), {"cwd": self.cwd})
        self.httpd = _make_server(handler_cls)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)

    # ── 请求助手（真 HTTP）────────────────────────────────────────────────
    def get(self, path: str) -> tuple[int, Any]:
        """GET。JSON 端点返回解析后的对象；非 JSON（如 `/` 的 HTML）返回文本。"""
        try:
            with urllib.request.urlopen(self._url(path), timeout=10) as r:  # noqa: S310（本机 127.0.0.1）
                return r.status, _decode(r.headers.get("Content-Type", ""), r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.headers.get("Content-Type", ""), exc.read())

    def post(self, payload: dict) -> tuple[int, Any]:
        req = urllib.request.Request(
            self._url("/api/write"),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310（同上）
                return r.status, _decode(r.headers.get("Content-Type", ""), r.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.headers.get("Content-Type", ""), exc.read())

    def _url(self, path: str) -> str:
        """拼本机 URL —— **显式钉死 scheme 与 host**（不接受外部传入的地址）。"""
        return f"http://127.0.0.1:{self.port}{path}"

    def fingerprint(self) -> tuple[str, str]:
        """事件流与投影的**逐字节指纹**（只读性的判据）。"""
        with open(os.path.join(self.tmp, "events.jsonl"), "rb") as h:
            events = h.read()
        with open(os.path.join(self.tmp, "state.json"), "rb") as h:
            state = h.read()
        return events.hex(), state.hex()

    # ── 判据 1：只读 ───────────────────────────────────────────────────────
    def test_read_endpoints_do_not_touch_state(self):
        before = self.fingerprint()
        for path in ("/", "/api/trees", "/api/board?tree=t", "/api/assets",
                     "/api/specs", "/api/host-next?tree=t"):
            status, _ = self.get(path)
            self.assertEqual(status, 200, f"{path} 应 200")
        self.assertEqual(self.fingerprint(), before, "只读端点改了状态")

    def test_index_serves_html(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<title>disflow</title>", str(body))
        # 三视图标签都在（Spec 是体系的一部分，不在前台看不到 = 缺一块）
        self.assertIn('data-view="specs"', str(body))

    def test_specs_endpoint_reports_roles_and_missing(self):
        """`/api/specs` 报交付覆盖；**缺角色是报告不是门**（HTTP 仍 200）。"""
        self.engine.spec_init("s1", "交付甲")
        self.engine.spec_link("s1", "pkg", "implementation")
        status, body = self.get("/api/specs")
        self.assertEqual(status, 200)
        specs = body["specs"]
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["spec"], "s1")
        self.assertEqual(specs[0]["assets"], {"pkg": "implementation"})
        self.assertEqual(specs[0]["roles"], ["implementation"])
        # 缺角色只报不拦（与 assets check 同纪律）——列出缺哪些，状态码仍是 200
        self.assertIn("documentation", specs[0]["missing_roles"])
        self.assertEqual(len(body["roles"]), 5)

    def test_specs_empty_is_not_an_error(self):
        """无 Spec ⇒ 200 + 空表（不 404：空是**正常状态**，不是资源缺失）。"""
        status, body = self.get("/api/specs")
        self.assertEqual(status, 200)
        self.assertEqual(body["specs"], [])

    # ── 判据 2：写面经 Engine、拒收要响亮 ────────────────────────────────
    def test_write_start_marks_running(self):
        status, body = self.post({"action": "start", "tree": "t", "node": "a",
                                  "holder": "sess-1"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["ok"])
        self.assertEqual(self.engine.status("t")["running"], ["a"])

    def test_write_rejects_unready_node(self):
        """b 依赖 a ⇒ 未就绪，必须 409 且消息里点名阻塞者。"""
        status, body = self.post({"action": "start", "tree": "t", "node": "b",
                                  "holder": "sess-1"})
        self.assertEqual(status, 409)
        self.assertIn("a", body["error"])

    def test_write_rejects_missing_holder(self):
        status, body = self.post({"action": "start", "tree": "t", "node": "a"})
        self.assertEqual(status, 400)
        self.assertIn("holder", body["error"])

    def test_write_rejects_unknown_action(self):
        """`close` / `reap` 这类高危动词**不开到 web 上**（白名单）。"""
        status, body = self.post({"action": "close", "tree": "t"})
        self.assertEqual(status, 400)
        self.assertIn("action", body["error"])

    def test_write_expand_creates_children(self):
        status, body = self.post({"action": "expand", "tree": "t", "parent": "a",
                                  "children": [{"id": "a1", "goal": "子任务"}]})
        self.assertEqual(status, 200, body)
        self.assertIn("a1", self.engine.tree("t")["nodes"])

    def test_write_expand_rejects_empty_children(self):
        status, body = self.post({"action": "expand", "tree": "t", "parent": "a",
                                  "children": []})
        self.assertEqual(status, 400)
        self.assertIn("children", body["error"])

    def test_write_done_runs_gates(self):
        """done 真跑门：验收命令通过 ⇒ 200；节点进 done。"""
        self.post({"action": "start", "tree": "t", "node": "a", "holder": "s"})
        self.post({"action": "collect", "tree": "t", "node": "a",
                   "result": {"status": "ok", "summary": "做完 A", "changed_files": []}})
        status, body = self.post({"action": "done", "tree": "t", "node": "a"})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.engine.tree("t")["nodes"]["a"]["status"], "done")

    def test_write_done_rejects_weak_waiver_reason(self):
        """破例理由质量门经 web 也生效（不是只在 CLI 上）——「无」必须被拒。"""
        self.post({"action": "start", "tree": "t", "node": "a", "holder": "s"})
        self.post({"action": "collect", "tree": "t", "node": "a",
                   "result": {"status": "ok", "summary": "做完", "changed_files": []}})
        status, body = self.post({"action": "done", "tree": "t", "node": "a",
                                  "declarations": {"scope_waiver": "无"}})
        self.assertEqual(status, 409)
        self.assertIn("理由", body["error"])

    # ── 判据 3：事实同源 ────────────────────────────────────────────────
    def test_board_payload_is_the_same_layout(self):
        status, payload = self.get("/api/board?tree=t")
        self.assertEqual(status, 200)
        expected = views.board_layout(self.engine.tree("t"), None,
                                      stages=self.engine.cfg.stages,
                                      closed=self.engine.closed_trees(),
                                      tree_name="t", active_only=False,
                                      empty_columns=True)
        self.assertEqual(payload, expected)

    def test_unknown_route_and_bad_params(self):
        self.assertEqual(self.get("/api/nope")[0], 404)
        self.assertEqual(self.get("/api/board")[0], 400)              # 缺 tree
        self.assertEqual(self.get("/api/board?tree=t&by=zzz")[0], 400)  # by 非法
        status, body = self.get("/api/board?tree=nosuch")
        self.assertEqual(status, 404)
        self.assertIn("nosuch", body["error"])


if __name__ == "__main__":
    unittest.main()
