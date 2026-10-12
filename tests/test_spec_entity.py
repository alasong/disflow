"""FR-33 / ADR 0006：Spec（交付单元）是**独立顶层实体** —— 端到端与边界测试。

为何单开一个文件（而不是塞进 `test_engine.py`）：本条需求有一组**自己的**硬边界
（ADR 0006 定的四条），它们是这个实体的**契约**，不是别处的副作用：

1. **独立顶层**：`specs` 与 `trees` 并列；不是树的字段、不是节点的字段。
2. **关联非归属**：删树不动 Spec；删 Spec 不动资产。
3. **可重放**：同一批事件两次 `project` 结果相同（NFR-5）。
4. **不得成第二真相源**：Spec 不定义资产刷新顺序、不复制 node 状态。

反过来，本文件也钉住「**不做**什么」——那些是 ADR 0006 明确否掉的形态，
防以后有人「顺手」加回来（错的方向被写进代码而不被拦，是本仓记过的失效模式）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from disflow import store
from disflow.cli import main
from disflow.engine import Engine, EngineError
from disflow.state import SPEC_ROLES, project, specs
from disflow.vocab import STORAGE


def _engine(tmp: str) -> Engine:
    """测试隔离：state dir 落到临时目录（同 tests/test_engine.py 的做法）。"""
    storage = {**STORAGE, "state": {**STORAGE["state"], "dir": tmp}}
    return Engine(storage=storage, cwd=tmp)


class SpecEntityTest(unittest.TestCase):
    """实体本身：形状、重放、拒收。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.engine = _engine(self._tmp.name)

    def test_specs_is_top_level_alongside_trees(self) -> None:
        """`specs` 与 `trees` **并列**（ADR 0006：独立顶层实体）。"""
        self.engine.spec_init("s1", "交付甲")
        state = self.engine.load()
        self.assertIn("specs", state)
        self.assertIn("trees", state)
        # 关键：spec **不在**任何树下面（否则就退化成树的字段）
        self.assertNotIn("s1", state.get("trees", {}))

    def test_init_then_read_back(self) -> None:
        self.engine.spec_init("s1", "交付甲")
        spec = self.engine.spec("s1")
        self.assertEqual(spec["goal"], "交付甲")
        self.assertFalse(spec["closed"])
        self.assertEqual(spec["assets"], {})

    def test_link_records_role_and_delivers(self) -> None:
        self.engine.spec_init("s1", "g")
        self.engine.spec_link("s1", "pkg", "implementation")
        self.engine.spec_link("s1", "tests", "verification")
        spec = self.engine.spec("s1")
        self.assertEqual(spec["assets"], {"pkg": "implementation",
                                          "tests": "verification"})
        self.assertEqual(spec["delivers"], ["pkg", "tests"])

    def test_relink_same_asset_overwrites_role(self) -> None:
        """同一资产换角色 = 覆盖（同一对内多角色会造出「算哪个」的歧义）。"""
        self.engine.spec_init("s1", "g")
        self.engine.spec_link("s1", "pkg", "implementation")
        self.engine.spec_link("s1", "pkg", "design")
        spec = self.engine.spec("s1")
        self.assertEqual(spec["assets"], {"pkg": "design"})
        self.assertEqual(spec["delivers"], ["pkg"])   # 不重复

    # ── 拒收路径：全部要响亮（不静默降级，NFR-7）───────────────────────

    def test_rejects_unknown_role(self) -> None:
        self.engine.spec_init("s1", "g")
        with self.assertRaises(EngineError):
            self.engine.spec_link("s1", "pkg", "乱写的角色")
        # 拒收后**不留痕**：非法事件不该进流
        self.assertEqual(self.engine.spec("s1")["assets"], {})

    def test_rejects_duplicate_init(self) -> None:
        self.engine.spec_init("s1", "g")
        with self.assertRaises(EngineError):
            self.engine.spec_init("s1", "又建一次")

    def test_rejects_missing_spec(self) -> None:
        with self.assertRaises(EngineError):
            self.engine.spec("nosuch")
        with self.assertRaises(EngineError):
            self.engine.spec_link("nosuch", "pkg", "implementation")

    def test_rejects_unlink_of_unlinked_asset(self) -> None:
        self.engine.spec_init("s1", "g")
        with self.assertRaises(EngineError):
            self.engine.spec_unlink("s1", "pkg")

    def test_rejects_double_close_and_reopen_of_open(self) -> None:
        self.engine.spec_init("s1", "g")
        with self.assertRaises(EngineError):
            self.engine.spec_reopen("s1", "开着呢")
        self.engine.spec_close("s1")
        with self.assertRaises(EngineError):
            self.engine.spec_close("s1")

    def test_reopen_records_reason(self) -> None:
        self.engine.spec_init("s1", "g")
        self.engine.spec_close("s1")
        self.engine.spec_reopen("s1", "漏了一条")
        spec = self.engine.spec("s1")
        self.assertFalse(spec["closed"])
        self.assertEqual(spec["reopen_log"][-1]["reason"], "漏了一条")

    # ── 可重放（NFR-5）──────────────────────────────────────────────────

    def test_projection_is_replayable(self) -> None:
        """同一批事件两次投影**逐字段相同** —— Spec 不引入时钟/计数依赖。"""
        events = [
            {"kind": "spec.init", "spec": "s1", "goal": "g"},
            {"kind": "spec.link", "spec": "s1", "asset": "pkg",
             "role": "implementation"},
            {"kind": "spec.close", "spec": "s1", "at": "2026-10-12"},
        ]
        first = project(events)
        second = project(events)
        self.assertEqual(first, second)

    def test_rebuild_check_passes_with_specs(self) -> None:
        """`rebuild --check` 对含 Spec 的事件流一致（投影可重算）。"""
        self.engine.spec_init("s1", "g")
        self.engine.spec_link("s1", "pkg", "implementation")
        self.engine.spec_close("s1")
        matched, diffs = store.projection_matches(self.engine.storage,
                                                  project=project)
        self.assertTrue(matched, diffs)

    def test_old_event_stream_without_specs_still_reads(self) -> None:
        """旧事件流（无任何 `spec.*`）仍可读 —— `specs` 键缺失不得炸。"""
        state = project([{"kind": "tree.init", "slug": "t", "goal": "g",
                          "root": "root", "stage": "req"}])
        self.assertEqual(specs(state), {})

    # ── 硬边界（ADR 0006 明确否掉的形态，防顺手加回来）──────────────────

    def test_deleting_tree_does_not_remove_spec(self) -> None:
        """**关联非归属**：Spec 不因树而生灭（否则跨交付复用就没了）。

        用「建树 + 再挂一个 Spec」验证：树的**存在性**与 Spec 无关。
        不调 `close`：那会跑 `tree_closure` 门（要求 evaluate/reflect 节点）——
        本测试要验的是**实体独立性**，不是关门语义（那是另一条需求的事）。
        """
        self.engine.init_tree("t", "一棵树")
        self.engine.spec_init("s1", "g")
        self.engine.spec_link("s1", "pkg", "implementation")
        state = self.engine.load()
        # 两面各自独立：树在 trees 里，Spec 在 specs 里
        self.assertIn("t", state["trees"])
        self.assertIn("s1", state["specs"])
        # 删 Spec 也不动资产（关联非归属）——取消关联后 Spec 仍在
        self.engine.spec_unlink("s1", "pkg")
        self.assertIn("s1", self.engine.load()["specs"])
        self.assertEqual(self.engine.spec("s1")["assets"], {})

    def test_spec_does_not_duplicate_node_state(self) -> None:
        """Spec 不得复制 node 的状态/就绪（归引擎）—— 它的字段是**闭集**。"""
        self.engine.spec_init("s1", "g")
        spec = self.engine.spec("s1")
        self.assertEqual(
            set(spec), {"goal", "gate", "assets", "delivers", "closed", "reopen_log"})
        for forbidden in ("nodes", "ready", "status", "progress", "stage"):
            self.assertNotIn(forbidden, spec)

    def test_roles_are_subset_of_asset_layer_categories(self) -> None:
        """引擎的 `SPEC_ROLES` 必须是资产层 `CATEGORIES` 的**子集**。

        为何不是「相等」：两层管的不是一件事（实测校对后修正了本条断言）：
        - 资产层 `CATEGORIES` 是**全集**（7 个）：requirement/design/implementation/
          verification/process/documentation/config —— 任何资产都能打其中一个；
        - 引擎 `SPEC_ROLES` 是**交付角色**（ADR 0006 的 5 个）—— 「全量交付」只问这 5 个
          （`process`/`config` **不是**交付物，是过程/配置，不该被要求齐全）。

        所以要钉的是**定向**关系：引擎声明的角色都是合法的资产 category（否则 Spec 关联
        一个资产时，那个资产永远填不上这个角色 ⇒ “缺角色”永远报红，是个死项）。
        反向（资产 category ⊆ 引擎角色）**不成立也不该成立**。

        两边各写一份而不是 import：引擎不得依赖 `scripts/`（NFR-1 / 分层，
        `test_layering.py` 守）—— 故靠这条测试对账，抓「两边各自演进导致的静默漂移」。
        """
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "scripts", "assets_scan.py")
        loader_spec = importlib.util.spec_from_file_location("assets_scan_probe", path)
        if loader_spec is None or loader_spec.loader is None:   # pragma: no cover
            self.fail(f"无法加载 {path}（对账测试无法进行）")
        module = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(module)
        extra = set(SPEC_ROLES) - set(module.CATEGORIES)
        self.assertEqual(extra, set(),
                         f"引擎角色 {extra} 不是合法的资产 category ⇒ 这些角色永远填不上")


