"""Protected final admission, latest-state preservation and fair scheduling."""

import asyncio
import unittest

from delivery.client_queue import PushResult, QueueClosed
from delivery.final_aware_queue import FinalAwareQueue


class FinalAwareQueueTests(unittest.IsolatedAsyncioTestCase):
    def queue(self, **overrides):
        options = dict(max_messages=8, max_bytes=1000, max_active_segments=8)
        options.update(overrides)
        return FinalAwareQueue(**options)

    def push(self, queue, segment, revision=1, final=False, degraded=True, payload=None):
        if payload is None:
            payload = f"s{segment}r{revision}{'F' if final else 'P'}"
        return queue.push_caption(payload, segment=segment, revision=revision,
                                  final=final, coalesce=degraded)

    async def test_normal_preserves_every_revision_in_fifo_order(self):
        q = self.queue()
        for segment, revision, final in ((1, 1, False), (2, 1, False), (1, 2, True)):
            self.assertIs(self.push(q, segment, revision, final, degraded=False), PushResult.ACCEPTED)
        self.assertFalse(q.stats().degraded)
        self.assertEqual([await q.get() for _ in range(3)], ["s1r1P", "s2r1P", "s1r2F"])

    def test_invalid_lane_settings_are_rejected(self):
        for field in ("max_active_segments", "final_burst"):
            for value in (0, -1, True, 1.5):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        self.queue(**{field: value})

    async def test_new_provisional_cannot_take_final_fifo_capacity(self):
        q = self.queue(max_messages=2)
        self.push(q, 1, final=True, degraded=False)
        self.push(q, 2, final=True, degraded=False)
        self.assertIs(self.push(q, 3, degraded=False), PushResult.FULL)
        self.assertIs(self.push(q, 3), PushResult.ACCEPTED)
        self.assertEqual((q.stats().final_depth, q.stats().active_depth), (2, 1))
        self.assertEqual([await q.get() for _ in range(3)], ["s1r1F", "s2r1F", "s3r1P"])

    async def test_full_active_map_does_not_block_unrelated_final(self):
        q = self.queue(max_active_segments=1)
        self.push(q, 1)
        self.assertIs(self.push(q, 2, final=True), PushResult.ACCEPTED)
        self.assertEqual([await q.get(), await q.get()], ["s2r1F", "s1r1P"])

    async def test_final_removes_its_own_provisional_but_not_other_latest(self):
        q = self.queue()
        self.push(q, 1)
        self.push(q, 2)
        self.push(q, 1, revision=2)
        self.push(q, 1, revision=3, final=True)
        self.assertEqual((q.stats().final_depth, q.stats().active_depth), (1, 1))
        self.assertEqual([await q.get(), await q.get()], ["s1r3F", "s2r1P"])

    async def test_transition_compacts_existing_fifo_and_preserves_final_order(self):
        q = self.queue()
        self.push(q, 1, degraded=False)
        self.push(q, 2, degraded=False)
        self.push(q, 1, revision=2, final=True, degraded=False)
        self.push(q, 3, final=True, degraded=False)
        self.push(q, 4)
        self.assertEqual([await q.get() for _ in range(4)], ["s1r2F", "s3r1F", "s2r1P", "s4r1P"])

    async def test_low_final_pressure_gives_active_state_a_turn(self):
        q = self.queue()
        self.push(q, 100)
        for segment in range(1, 5):
            self.push(q, segment, final=True)
            self.assertEqual(await q.get(), f"s{segment}r1F")
        self.push(q, 5, final=True)
        self.assertEqual(await q.get(), "s100r1P")
        self.assertEqual(await q.get(), "s5r1F")

    async def test_growing_final_backlog_overrides_fixed_burst_share(self):
        q = self.queue(max_messages=12, final_burst=1)
        self.push(q, 100)
        for segment in range(1, 9):
            self.push(q, segment, final=True)
        self.assertEqual([await q.get() for _ in range(5)], [f"s{n}r1F" for n in range(1, 6)])
        self.assertEqual(await q.get(), "s100r1P")

    async def test_active_replacement_keeps_its_turn(self):
        q = self.queue()
        self.push(q, 1)
        self.push(q, 2)
        self.push(q, 1, revision=2)
        self.assertEqual([await q.get(), await q.get()], ["s1r2P", "s2r1P"])

    async def test_return_to_normal_restores_retained_arrival_order(self):
        q = self.queue()
        self.push(q, 1, degraded=False)
        self.push(q, 2, degraded=False)
        self.push(q, 1, revision=2)
        self.push(q, 3, degraded=False)
        self.assertFalse(q.stats().degraded)
        self.assertEqual([await q.get() for _ in range(3)], ["s2r1P", "s1r2P", "s3r1P"])

    async def test_separate_utf8_byte_budgets_and_close(self):
        q = self.queue(max_bytes=6)
        self.push(q, 1, final=True, payload="你你")
        self.assertIs(self.push(q, 2, payload="好好"), PushResult.ACCEPTED)
        self.assertEqual(q.stats().pending_bytes, 12)
        self.assertEqual((q.stats().final_bytes, q.stats().active_bytes), (6, 6))
        self.assertIs(self.push(q, 2, revision=2, payload="新新"), PushResult.ACCEPTED)
        q.close()
        self.assertEqual(q.stats().pending_bytes, 0)
        self.assertEqual(q.stats().depth, 0)
        with self.assertRaises(QueueClosed):
            await q.get()

    async def test_failed_transition_keeps_normal_backlog_unchanged(self):
        q = self.queue(max_messages=2)
        self.push(q, 1, final=True, degraded=False)
        self.push(q, 2, final=True, degraded=False)
        before = q.stats()
        self.assertIs(self.push(q, 3, final=True), PushResult.FULL)
        self.assertEqual(q.stats().depth, before.depth)
        self.assertEqual(q.stats().pending_bytes, before.pending_bytes)
        self.assertFalse(q.stats().degraded)
        self.assertEqual([await q.get(), await q.get()], ["s1r1F", "s2r1F"])

    async def test_failed_active_replacement_is_atomic(self):
        q = self.queue(max_bytes=6)
        self.push(q, 1, payload="a")
        self.push(q, 2, payload="12345")
        before = q.stats()
        self.assertIs(self.push(q, 1, revision=2, payload="bb"), PushResult.FULL)
        self.assertEqual(q.stats().pending_bytes, before.pending_bytes)
        self.assertEqual([await q.get(), await q.get()], ["a", "12345"])

    async def test_normal_waiter_wakes_after_transition_to_degraded(self):
        q = self.queue()
        task = asyncio.create_task(q.get())
        await asyncio.sleep(0)
        self.push(q, 1, final=True)
        self.assertEqual(await asyncio.wait_for(task, 1), "s1r1F")

    async def test_closed_queue_rejects_all_pushes_and_wakes_waiter(self):
        q = self.queue()
        self.push(q, 1)
        await q.get()
        task = asyncio.create_task(q.get())
        await asyncio.sleep(0)
        q.close()
        q.close()
        with self.assertRaises(QueueClosed):
            await asyncio.wait_for(task, 1)
        self.assertIs(self.push(q, 1), PushResult.CLOSED)
        self.assertIs(q.push("control"), PushResult.CLOSED)

    async def test_control_messages_share_protected_fifo(self):
        q = self.queue()
        q.push("control")
        self.push(q, 1)
        self.push(q, 2, final=True)
        self.assertEqual([await q.get() for _ in range(3)], ["control", "s2r1F", "s1r1P"])


if __name__ == "__main__":
    unittest.main()
