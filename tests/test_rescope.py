"""写面声明的**当口改动**（`rescope`）与 `design_first` 破例（`scope_waiver`）。

为什么这两件事必须成对存在（且不能只有后者）：
`design_first` 的全部力量来自「**计划先于改动**」。如果只提供「随便改计划」的口子，
那它等于没有门——把计划改成“我刚改过的东西”即可过门。所以：

- `rescope`：发现要碰 X、**还没碰** X 时把范围声明对（计划仍然先于改动）；
- `scope_waiver`：**已经**碰了 X 才想补声明 → 显式破例，且**违规记录进证据**。

判据靠内容指纹（`introduced` 同一原语）：已碰过的路径不许再声明进来。
"""
from __future__ import annotations

import os
import subprocess
import unittest

from base import DisflowTestCase, acceptance, git_repo, touch

from disflow import vocab, store
from disflow.engine import EngineError, GateReject
from disflow.state import project


class RescopeTest(DisflowTestCase):
    def _running(self, files: dict[str, str] | None = None,
                 commit: bool = False) -> tuple:
        """建树 + 一个 running 的叶子节点 `n`（写面声明 `src_a.py`）。"""
        engine, repo = git_repo(self, files if files is not None
                                else {"src_a.py": "x = 1\n", "other.py": "o = 1\n"},
                                commit=commit)
        engine.init_tree("t", "g")
        engine.expand("t", "root", [{"id": "n", "goal": "改 a",
                                     "touch_plan": ["src_a.py"],
                                     "acceptance": acceptance()}])
        engine.start("t", "n", "w")
        return engine, repo

    @staticmethod
    def _node(engine) -> dict:
        return engine.load()["trees"]["t"]["nodes"]["n"]

    # ── rescope 的正当用法 ───────────────────────────────────────────────

    def test_rescope_updates_plan_and_records_reason(self):
        engine, _repo = self._running()
        engine.rescope("t", "n", ["src_a.py", "brand_new.py"], "需要新建 brand_new.py")
        node = self._node(engine)
        self.assertEqual(node["touch_plan"], ["src_a.py", "brand_new.py"])
        log = node["rescope_log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["from"], ["src_a.py"])
        self.assertEqual(log[0]["to"], ["src_a.py", "brand_new.py"])
        self.assertIn("brand_new.py", log[0]["reason"])

    def test_rescope_then_change_in_new_scope_passes_the_gate(self):
        """先声明后动手：新增范围内的改动不该被门拒。"""
        engine, repo = self._running()
        engine.rescope("t", "n", ["src_a.py", "brand_new.py"], "先声明")
        touch(os.path.join(repo, "brand_new.py"), "n = 1\n")
        engine.done("t", "n")
        evidence = self._node(engine)["evidence"]
        self.assertEqual(evidence["introduced"], ["brand_new.py"])
        self.assertEqual(evidence["scope"]["violations"], [])

    def test_rescope_may_adopt_a_clean_tracked_file(self):
        """干净已跟踪文件（不在基线里）也算“没碰过”，可以声明进来。"""
        engine, repo = self._running(commit=True)
        engine.rescope("t", "n", ["src_a.py", "other.py"], "要顺带改 other.py")
        touch(os.path.join(repo, "other.py"), "o = 2\n")
        engine.done("t", "n")
        self.assertEqual(self._node(engine)["evidence"]["scope"]["violations"], [])

    def test_rescope_may_adopt_preexisting_dirt_it_did_not_touch(self):
        """开工前就脏、且本节点**没碰**的文件：不算 introduced ⇒ 可以声明进来（不能误拒）。"""
        engine, repo = self._running(files={"src_a.py": "x = 1\n", "legacy.py": "l = 1\n"})
        self.assertIn("legacy.py", self._node(engine)["baseline"])
        engine.rescope("t", "n", ["src_a.py", "legacy.py"], "旧的脏文件其实归我")
        self.assertEqual(self._node(engine)["touch_plan"], ["src_a.py", "legacy.py"])

    def test_rescope_dry_run_writes_nothing(self):
        engine, _repo = self._running()
        before = len(store.load_events(vocab.STORAGE))
        engine.rescope("t", "n", ["src_a.py", "new.py"], "预演", dry_run=True)
        self.assertEqual(len(store.load_events(vocab.STORAGE)), before)
        self.assertEqual(self._node(engine)["touch_plan"], ["src_a.py"])

    # ── rescope 的拒绝路径（门的力量所在）─────────────────────────────────

    def test_rescope_rejects_a_path_this_node_already_touched(self):
        """核心判据：已经改过的路径不许事后声明进来 —— 否则“计划先于改动”作废。"""
        engine, repo = self._running()
        touch(os.path.join(repo, "other.py"), "o = 2\n")          # 越界改了
        with self.assertRaises(EngineError) as ctx:
            engine.rescope("t", "n", ["src_a.py", "other.py"], "事后补声明")
        self.assertIn("已经改过", str(ctx.exception))
        self.assertIn("scope_waiver", str(ctx.exception))          # 报错要点名替代路径
        self.assertEqual(self._node(engine)["touch_plan"], ["src_a.py"])

    def test_rescope_rejects_a_path_this_node_committed(self):
        """提交型 worker（`git status` 为空）也不能把越界改动说成“没碰过”。

        判据必须复用 `start` 记的 `head_sha`：只看未提交改动时，越界改 → `git commit` →
        rescope 即可把「刚改过的东西」补进计划（就地派发一样能绕过，不只是 worktree）。
        """
        engine, repo = self._running(commit=True)
        touch(os.path.join(repo, "other.py"), "o = 2\n")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-qm", "out-of-scope"], check=True, capture_output=True)
        with self.assertRaises(EngineError) as ctx:
            engine.rescope("t", "n", ["src_a.py", "other.py"], "事后补声明")
        self.assertIn("已经改过", str(ctx.exception))
        self.assertEqual(self._node(engine)["touch_plan"], ["src_a.py"])

    def test_rescope_requires_a_reason(self):
        engine, _repo = self._running()
        with self.assertRaises(EngineError):
            engine.rescope("t", "n", ["src_a.py", "new.py"], "   ")

    def test_rescope_requires_a_running_node(self):
        engine, _repo = self._running()
        engine.expand("t", "root", [{"id": "m", "goal": "g", "touch_plan": ["m.py"],
                                     "acceptance": acceptance()}])
        with self.assertRaises(EngineError) as ctx:
            engine.rescope("t", "m", ["m.py", "q.py"], "还没开工")
        self.assertIn("running", str(ctx.exception))

    def test_rescope_rejects_a_no_op_declaration(self):
        engine, _repo = self._running()
        with self.assertRaises(EngineError):
            engine.rescope("t", "n", ["src_a.py"], "什么都没加")

    def test_rescope_rejects_malformed_touch(self):
        engine, _repo = self._running()
        for bad in ([], "src_a.py", [""], ["ok", 7]):
            with self.assertRaises(EngineError):
                engine.rescope("t", "n", bad, "格式不对")   # type: ignore[arg-type]

    # ── scope_waiver：显式破例，且留痕 ───────────────────────────────────

    def test_out_of_scope_change_is_still_rejected_without_a_waiver(self):
        """破例口不能把门变松：没有 waiver 时越界照拒。"""
        engine, repo = self._running()
        touch(os.path.join(repo, "other.py"), "o = 2\n")
        with self.assertRaises(GateReject) as ctx:
            engine.done("t", "n")
        self.assertTrue(any("touch_plan" in m for m in ctx.exception.missing))

    def test_scope_waiver_allows_and_lands_in_evidence(self):
        engine, repo = self._running()
        touch(os.path.join(repo, "other.py"), "o = 2\n")
        engine.done("t", "n", {"scope_waiver": "other.py 是配套改动"})
        node = self._node(engine)
        self.assertEqual(node["status"], "done")
        evidence = node["evidence"]
        # 违规**照记**（不掩盖），只是被显式破例放行
        self.assertEqual(evidence["scope"]["violations"], ["other.py"])
        self.assertEqual(evidence["scope_waiver"], "other.py 是配套改动")

    def test_size_waiver_also_lands_in_evidence(self):
        """与 scope_waiver 对称：破例必须留痕（此前只用于放行、不入证据）。"""
        engine, repo = self._running()
        engine.annotate("t", "n", {"size_budget": 0})
        touch(os.path.join(repo, "src_a.py"), "x = 2\n")
        engine.done("t", "n", {"size_waiver": "小改动但预算设成了 0"})
        self.assertEqual(self._node(engine)["evidence"]["size_waiver"],
                         "小改动但预算设成了 0")

    def test_rescope_survives_replay(self):
        """新事件必须能被重放（投影一致性是硬约束）。"""
        engine, _repo = self._running()
        engine.rescope("t", "n", ["src_a.py", "new.py"], "加范围")
        matched, diffs = store.projection_matches(vocab.STORAGE, project=project)
        self.assertTrue(matched, diffs)


if __name__ == "__main__":
    unittest.main()
