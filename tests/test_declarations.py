"""声明与实现的一致性：**字段闭集 / 事件闭集 / 平台依赖**。

**为什么要有这个文件**：本轮审查（python-expert 12 类）查出三个“声明是一套、实现是另一套”：
1. `NODE_FIELDS` 含 `status`/`lease`/`evidence`/`parent`，但 `new_node` 根本不收它们
   ⇒ 声明通过校验后被**静默丢弃**。这与上一轮修的 `requires` 是**同一个 bug 家族**。
2. `EVENT_KINDS`（vocab）与 `_HANDLERS`（state）两套清单可漂移，而前者**只管报错文本**。
3. `store.py` 在模块顶层 `import fcntl` ⇒ 非 POSIX 平台连 `import disflow` 都失败。

所以这里不测“某个值对不对”，而测**声明与实现互为镜像**——
这样这类 bug 不是“修了一个”，而是**结构上不可能再出现**。
"""
from __future__ import annotations

import ast
import contextlib
import inspect
import pathlib
import unittest

from base import DisflowTestCase, acceptance

from disflow import vocab
from disflow.engine import Engine
from disflow.model import DECLARABLE_FIELDS, NODE_FIELDS, RUNTIME_FIELDS, new_node
from disflow.state import _HANDLERS

ROOT = pathlib.Path(__file__).resolve().parent.parent


