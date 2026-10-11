"""跨体系只读对账（`scripts/reconcile.py`）回归。

四层测（互相独立，失败能定位）：
1. **一致样本 → 无差异**：构造两个账本已对上的投影/注册表，`build_findings` 必须为空、
   `main` 返回 0。
2. **每类差异 C1–C5 各一条**：各自被点名且 `main` 返回 1（走真 CLI 入口，替身注入两边事实）。
3. **只读守卫**：对真仓跑一次 CLI，`events.jsonl` / `state.json` / `registry.json` **逐字节不变**。
4. **CLI 契约**：`--json` 输出含 `ok`/`count`/`findings`，退出码与 `count` 一致。

`build_findings` 是纯函数，故 C1–C5 用**手搓 rows**（直接给 `trust`）测规则本身，不碰 git；
只有只读守卫那条跑真 CLI（才需要真文件）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_reconcile() -> Any:
    path = ROOT / "scripts" / "reconcile.py"
    spec = importlib.util.spec_from_file_location("_reconcile", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != "__main__"` ⇒ 不跑 CLI
    return module


def _asset(**over: Any) -> dict:
    base = {"type": "doc", "path": "README.md", "purpose": "p", "tier": "asserted"}
    base.update(over)
    return base


def _row(aid: str, tier: str = "asserted", trust: str | None = None) -> dict:
    return {"id": aid, "tier": tier, "trust": trust or tier}


def _state(nodes: dict, *, closed: bool = False, tree: str = "t") -> dict:
    return {"trees": {tree: {"nodes": nodes, "closed": closed}}}


class ConsistentTest(unittest.TestCase):
    """两个账本已对上 ⇒ 无差异、exit 0。"""

    def setUp(self) -> None:
        self.rec = _load_reconcile()
        self.assets = {
            "pkg": _asset(type="code", path="disflow/", tier="verified",
                          evidence="true"),
            "readme": _asset(owner="t/root"),
            "spec": _asset(spec={"required_roles": ["implementation"], "delivers": ["pkg"]}),
        }
        self.rows = [_row("pkg", tier="verified"), _row("readme"), _row("spec")]
        # 投影：root 节点 produces pkg（连上 Spec 的 deliverable），owner 落点 t/root 存在
        self.state = _state({"root": {"id": "root", "produces": ["pkg"], "touch_plan": []}})

    def test_build_findings_empty(self):
        self.assertEqual(self.rec.build_findings(self.assets, self.rows, self.state), [])

    def test_main_exit_zero(self):
        rec = self.rec
        for attr in ("_load_registry", "load_state"):
            self.addCleanup(setattr, rec, attr, getattr(rec, attr))
        self.addCleanup(setattr, rec.scan, "derive", rec.scan.derive)
        rec._load_registry = lambda: self.assets
        rec.scan.derive = lambda _a: self.rows
        rec.load_state = lambda: self.state
        self.assertEqual(rec.main([]), 0)


class EachCheckTest(unittest.TestCase):
    """C1–C5 各一条：被点名 + `main` exit 1。"""

    def setUp(self) -> None:
        self.rec = _load_reconcile()

    def _run_main(self, assets: dict, rows: list[dict], state: dict) -> tuple[int, str]:
        rec = self.rec
        for attr in ("_load_registry", "load_state"):
            self.addCleanup(setattr, rec, attr, getattr(rec, attr))
        self.addCleanup(setattr, rec.scan, "derive", rec.scan.derive)
        rec._load_registry = lambda: assets
        rec.scan.derive = lambda _a: rows
        rec.load_state = lambda: state
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rec.main([])
        return rc, buf.getvalue()

    def _checks(self, findings: list[dict]) -> list[str]:
        return [f["check"] for f in findings]

    def test_c1_owner_dangling(self):
        assets = {"a": _asset(owner="ghost/n0")}
        findings = self.rec.build_findings(assets, [_row("a")], _state({}))
        self.assertIn("C1", self._checks(findings))
        self.assertEqual(findings[0]["id"], "a")
        rc, out = self._run_main(assets, [_row("a")], _state({}))
        self.assertEqual(rc, 1)
        self.assertIn("C1", out)

    def test_c2_produces_unregistered(self):
        state = _state({"n0": {"id": "n0", "produces": ["ghost"], "touch_plan": []}})
        findings = self.rec.build_findings({}, [], state)
        self.assertIn("C2", self._checks(findings))
        self.assertEqual(findings[0]["id"], "ghost")
        rc, _out = self._run_main({}, [], state)
        self.assertEqual(rc, 1)

    def test_c3_spec_delivery_unverified(self):
        assets = {"spec": _asset(spec={"required_roles": ["implementation"],
                                       "delivers": ["pkg"]}),
                  "pkg": _asset(type="code", path="disflow/", tier="asserted")}
        findings = self.rec.build_findings(
            assets, [_row("spec"), _row("pkg", tier="asserted")], _state({}))
        self.assertIn("C3", self._checks(findings))
        rc, _out = self._run_main(assets, [_row("spec"), _row("pkg")], _state({}))
        self.assertEqual(rc, 1)

    def test_c3_verified_but_stale(self):
        assets = {"spec": _asset(spec={"required_roles": ["implementation"],
                                       "delivers": ["pkg"]}),
                  "pkg": _asset(type="code", path="disflow/", tier="verified",
                                evidence="true")}
        findings = self.rec.build_findings(
            assets, [_row("spec"), _row("pkg", tier="verified", trust="stale")],
            _state({}))
        self.assertIn("C3", self._checks(findings))

    def test_c4_spec_unlinked(self):
        assets = {"spec": _asset(spec={"required_roles": ["implementation"],
                                       "delivers": ["pkg"]}),
                  "pkg": _asset(type="code", path="disflow/", tier="verified",
                                evidence="true")}
        findings = self.rec.build_findings(
            assets, [_row("spec"), _row("pkg", tier="verified")], _state({}))
        self.assertIn("C4", self._checks(findings))
        self.assertEqual(findings[0]["id"], "spec")
        rc, _out = self._run_main(assets, [_row("spec"), _row("pkg", tier="verified")],
                                  _state({}))
        self.assertEqual(rc, 1)

    def test_c4_linked_by_owner_passes(self):
        assets = {"spec": _asset(spec={"required_roles": ["implementation"],
                                       "delivers": ["pkg"]}),
                  "pkg": _asset(type="code", path="disflow/", tier="verified",
                                evidence="true", owner="t/root")}
        state = _state({"root": {"id": "root", "produces": [], "touch_plan": []}})
        findings = self.rec.build_findings(
            assets, [_row("spec"), _row("pkg", tier="verified")], state)
        self.assertNotIn("C4", self._checks(findings))

    def test_c5_closed_tree_stale_asset(self):
        assets = {"pkg": _asset(type="code", path="disflow/")}
        rows = [_row("pkg", trust="stale")]
        state = _state({"D10": {"id": "D10", "produces": [],
                                "touch_plan": ["disflow/store.py"]}}, closed=True)
        findings = self.rec.build_findings(assets, rows, state)
        self.assertIn("C5", self._checks(findings))
        self.assertEqual(findings[0]["id"], "pkg")
        rc, _out = self._run_main(assets, rows, state)
        self.assertEqual(rc, 1)

    def test_c5_open_tree_not_reported(self):
        assets = {"pkg": _asset(type="code", path="disflow/")}
        rows = [_row("pkg", trust="stale")]
        state = _state({"D10": {"id": "D10", "produces": [],
                                "touch_plan": ["disflow/store.py"]}}, closed=False)
        findings = self.rec.build_findings(assets, rows, state)
        self.assertNotIn("C5", self._checks(findings))


class JsonOutputTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rec = _load_reconcile()

    def test_json_shape_and_rc(self):
        rec = self.rec
        assets = {"a": _asset(owner="ghost/n0")}
        for attr in ("_load_registry", "load_state"):
            self.addCleanup(setattr, rec, attr, getattr(rec, attr))
        self.addCleanup(setattr, rec.scan, "derive", rec.scan.derive)
        rec._load_registry = lambda: assets
        rec.scan.derive = lambda _a: [_row("a")]
        rec.load_state = lambda: _state({})
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = rec.main(["--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(rc, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["count"], len(payload["findings"]))
        self.assertEqual(payload["findings"][0]["check"], "C1")


class ReadOnlyGuardTest(unittest.TestCase):
    """只读守卫：真仓跑一次 CLI，两个体系的三根事实文件**逐字节不变**。"""

    WATCHED = (".disflow/events.jsonl", ".disflow/state.json", "assets/registry.json")

    @staticmethod
    def _digest(rel: str) -> str:
        return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()

    def test_cli_reads_only(self):
        before = {rel: self._digest(rel) for rel in self.WATCHED if (ROOT / rel).exists()}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/reconcile.py")],
            cwd=ROOT, capture_output=True, text=True)
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        after = {rel: self._digest(rel) for rel in before}
        self.assertEqual(before, after, "reconcile 改了事实文件（必须只读）")

    def test_real_repo_reports_c4(self):
        """本仓 owner/produces 全空 ⇒ 必须报 C4（Spec 未链接到过程），且不崩。"""
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/reconcile.py")],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("C4", proc.stdout)


if __name__ == "__main__":
    unittest.main()
