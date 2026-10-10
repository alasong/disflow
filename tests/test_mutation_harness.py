"""变异 harness 自身的行为：**「针脚未应用」必须与「漏抓」分开报**。

**为什么这条值得单独测**：harness 的输出是**结论**（“这个行为没有被测试守住”），
而结论可能是编造的——实测踩过：改了 `fingerprint`/`_numstat`/`_is_tracked` 的签名后，
3 条针脚的 `old` 文本匹配不上了，harness 却把它们报成「漏抓」，
于是凭空多出 3 个根本不存在的“覆盖缺口”。

正确的行为：针脚找不到 = **harness 自己的错**（exit 2，明说“未应用，本轮结果不可信”）；
漏抓 = **真覆盖缺口**（exit 1）。两者混在一起，就会把“过期针脚”说成“测试没守住”。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import unittest
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_harness() -> Any:
    """按路径加载 `scripts/mutation_check.py`。

    为何不用 `sys.path.insert` + `import mutation_check`：那会让 import 处在可执行
    语句之后（“import not at top of file”），也会让类型检查器静态解析不到。
    按路径加载把“它只是个脚本”这件事说明白，两个副作用都没有。

    返回 `Any` 是**如实的**：模块是动态加载的，类型检查器本就无从知道它的形状；
    这里不是在压告警，而是陈述一个真实事实。
    """
    path = ROOT / "scripts" / "mutation_check.py"
    spec = importlib.util.spec_from_file_location("mutation_check", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mc: Any = _load_harness()


class PinNotFoundTest(unittest.TestCase):
    def test_unapplied_pin_raises_a_distinct_error(self):
        bogus = ("故意过期的针脚", "disflow/gates.py", "这段代码根本不存在", "x", "test_gates.py")
        with self.assertRaises(mc.PinNotFound):
            mc._check(bogus)

    def test_unapplied_pin_exits_two_and_never_claims_a_coverage_hole(self):
        original = mc.MUTATIONS
        mc.MUTATIONS = [("故意过期的针脚", "disflow/gates.py",
                         "这段代码根本不存在", "x", "test_gates.py")]
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = mc.main([])
        finally:
            mc.MUTATIONS = original
        text = buffer.getvalue()
        self.assertEqual(code, 2, "未应用必须是 2（harness 的错），不是 1（漏抓）")
        self.assertIn("未应用", text)
        self.assertIn("不可信", text)
        self.assertNotIn("没有被测试守住", text,
                         "没注入就说“覆盖缺口”是编造——这句不该出现")

    def test_every_pin_applies_to_current_code(self):
        """全量检查：所有针脚的 `old` 都必须能在当前代码里找到。

        这就是上面那个坑的**预防**：改了实现签名而不更新针脚 ⇒ 立刻报错，
        而不是等到跑变异时被报成“漏抓”。
        """
        broken = []
        for name, relpath, old, _new, _pattern in mc.MUTATIONS:
            text = (ROOT / relpath).read_text(encoding="utf-8")
            if old not in text:
                broken.append(f"{name} ({relpath})")
        self.assertEqual(broken, [], f"这些针脚已与代码脱节：{broken}")


class HarnessReportingTest(unittest.TestCase):
    """harness 的两条失败通道各自钉一个用例：**漏抓**与**针脚未应用**。

    为何单独测：harness 的输出是**结论**（exit 1「这个行为没被守住」/ exit 2「本轮不可信」），
    而结论本身可能错——把一条「改了实现、测试照样绿」的针脚报成抓到（真空的绿被当成证据），
    或把「针脚对不上」静默跳过，都会让报告失真。两条通道必须都可复现地报出来。
    """

    # **故意没牙齿**的针脚：把未知 slug 的报错文案改掉（真改了输出），但 `test_metrics.py`
    # 只断言 reason 含 slug / 出现过的 slug、且不等于 `NO_START_REASON`——文案格式没被守住，
    # 于是注入生效、对应测试仍全绿。这就是「测试通过是真空的」的活样本。
    _TOOTHLESS = ("故意无牙的针脚", "disflow/metrics.py",
                  '''        return (f"未知 slug：{slug!r}；该事件流里出现过的 slug："
                f"{', '.join(sorted(seen))}")''',
                  '''        return (f"zzz slug={slug!r} seen={', '.join(sorted(seen))}")''',
                  "test_metrics.py")

    def test_a_toothless_pin_is_reported_as_a_miss_not_as_caught(self):
        """针脚改了实现、但对应测试照样绿 ⇒ 必须报「漏抓」（exit 1），不得算抓到。"""
        original = mc.MUTATIONS
        mc.MUTATIONS = [self._TOOTHLESS]
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = mc.main([])
        finally:
            mc.MUTATIONS = original
        text = buffer.getvalue()
        self.assertEqual(code, 1, "注入生效但测试没红 ⇒ 漏抓（1），不是 harness 错（2）")
        self.assertIn("漏抓", text)
        self.assertIn("没有被测试守住", text)

    def test_a_mismatched_pin_is_reported_never_silently_skipped(self):
        """针脚 `old` 在文件里找不到：必须点名报出（exit 2），不得静默跳过当没事。

        与 `PinNotFoundTest` 的区别：这里把对不上的针脚**混在一条有效针脚中间**，
        验证 harness 既不跳过坏的、也不把好的误报成漏抓（`_check` 抛错前的输出不污染结论）。
        """
        good = ("有效针脚", "disflow/metrics.py",
                "    return ts <= cutoff_ts and _day(ts) <= cutoff_day",
                "    return ts > cutoff_ts and _day(ts) <= cutoff_day",
                "test_metrics.py")
        bogus = ("对不上的针脚", "disflow/metrics.py",
                 "这段文本在 metrics.py 里根本不存在", "x", "test_metrics.py")
        original = mc.MUTATIONS
        mc.MUTATIONS = [good, bogus]
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = mc.main([])
        finally:
            mc.MUTATIONS = original
        text = buffer.getvalue()
        self.assertEqual(code, 2, "针脚对不上 = harness 的错（2），且本轮结果不可信")
        self.assertIn("对不上的针脚", text)
        self.assertIn("未应用", text)
        self.assertNotIn("没有被测试守住", text, "坏针脚不得被当成覆盖缺口")


if __name__ == "__main__":
    unittest.main()
