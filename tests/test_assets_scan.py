"""项目资产登记：校验拒收 + 可信依赖派生 + 索引防漂移。

三件事分层测（互相独立，失败能定位）：
1. `validate` 的拒收面（悬空依据 / verified 缺证据 / premises 成环）——纯函数，不碰真仓。
2. `derive` 的 stale 规则——用替身固定 git 事实，测规则本身而非环境。
3. `assets/index.jsonl` 与 `assets/registry.json` 的**稳定字段**一致——防「改了注册表忘跑 scan」。
   只比稳定字段（id/type/path/purpose/tier/premises），不比 trust/hash/dirty（那些随工作区变）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_scan() -> Any:
    spec = importlib.util.spec_from_file_location(
        "_assets_scan", ROOT / "scripts" / "assets_scan.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)          # `__name__ != __main__` ⇒ 不跑 CLI
    return module


def _asset(**over) -> dict:
    base = {"type": "doc", "path": "README.md", "purpose": "p", "tier": "asserted"}
    base.update(over)
    return base


class ValidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scan = _load_scan()

    def _reject(self, assets: dict) -> str:
        with self.assertRaises(SystemExit) as ctx:
            self.scan.validate(assets)
        return str(ctx.exception)

    def test_clean_registry_passes(self):
        self.scan.validate({"a": _asset()})          # 不抛即通过

    def test_dangling_premise_rejected(self):
        msg = self._reject({"a": _asset(premises=["nope"])})
        self.assertIn("未登记资产", msg)

    def test_verified_without_evidence_rejected(self):
        msg = self._reject({"a": _asset(tier="verified")})
        self.assertIn("evidence", msg)

    def test_verified_with_evidence_passes(self):
        self.scan.validate({"a": _asset(tier="verified", evidence="true")})

    def test_cycle_rejected(self):
        msg = self._reject({
            "a": _asset(premises=["b"]),
            "b": _asset(premises=["a"]),
        })
        self.assertIn("成环", msg)

    def test_missing_path_rejected(self):
        msg = self._reject({"a": _asset(path="docs/nope-不存在.md")})
        self.assertIn("path 不存在", msg)


class StaleRuleTest(unittest.TestCase):
    """固定 git 事实测 stale 规则；不依赖当前工作区脏不脏。"""

    def setUp(self) -> None:
        self.scan = _load_scan()
        for attr in ("dirty", "last_commit", "content_hash"):
            self.addCleanup(setattr, self.scan, attr, getattr(self.scan, attr))
        self.scan.content_hash = lambda _rel: "h"
        self.scan.last_commit = lambda _rel: 100
        self.scan.dirty = lambda _rel: False

    def _rows(self, assets: dict) -> dict:
        return {r["id"]: r for r in self.scan.derive(assets)}

    def test_dirty_premise_marks_dependent_stale(self):
        self.scan.dirty = lambda rel: rel == "disflow/"
        rows = self._rows({
            "pkg": _asset(type="code", path="disflow/"),
            "doc": _asset(premises=["pkg"]),
        })
        self.assertEqual(rows["doc"]["trust"], "stale")
        self.assertEqual(rows["doc"]["stale_basis"], ["pkg"])
        self.assertEqual(rows["pkg"]["trust"], "asserted")

    def test_newer_premise_marks_dependent_stale(self):
        self.scan.last_commit = lambda rel: 200 if rel == "disflow/" else 100
        rows = self._rows({
            "pkg": _asset(type="code", path="disflow/"),
            "doc": _asset(premises=["pkg"]),
        })
        self.assertEqual(rows["doc"]["trust"], "stale")

    def test_clean_premises_keep_tier(self):
        rows = self._rows({
            "pkg": _asset(type="code", path="disflow/"),
            "doc": _asset(premises=["pkg"]),
        })
        self.assertEqual(rows["doc"]["trust"], "asserted")

    def test_reverse_impact_is_transitive(self):
        assets = {
            "a": _asset(premises=[]),
            "b": _asset(premises=["a"]),
            "c": _asset(premises=["b"]),
        }
        self.assertEqual(self.scan.reverse_impact(assets, "a"), ["b", "c"])
        self.assertEqual(self.scan.reverse_impact(assets, "c"), [])


class IndexSyncTest(unittest.TestCase):
    """生成索引与手写注册表不许漂移（只比稳定字段）。"""

    def test_index_matches_registry(self):
        registry = json.loads((ROOT / "assets/registry.json").read_text(encoding="utf-8"))
        assets = registry["assets"]
        lines = (ROOT / "assets/index.jsonl").read_text(encoding="utf-8").splitlines()
        index = {r["id"]: r for r in (json.loads(line) for line in lines if line)}

        self.assertEqual(set(index), set(assets),
                         "index.jsonl 与 registry.json 的资产集不一致：改注册表后要跑 scan")
        for aid, decl in assets.items():
            row = index[aid]
            for key in ("type", "path", "purpose", "tier"):
                self.assertEqual(row[key], decl[key], f"{aid}.{key} 漂移")
            self.assertEqual(row["premises"], list(decl.get("premises") or []),
                             f"{aid}.premises 漂移")


if __name__ == "__main__":
    unittest.main()