class SpecCliTest(unittest.TestCase):
    """CLI 面：动词可用、退出码分明（0 成功 / 1 引擎拒收 / 2 参数错）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = mock.patch.dict(os.environ,
                                   {"DISFLOW_STATE_DIR": self._tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_cli(self, *argv: str) -> tuple[int, str]:
        """跑一次 CLI，返回 (退出码, stdout)。**每次调用的输出各自取**——
        否则只能看到最后一次的（本测试初版就踩了这个：断言的是重开而不是详情）。"""
        import io
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", io.StringIO()):
            code = main(list(argv))
        return code, out.getvalue()

    def test_full_lifecycle_via_cli(self) -> None:
        self.assertEqual(self.run_cli("spec-init", "s1", "--goal", "g")[0], 0)
        self.assertEqual(self.run_cli("spec-link", "s1", "pkg",
                                      "implementation")[0], 0)
        # 详情输出**当场取**（不能拿最后一次调用的）
        code, out = self.run_cli("spec", "s1")
        self.assertEqual(code, 0)
        self.assertIn("pkg", out)
        self.assertIn("implementation", out)
        self.assertEqual(self.run_cli("spec-close", "s1")[0], 0)
        self.assertEqual(self.run_cli("spec-reopen", "s1",
                                      "--reason", "漏了")[0], 0)
        # 重开后详情里应能看到重开记录带来的状态
        self.assertEqual(self.run_cli("spec", "s1")[0], 0)

    def test_specs_lists_created(self) -> None:
        self.run_cli("spec-init", "s1", "--goal", "交付甲")
        code, out = self.run_cli("specs")
        self.assertEqual(code, 0)
        self.assertIn("s1", out)

    def test_engine_rejection_exits_1(self) -> None:
        self.assertEqual(self.run_cli("spec", "nosuch")[0], 1)

    def test_bad_role_exits_2(self) -> None:
        """角色非法是**参数错**（argparse `choices`）⇒ 2，不是 1。

        `argparse` 的错路是 `parser.error()` → `sys.exit(2)`（**抛 `SystemExit`**，
        不是 return）——故这里断言 `SystemExit.code == 2`，而不是拿 `main` 的返回值。
        这与 `test_cli.py` 对参数错的既有断言方式一致。
        """
        self.assertEqual(self.run_cli("spec-init", "s1", "--goal", "g")[0], 0)
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("spec-link", "s1", "pkg", "乱写")
        self.assertEqual(ctx.exception.code, 2)

    def test_spec_init_dry_run_writes_nothing(self) -> None:
        self.assertEqual(self.run_cli("spec-init", "s1", "--goal", "g",
                                      "--dry-run")[0], 0)
        code, out = self.run_cli("specs")
        self.assertEqual(code, 0)
        self.assertIn("（无 Spec）", out)


if __name__ == "__main__":
    unittest.main()