class NodeFieldContractTest(DisflowTestCase):
    def test_declarable_fields_are_exactly_what_new_node_accepts(self):
        """**结构性守卫**：声明可给的字段 == `new_node` 真正接下的参数。

        多一个 ⇒ 声明会被静默丢弃（`requires` 曾经就是这样，后果是调度乱序）；
        少一个 ⇒ 用户写不出来。两者都不行，所以必须**逐个对齐**，而不是靠人记得同步。

        两处名字不一对一是**故意的**，必须在这里写明否则就是隐性假设：
          - 用户写 `id` ⇒ 传成 `node_id`（位置参数）
          - `parent` 是**内部接线**（由 expand 的父子关系给），用户写 `parent` 会被拒
        """
        params = set(inspect.signature(new_node).parameters)
        # 用户可声明的字段 ↔ 函数参数（id↔node_id；parent 不在两边，它是接线）
        expected = (params - {"node_id", "parent"}) | {"id"}
        self.assertEqual(
            set(DECLARABLE_FIELDS), expected,
            "DECLARABLE_FIELDS 与 new_node 的参数不一致："
            f"只声明不接收={sorted(set(DECLARABLE_FIELDS) - expected)}；"
            f"只接收不声明={sorted(expected - set(DECLARABLE_FIELDS))}")
        self.assertIn("parent", inspect.signature(new_node).parameters,
                     "new_node 仍需接 parent（内部接线用），只是用户不能声明它")

    def test_declarable_and_runtime_partition_the_node_fields(self):
        """两类必须**无缝且不重叠**地覆盖节点字段闭集（漏一个就等于没有归属）。"""
        self.assertEqual(set(DECLARABLE_FIELDS) | set(RUNTIME_FIELDS), set(NODE_FIELDS))
        self.assertEqual(set(DECLARABLE_FIELDS) & set(RUNTIME_FIELDS), set())
        self.assertTrue(RUNTIME_FIELDS, "运行时字段集不该为空（parent/status/lease/evidence）")

    def test_runtime_fields_are_rejected_not_silently_ignored(self):
        """声明运行时字段必须**报错**。

        旧行为是“接受后静默丢弃”——实测 `{"status":"done"}` 进去仍然是 `pending`。
        静默丢弃比报错危险：写的人以为生效了。
        """
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        for field, value in (("status", "done"), ("lease", {"holder": "h"}),
                             ("evidence", {"fake": 1}), ("parent", "别的节点")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as ctx:
                    engine.expand("t", "root", [
                        {"id": "x", "goal": "g", "acceptance": acceptance(),
                         field: value}])
                self.assertIn(field, str(ctx.exception))
                self.assertIn("运行时事件决定", str(ctx.exception))
        # 拒收必须不落事件（不是“落了一半”）
        self.assertEqual(engine.load()["trees"]["t"]["nodes"].keys(), {"root"})

    def test_unknown_field_message_lists_only_declarable_fields(self):
        """报错里列出的“可声明字段”不能把运行时字段也算进去（否则误导用户去写它）。"""
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")
        with self.assertRaises(ValueError) as ctx:
            engine.expand("t", "root", [{"id": "x", "goal": "g", "没这个字段": 1}])
        message = str(ctx.exception)
        for runtime in RUNTIME_FIELDS:
            self.assertNotIn(f"'{runtime}'", message,
                             f"运行时字段 {runtime} 不该出现在“可声明”列表里")


class EventKindContractTest(DisflowTestCase):
    def test_event_kinds_match_dispatchable_handlers(self):
        """`vocab.EVENT_KINDS`（声明）必须与 `state._HANDLERS`（可派发）**逐个相等**。

        两者漂移的后果是隐蔽的：`EVENT_KINDS` 曾经只用于报错文本 ⇒ 报错会列出
        根本没有 handler 的 kind，而少列一个真正可派发的 kind 没人发现。
        """
        self.assertEqual(sorted(vocab.EVENT_KINDS), sorted(_HANDLERS),
                         "事件闭集与派发表不一致（加/删 kind 时两边都要动）")

    def test_unknown_kind_error_lists_dispatchable_kinds(self):
        """报错文本要用**真正可派发**的集合，不能引用可漂移的声明。"""
        from disflow import state

        with self.assertRaises(ValueError) as ctx:
            state.apply_event(state.empty(), {"kind": "没这个事件", "slug": "t"})
        message = str(ctx.exception)
        self.assertIn("可派发", message)
        self.assertIn("tree.init", message)
        self.assertNotIn("闭集：", message)


class PlatformDependencyTest(DisflowTestCase):
    def test_store_does_not_import_fcntl_at_module_level(self):
        """`fcntl` 不能出现在模块顶层。

        顶层导入 ⇒ Windows 上 `import disflow` 直接失败，**只读动词本来是可用的**。
        惰性导入之后：能 import、能读，只在真要写时才报一句能看懂的错。

        这里用 AST 而不是“跑一下试试”：本机是 POSIX，跑不出那个失败路径。
        """
        tree = ast.parse((ROOT / "disflow" / "store.py").read_text(encoding="utf-8"))
        top_level = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                top_level.add(node.module or "")
        self.assertNotIn("fcntl", top_level, "fcntl 必须惰性导入（见 file_lock 的注释）")
        self.assertNotIn("msvcrt", top_level)

    def test_read_only_verbs_never_take_the_lock(self):
        """只读路径不取锁 ⇒ 加锁不可用的平台上它们仍可用（这是惰性导入的意义）。

        做法：把 `file_lock` 打桩成**必抛**，再看读操作是否照常、写操作是否就停。
        这比“本机跑一下”强：本机是 POSIX，跑不出那个失败路径。
        """
        engine = Engine(cwd=self.tmp)
        engine.init_tree("t", "g")                       # 建树需要锁（先干完）

        from disflow import store

        @contextlib.contextmanager
        def unusable_lock(_spec):
            raise OSError("本平台无 flock")
            yield                                          # pragma: no cover

        original = store.lock
        store.lock = unusable_lock
        try:
            self.assertEqual(engine.trees(), ["t"])        # 读：不需要锁
            self.assertEqual(engine.load()["trees"]["t"]["goal"], "g")
            self.assertIsInstance(engine.drive("t"), dict)
            with self.assertRaises(OSError):
                engine.set_goal("t", "新的")               # 写：必须被挡住，不得静默不加锁
        finally:
            store.lock = original


if __name__ == "__main__":
    unittest.main()
