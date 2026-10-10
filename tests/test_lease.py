"""租约（FR-26）测试：带有效期、过期可显式回收、就绪判定不看时钟。"""
from __future__ import annotations

import unittest

from base import DisflowTestCase, acceptance

from disflow.engine import Engine, EngineError
from disflow.ready import is_ready, lease_expired


class LeaseTest(DisflowTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine = Engine(cwd=self.tmp)

    def _seed(self) -> None:
        self.engine.init_tree("t", "g")
        self.engine.expand("t", "root", [{"id": "a", "acceptance": acceptance()}])

    def test_start_sets_expiring_lease(self):
        self._seed()
        self.engine.start("t", "a", "w1")
        node = self.engine.load()["trees"]["t"]["nodes"]["a"]
        self.assertEqual(node["status"], "running")
        self.assertIn("expires_at", node["lease"])
        self.assertEqual(node["lease"]["holder"], "w1")

    def test_active_lease_is_not_reaped(self):
        self._seed()
        self.engine.start("t", "a", "w1")
        self.assertEqual(self.engine.reap("t"), [])
        self.assertEqual(self.engine.load()["trees"]["t"]["nodes"]["a"]["status"], "running")

    def test_expired_lease_is_reclaimed(self):
        self._seed()
        self.engine.start("t", "a", "w1", ttl=-1)          # 立即过期
        self.assertEqual(self.engine.reap("t"), ["a"])
        node = self.engine.load()["trees"]["t"]["nodes"]["a"]
        self.assertEqual(node["status"], "pending")
        self.assertIsNone(node["lease"])
        self.assertIn("租约超时", node["reclaimed"])
        # 回收后可重新派发
        self.assertEqual(self.engine.drive("t")["ready_wave"], ["a"])

    def test_lease_does_not_block_ready_after_reap(self):
        self._seed()
        self.engine.start("t", "a", "w1", ttl=-1)
        nodes = self.engine.load()["trees"]["t"]["nodes"]
        # 未 reap 前：租约存在 → 不就绪（就绪判定不自行看时钟）
        self.assertFalse(is_ready(nodes, "a"))
        self.engine.reap("t")
        nodes = self.engine.load()["trees"]["t"]["nodes"]
        self.assertTrue(is_ready(nodes, "a"))

    def test_reap_is_deterministic_and_idempotent(self):
        self._seed()
        self.engine.start("t", "a", "w1", ttl=-1)
        self.assertEqual(self.engine.reap("t"), ["a"])
        self.assertEqual(self.engine.reap("t"), [])        # 第二次无事可做

    def test_lease_expired_helper(self):
        self.assertTrue(lease_expired({"expires_at": "2000-01-01T00:00:00+00:00"}, 0.0 + 10**10))
        self.assertFalse(lease_expired({"expires_at": "2999-01-01T00:00:00+00:00"}, 0.0))
        self.assertFalse(lease_expired(None, 0.0))
        self.assertFalse(lease_expired({}, 0.0))
        self.assertFalse(lease_expired({"expires_at": "not-a-date"}, 0.0))

    def test_start_after_reap_works(self):
        self._seed()
        self.engine.start("t", "a", "w1", ttl=-1)
        self.engine.reap("t")
        self.engine.start("t", "a", "w2")
        self.assertEqual(self.engine.load()["trees"]["t"]["nodes"]["a"]["lease"]["holder"], "w2")

    def test_cannot_start_a_non_pending_node(self):
        self._seed()
        self.engine.start("t", "a", "w1")
        with self.assertRaises(EngineError):
            self.engine.start("t", "a", "w2")


if __name__ == "__main__":
    unittest.main()
