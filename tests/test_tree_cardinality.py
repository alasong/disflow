"""树级判据的机检化：建树的「是否本该同树」提示 + `doctor` 的跨树等待检查。

**为什么要有它**：disflow 的模型里「看板 : 树 = 1:1」「树 = 任务依赖视图」两条机制成立，
但**建树无门槛、树之间无判据** —— 拆分全凭宿主裁量，于是同一目标的两个阶段被建成两棵
并列的树（实测反例 `mldev` / `mlxfix`：前者是「产出首个作品」，后者是「把作品做进上游」，
本是同树内的 `repro → fix`）。

**判据不是词面重合**（这是本节点返工改正的地方）：拿 `gates` 的切词去比 `mldev` /
`mlxfix` 两句 goal，**只共享 1 个通用动词「产出」** —— 词面判据在它为之而写的那个 bug 上
必然失效。故：

- **建树提示**的主判据是**结构信号**：存在已 `closed` 的树 ⇒ 提醒「这可能是它的下一阶段」
  并给出**可照抄**的 `reopen` + `expand` 命令（本仓已实测「判据写成提示零行为改变」，
  只说「注意」等于没说；命令必须能被直接复制）。
- **`doctor`** 报出**已声明 `awaits`** 的树：`awaits` 指向另一棵树 = 承认两者有依赖 ⇒
  那本该是同树内的 `requires`，不是「两棵树 + 跨树等待」。这一条**才**抓得住 `mldev`/
  `mlxfix`（`mlxfix` 的节点写了 `awaits: ['mldev']`）。

两侧都钉：提示/自检都必须**只提示不拒收**（词面/结构信号都有假阳性，拒收会挡住合法建树），
且**给出可照抄的出路**。
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from base import DisflowTestCase, acceptance

from disflow.engine import Engine
from disflow.cli import main


class TreeCardinalityTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def _close(self, tree: str) -> None:
        self.engine.annotate(tree, "root", {"closure": "not_applicable: 测试用"})
        self.engine.close(tree)

    # ── 建树提示：结构信号（存在 closed 的树）+ 可照抄命令 ──────────────

    def test_init_after_closed_tree_warns_and_gives_copyable_reopen_cmd(self):
        """已有 closed 的树 ⇒ 建新树时给提示 + 一条**可照抄**的 reopen+expand 命令。"""
        code, _out, _err = self._run(["init", "mldev", "--goal", "收敛出方向并产出首个作品"])
        self.assertEqual(code, 0)
        self._close("mldev")

        code, out, err = self._run(["init", "mlxfix", "--goal", "为 1573 产出最小修复"])
        self.assertEqual(code, 0, "提示不改行为：建树仍成功（拒收会挡住合法建树）")
        self.assertIn("✓ 建树 mlxfix", out, "成功报文仍在 stdout")
        self.assertIn("mldev", err, "提示要报出是哪棵已关闭的树")
        self.assertIn("已关闭", err)
        self.assertIn("reopen mldev", err, "必须给出可照抄的 reopen 命令，不是只说「注意」")
        self.assertIn("expand mldev root --children", err, "同树继续还要给 expand 的用法")
        self.assertIn("本提示不影响结果", err, "要说清这是提示、不是拒收")

    def test_init_without_closed_tree_does_not_warn(self):
        """没有 closed 的树 ⇒ 不误报（假红比不报更坏）。"""
        first = self._run(["init", "mldev", "--goal", "收敛出方向并产出首个作品"])
        self.assertEqual(first[2], "", "第一棵树不该有任何提醒")
        # 再建一棵未关闭的树（共享内容词也不该触发 —— 词面不是主判据）
        code, _out, err = self._run(["init", "other", "--goal", "收敛出别的方向并产出作品"])
        self.assertEqual(code, 0)
        self.assertNotIn("下一阶段", err)

    def test_word_overlap_is_only_additional_info_not_the_trigger(self):
        """词面共同词只作附加信息打印 —— 它不是触发条件（权威反例只共享 1 个通用词）。"""
        self._run(["init", "mldev", "--goal", "收敛出方向并产出首个作品"])
        self._close("mldev")
        _code, _out, err = self._run(["init", "mlxfix", "--goal", "为 1573 产出最小修复"])
        # 「产出」是权威反例里唯一的共同词，且它被明确标注为「非判据」
        self.assertIn("非判据", err)
        self.assertIn("不作主判据", err)

    # ── doctor：已声明 awaits 的树对（这才能抓住 mldev / mlxfix） ────────

    def test_doctor_reports_awaits_pairs_as_advisory_not_failure(self):
        """`mlxfix` 声明 `awaits: ['mldev']` ⇒ doctor 报出，但**不判红**（exit 0）。"""
        self._run(["init", "mldev", "--goal", "收敛出方向并产出首个作品"])
        self._close("mldev")
        self._run(["init", "mlxfix", "--goal", "为 1573 产出最小修复"])
        self.engine.expand("mlxfix", "root",
                           [{"id": "e1", "awaits": ["mldev"], "goal": "修 1573",
                             "acceptance": acceptance()}])

        code, out, err = self._run(["doctor"])
        self.assertEqual(code, 0, "awaits 是合法机制，不能断言它一定错 ⇒ 不判红")
        self.assertEqual(err, "", "非故障不该走 stderr")
        self.assertIn("需人判断", out)
        self.assertIn("跨树等待", out, "要报出「声明了跨树等待」这件事")
        self.assertIn("mlxfix 等待 mldev", out, "要点名是哪两棵树")
        self.assertIn("requires", out, "要给出「本该同树内用 requires」这条出路")
        self.assertIn("✓ doctor 全绿", out)

    def test_doctor_without_awaits_stays_fully_green(self):
        """两棵无 `awaits` 关联的树 ⇒ doctor 全绿、无该条（负向对照的另一半）。"""
        self._run(["init", "a", "--goal", "做甲"])
        self._run(["init", "b", "--goal", "做乙"])
        code, out, _err = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertNotIn("跨树等待", out)
        self.assertIn("✓ doctor 全绿", out)

    def test_awaits_to_unknown_tree_is_not_reported(self):
        """`awaits` 指向不存在的树 ⇒ 不报（避免把悬空引用误报成树对）。"""
        self._run(["init", "b", "--goal", "做乙"])
        self.engine.expand("b", "root",
                           [{"id": "b1", "awaits": ["ghost"], "goal": "g",
                             "acceptance": acceptance()}])
        code, out, _err = self._run(["doctor"])
        self.assertEqual(code, 0)
        self.assertNotIn("跨树等待", out)


if __name__ == "__main__":
    unittest.main()
