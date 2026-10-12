"""项目资产登记：校验拒收 + 可信依赖派生 + 索引防漂移。

三件事分层测（互相独立，失败能定位）：
1. `validate` 的拒收面（悬空依据 / verified 缺证据 / premises 成环）——纯函数，不碰真仓。
2. `derive` 的 stale 规则——用替身固定 git 事实，测规则本身而非环境。
3. `assets/index.jsonl` 与 `assets/registry.json` 的**稳定字段**一致——防「改了注册表忘跑 scan」。
   只比稳定字段（id/type/path/purpose/tier/premises），不比 trust/hash/dirty（那些随工作区变）。
4. **依据边分型**（`anchors`/`generated_from`/`verifies`/`documents`）与 **Spec 交付门**
   （`spec` 块的角色覆盖、`delivery` 缺角色 rc=1）——这两组是本轮新增机制。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import tempfile
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

    def test_invalid_category_rejected(self):
        msg = self._reject({"a": _asset(category="bogus")})
        self.assertIn("category 非法", msg)


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

    def test_order_puts_premises_before_dependents(self):
        """刷新顺序：依据必须排在被依据之前（拓扑序）。"""
        assets = {
            "a": _asset(premises=[]),
            "b": _asset(premises=["a"]),
            "c": _asset(premises=["b"]),
        }
        order = self.scan.topo_order(assets)
        self.assertLess(order.index("a"), order.index("b"))
        self.assertLess(order.index("b"), order.index("c"))

    def test_plan_is_touched_plus_downstream_in_topo_order(self):
        """刷新计划 = 命中的资产 ∪ 其下游，按拓扑序；无关资产不进。"""
        assets = {
            "a": _asset(type="code", path="pkg/", premises=[]),
            "b": _asset(path="docs/b.md", premises=["a"]),
            "c": _asset(path="docs/c.md", premises=[]),
        }
        plan = self.scan.refresh_plan(assets, ["pkg/x.py"])
        ids = [aid for aid, _ in plan]
        self.assertEqual(ids, ["a", "b"])

    def test_typed_premise_drives_stale(self):
        """`dict` 形式的依据与字符串等价——**分型不改变 stale 的判定**。"""
        self.scan.dirty = lambda rel: rel == "disflow/"
        rows = self._rows({
            "pkg": _asset(type="code", path="disflow/"),
            "doc": _asset(premises=[{"id": "pkg", "kind": "generated_from"}]),
        })
        self.assertEqual(rows["doc"]["trust"], "stale")
        self.assertEqual(rows["doc"]["stale_basis"], ["pkg"])


class RelationKindTest(unittest.TestCase):
    """依据边分型：纯字符串 = `anchors`（默认）；已知 kind 收，非法 kind / 缺 id 拒。"""

    def setUp(self) -> None:
        self.scan = _load_scan()

    def _reject(self, assets: dict) -> str:
        with self.assertRaises(SystemExit) as ctx:
            self.scan.validate(assets)
        return str(ctx.exception)

    def test_plain_string_defaults_to_anchors(self):
        self.assertEqual(self.scan.premise_kind("pkg"), "anchors")
        self.assertEqual(self.scan.premise_kind({"id": "pkg"}), "anchors")
        self.assertEqual(self.scan.premise_id({"id": "pkg"}), "pkg")
        self.assertEqual(
            self.scan.premise_ids(["a", {"id": "b", "kind": "verifies"}]), ["a", "b"])

    def test_known_kinds_accepted(self):
        self.scan.validate({
            "a": _asset(),
            "gen": _asset(premises=[{"id": "a", "kind": "generated_from"}]),
            "ver": _asset(premises=[{"id": "a", "kind": "verifies"}]),
            "doc": _asset(premises=[{"id": "a", "kind": "documents"}]),
        })

    def test_illegal_kind_rejected(self):
        msg = self._reject({"a": _asset(),
                            "b": _asset(premises=[{"id": "a", "kind": "bogus"}])})
        self.assertIn("kind 非法", msg)

    def test_premise_without_id_rejected(self):
        msg = self._reject({"a": _asset(premises=[{"kind": "verifies"}])})
        self.assertIn("缺 id", msg)


class LevelTest(unittest.TestCase):
    """`level`（要求等级）：RFC 2119 的机械形式——冲突时以谁为准。

    与 `tier` **正交**：tier 答「可不可信」，level 答「冲突时听谁的」。
    """

    def setUp(self) -> None:
        self.scan = _load_scan()

    def _reject(self, assets: dict) -> str:
        with self.assertRaises(SystemExit) as ctx:
            self.scan.validate(assets)
        return str(ctx.exception)

    def test_illegal_level_rejected(self):
        msg = self._reject({"a": _asset(level="critical")})
        self.assertIn("level 非法", msg)

    def test_omitted_level_is_legal(self):
        self.scan.validate({"a": _asset()})          # 不声明 = 合法（不编默认值）

    def test_both_levels_accepted(self):
        self.scan.validate({"a": _asset(level="normative")})
        self.scan.validate({"a": _asset(level="informative")})

    def test_normative_anchoring_on_informative_rejected(self):
        """等级序不变式：上层的正确性不得锚在下层上。"""
        msg = self._reject({
            "a": _asset(level="normative", premises=["b"]),
            "b": _asset(level="informative"),
        })
        self.assertIn("level 序违反", msg)

    def test_informative_anchoring_on_normative_passes(self):
        """反向是**正常**的：低等级锚在高等级上（本仓 6 条属此）。"""
        self.scan.validate({
            "a": _asset(level="informative", premises=["b"]),
            "b": _asset(level="normative"),
        })

    def test_undeclared_level_does_not_arbitrate(self):
        """任一方未声明 ⇒ 不判（缺声明时不得假装知道谁赢）。"""
        self.scan.validate({
            "a": _asset(level="normative", premises=["b"]),   # b 无 level
            "b": _asset(),
        })

    def test_same_level_is_not_a_violation(self):
        self.scan.validate({
            "a": _asset(level="normative", premises=["b"]),
            "b": _asset(level="normative"),
        })

    def test_outranks_lists_only_lower_declared_premises(self):
        """`overrides` 是 level 字段的**用途**（不只存着好看）。"""
        assets = {
            "top": _asset(level="normative", premises=["mid", "low", "unknown"]),
            "mid": _asset(level="normative"),
            "low": _asset(level="informative"),
            "unknown": _asset(),
        }
        rows = {r["id"]: r for r in self.scan.derive(assets)}
        self.assertEqual(rows["top"]["overrides"], ["low"])

    def test_outranks_empty_without_own_level(self):
        assets = {
            "a": _asset(premises=["b"]),                   # a 无 level
            "b": _asset(level="informative"),
        }
        rows = {r["id"]: r for r in self.scan.derive(assets)}
        self.assertEqual(rows["a"]["overrides"], [])

    def test_level_survives_into_index_row(self):
        rows = {r["id"]: r for r in self.scan.derive({"a": _asset(level="informative")})}
        self.assertEqual(rows["a"]["level"], "informative")


class AssertionGapTest(unittest.TestCase):
    """③ 越界检测：「谁违反了原则」的第二个可机检面。

    断言写进需求就有了约束力；若覆盖审计表里没有它 ⇒ 没有任何东西回答「它做到了吗」。
    这不是数据冲突（两份文档不一致），而是**单边越界**：声明了义务却没交代履行方式。
    """

    def setUp(self) -> None:
        self.scan = _load_scan()

    def _with_requirements(self, text: str):
        """把 requirements.md 指到一个临时文件上，不碰真仓。

        利用 `Path("/base") / "/abs/req.md" == Path("/abs/req.md")`——绝对路径直接顶掉根，
        所以不需要替改 `scan.ROOT`（少一个可被误遗留的全局副作用）。
        """
        tmp = pathlib.Path(tempfile.mkdtemp()) / "req.md"
        self.addCleanup(shutil.rmtree, str(tmp.parent), ignore_errors=True)
        tmp.write_text(text, encoding="utf-8")
        return {"requirements": {"path": str(tmp)}}

    def test_fully_covered_yields_nothing(self):
        assets = self._with_requirements(
            "- **FR-1**：甲\n\n## 7. 实现覆盖审计\n\n| FR-1 甲 | ✅ | 证据 |\n")
        self.assertEqual(self.scan.assertion_gaps(assets), [])

    def test_assertion_without_audit_is_reported(self):
        assets = self._with_requirements(
            "- **FR-1**：甲\n- **FR-2**：乙\n\n## 7. 实现覆盖审计\n\n| FR-1 甲 | ✅ | 证据 |\n")
        gaps = self.scan.assertion_gaps(assets)
        self.assertEqual([g["assertion"] for g in gaps], ["FR-2"])

    def test_merged_audit_row_expands(self):
        """`FR-1/2/3` 是本仓实际写法——不展开会误报。"""
        assets = self._with_requirements(
            "- **FR-1**：甲\n- **FR-2**：乙\n- **FR-3**：丙\n\n"
            "## 7. 实现覆盖审计\n\n| FR-1/2/3 甲 | ✅ | 证据 |\n")
        self.assertEqual(self.scan.assertion_gaps(assets), [])

    def test_nfr_table_form_is_recognised(self):
        """NFR 用表格式定义（`| NFR-1 | …`），与 FR 的列表式不同。"""
        assets = self._with_requirements(
            "| NFR-1 | 零依赖 |\n\n## 7. 实现覆盖审计\n\n| NFR-1 零依赖 | ✅ | 仅标准库 |\n")
        self.assertEqual(self.scan.assertion_gaps(assets), [])

    def test_audit_rows_before_the_section_are_ignored(self):
        """正文里也有 `| FR-1 |` 形的表格——不能被当成审计面。"""
        assets = self._with_requirements(
            "- **FR-1**：甲\n\n| FR-1 | 某个正文表 |\n\n## 7. 实现覆盖审计\n")
        gaps = self.scan.assertion_gaps(assets)
        self.assertEqual([g["assertion"] for g in gaps], ["FR-1"])

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(self.scan.assertion_gaps({}), [])
        self.assertEqual(self.scan.assertion_gaps({"requirements": {"path": "nope.md"}}), [])

    def test_real_registry_is_fully_covered(self):
        """现状据实断：本仓 34 条断言全部有审计（绿，但不变式被钉住）。"""
        self.assertEqual(self.scan.assertion_gaps(self.scan._load()), [])


class SpecDeliveryTest(unittest.TestCase):
    """Spec（带 `spec` 块的资产）的交付覆盖：按角色算齐不齐，缺角色 → 交付门 rc=1。"""

    def setUp(self) -> None:
        self.scan = _load_scan()

    def _reject(self, assets: dict) -> str:
        with self.assertRaises(SystemExit) as ctx:
            self.scan.validate(assets)
        return str(ctx.exception)

    def test_bad_role_rejected(self):
        msg = self._reject({"s": _asset(spec={"required_roles": ["bogus"], "delivers": []})})
        self.assertIn("required_roles", msg)

    def test_non_array_delivers_rejected(self):
        msg = self._reject({"s": _asset(spec={"required_roles": ["requirement"],
                                               "delivers": "nope"})})
        self.assertIn("delivers", msg)

    def test_dangling_deliver_rejected(self):
        msg = self._reject({"s": _asset(
            spec={"required_roles": ["requirement"], "delivers": ["nope"]})})
        self.assertIn("未登记资产", msg)

    def test_coverage_groups_by_role(self):
        cov = self.scan.spec_coverage({
            "s": _asset(spec={"required_roles": ["requirement", "verification"],
                              "delivers": ["reqdoc"]}),
            "reqdoc": _asset(category="requirement"),
        })
        self.assertEqual(cov[0]["covered"]["requirement"], ["reqdoc"])
        self.assertEqual(cov[0]["missing_roles"], ["verification"])

    def test_no_spec_block_yields_nothing(self):
        self.assertEqual(self.scan.spec_coverage({"a": _asset()}), [])

    def test_delivery_cli_rc(self):
        """交付门：角色缺 → rc=1，齐全 → rc=0（走真 CLI 入口，替身注入登记表）。"""
        scan = self.scan
        self.addCleanup(setattr, scan, "_load", scan._load)

        def reg(roles: list[str], cats: dict[str, str]) -> dict:
            out = {"s": _asset(spec={"required_roles": roles, "delivers": list(cats)})}
            out.update({d: _asset(category=c) for d, c in cats.items()})
            return out

        scan._load = lambda: reg(["requirement", "verification"], {"r": "requirement"})
        self.assertEqual(scan.main(["delivery"]), 1)     # 缺 verification
        scan._load = lambda: reg(["requirement"], {"r": "requirement"})
        self.assertEqual(scan.main(["delivery"]), 0)     # 角色齐全


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
            self.assertEqual(row.get("category", ""), decl.get("category", ""),
                             f"{aid}.category 漂移")
            self.assertEqual(row["premises"], list(decl.get("premises") or []),
                             f"{aid}.premises 漂移")


if __name__ == "__main__":
    unittest.main()
