"""新判据（融合自 kaa）：敷衍理由拒收 + 声称/命令对账信号。

两组判据都是**补本仓原有缺口**：
- `weak_reason`：破例通道（`no_result` / `scope_waiver` / `size_waiver`）原先接受任意非空
  字符串 ⇒ 写「无」就能把门关掉（与「不静默降级」相冲）。
- `claim_mismatch`：worker 声称「测试通过」而验收命令是 `true` —— 两条各自合法，门看不出
  它们不是一回事。
"""
from __future__ import annotations

import unittest

from disflow import gates
from disflow.config import DEFAULTS


class WeakReasonTest(unittest.TestCase):
    def test_blocklist_hits_are_rejected(self):
        for word in ("无", "不需要", "无需", "不适用", "没有", "n/a", "略", "同上"):
            with self.subTest(word=word):
                self.assertIsNotNone(gates.weak_reason(DEFAULTS, word))

    def test_symbol_only_and_tiny_reasons_are_rejected(self):
        for word in ("。", "?", "x"):
            with self.subTest(word=word):
                self.assertIsNotNone(gates.weak_reason(DEFAULTS, word))

    def test_specific_reason_passes(self):
        reason = "第三方库 patch 了 sys.path，本轮先绕开，下轮修"
        self.assertIsNone(gates.weak_reason(DEFAULTS, reason))

    def test_empty_reason_is_not_weak(self):
        """空理由由各破例通道自己判 —— 本判据不越权（空 != 敷衍）。"""
        self.assertIsNone(gates.weak_reason(DEFAULTS, ""))
        self.assertIsNone(gates.weak_reason(DEFAULTS, "   "))

    def test_empty_blocklist_disables_the_check(self):
        """空词表 = 关掉该判据（可配置，不是写死）。"""
        cfg = type(DEFAULTS)(weak_reasons=())
        self.assertIsNone(gates.weak_reason(cfg, "无"))


class ClaimMismatchTest(unittest.TestCase):
    def test_claim_without_test_command_is_flagged(self):
        msg = gates.claim_mismatch(DEFAULTS, "测试通过", "true")
        self.assertIsNotNone(msg)
        self.assertIn("true", str(msg))

    def test_claim_with_real_test_command_is_clean(self):
        self.assertIsNone(gates.claim_mismatch(
            DEFAULTS, "测试通过", "python3 -m unittest discover -s tests -t tests"))

    def test_no_claim_no_signal(self):
        """只说「实现完成」不算测试声称 —— 不凭空报警。"""
        self.assertIsNone(gates.claim_mismatch(DEFAULTS, "实现完成", "true"))

    def test_claim_of_pass_word_alone_is_not_enough(self):
        """只命中「通过」而没有测试词 —— 不是测试声称。"""
        self.assertIsNone(gates.claim_mismatch(DEFAULTS, "已通过", "true"))

    def test_case_insensitive(self):
        self.assertIsNotNone(gates.claim_mismatch(DEFAULTS, "All TESTS PASS", "echo hi"))
        self.assertIsNone(gates.claim_mismatch(DEFAULTS, "All TESTS PASS", "pytest -q"))

    def test_empty_summary_is_clean(self):
        self.assertIsNone(gates.claim_mismatch(DEFAULTS, "", "true"))


class GoalAlignTest(unittest.TestCase):
    """漂移门 1：产出声明与目标无共同内容词 ⇒ 拒收（跑题检测）。"""

    def test_off_topic_is_flagged(self):
        msg = gates.goal_align(DEFAULTS, "实现看板 web 入口", "重构了日志模块")
        self.assertIsNotNone(msg)
        self.assertIn("跑题", str(msg))

    def test_on_topic_passes(self):
        self.assertIsNone(gates.goal_align(
            DEFAULTS, "实现看板 web 入口", "实现看板 web 入口的只读端点"))

    def test_english_words_align(self):
        self.assertIsNone(gates.goal_align(
            DEFAULTS, "implement board web entry", "implement the board web endpoint"))

    def test_empty_side_does_not_overreach(self):
        """缺一边就无从比 —— 不越权（与 weak_reason 的空理由同款）。"""
        self.assertIsNone(gates.goal_align(DEFAULTS, "", "重构了日志"))
        self.assertIsNone(gates.goal_align(DEFAULTS, "实现 web", ""))

    def test_stopwords_alone_do_not_count(self):
        """只共享停用词（的/了/一个）不算对齐 —— 否则门形同虚设。"""
        self.assertIsNotNone(gates.goal_align(DEFAULTS, "做一个需求分析", "做一个日志清理"))


class CodeQualityTest(unittest.TestCase):
    """漂移门 2：改动集里的 .py 底线（语法/宽捕获/可变默认参/shell=True），纯 AST 不执行。"""

    def setUp(self) -> None:
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="disflow-cq-")

    def _write(self, name: str, body: str) -> None:
        import os
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as handle:
            handle.write(body)

    def test_clean_file_passes(self):
        self._write("ok.py", "def f(a: int = 0) -> int:\n    return a + 1\n")
        result = gates.code_quality(self.dir, ["ok.py"], DEFAULTS)
        self.assertTrue(result["checked"])
        self.assertEqual(result["issues"], {})

    def test_detects_all_four_kinds(self):
        self._write("bad.py", (
            "import subprocess\n"
            "def f(items=[]):\n"              # 可变默认参
            "    try:\n"
            "        return subprocess.run('ls', shell=True)\n"   # shell=True
            "    except Exception:\n"           # 宽捕获
            "        pass\n"
        ))
        issues = gates.code_quality(self.dir, ["bad.py"], DEFAULTS)["issues"]["bad.py"]
        text = "\n".join(issues)
        self.assertIn("可变默认参数", text)
        self.assertIn("shell=True", text)
        self.assertIn("except Exception", text)

    def test_each_issue_reported_once(self):
        """回归：初版把函数级检查嵌在 `ast.walk(tree)` 循环里 ⇒ 同一问题重复报 24 次。"""
        self._write("dup.py", "def f(items=[]):\n    return items\n")
        issues = gates.code_quality(self.dir, ["dup.py"], DEFAULTS)["issues"]["dup.py"]
        self.assertEqual(len(issues), 1, f"应只报一次（实际 {issues}）")

    def test_syntax_error_is_caught(self):
        self._write("broken.py", "def f(:\n")
        self.assertIn("语法错", "".join(
            gates.code_quality(self.dir, ["broken.py"], DEFAULTS)["issues"]["broken.py"]))

    def test_wide_except_with_reraise_is_allowed(self):
        """`except Exception: raise` 是合理写法（转换后重抛）—— 不得误拒。"""
        self._write("reraise.py", (
            "def f():\n    try:\n        pass\n"
            "    except Exception as exc:\n        raise ValueError('x') from exc\n"
        ))
        self.assertEqual(gates.code_quality(self.dir, ["reraise.py"], DEFAULTS)["issues"], {})

    def test_missing_file_is_not_checked(self):
        """回归：文件不存在时应 `checked=False`，不假装验过。"""
        self.assertFalse(gates.code_quality(self.dir, ["no_such.py"], DEFAULTS)["checked"])

    def test_empty_suffixes_disables_the_gate(self):
        cfg = type(DEFAULTS)(code_quality_suffixes=())
        self._write("bad.py", "def f(items=[]):\n    return items\n")
        self.assertFalse(gates.code_quality(self.dir, ["bad.py"], cfg)["checked"])


class VerifyRealTest(unittest.TestCase):
    """漂移门 3：假证据硬门（与 `claim_mismatch` 共用词表，信号→硬门）。"""

    def test_fake_evidence_is_rejected(self):
        msg = gates.verify_real(DEFAULTS, "测试通过", "true")
        self.assertIsNotNone(msg)
        self.assertIn("claim_waiver", str(msg))

    def test_real_test_command_passes(self):
        self.assertIsNone(gates.verify_real(
            DEFAULTS, "测试通过", "python3 -m unittest discover -s tests"))

    def test_no_claim_passes(self):
        self.assertIsNone(gates.verify_real(DEFAULTS, "实现完成", "true"))


if __name__ == "__main__":
    unittest.main()
