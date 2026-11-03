import asyncio
import unittest
from unittest.mock import patch

from delivery.client_queue import ClientQueue, PushResult, QueueClosed


class ClientQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_fifo_and_utf8_byte_limit(self):
        queue = ClientQueue(max_messages=10, max_bytes=6)
        self.assertEqual(queue.push("你"), PushResult.ACCEPTED)
        self.assertEqual(queue.push(b"abc"), PushResult.ACCEPTED)
        self.assertEqual(queue.stats().pending_bytes, 6)
        self.assertEqual(queue.push("x"), PushResult.FULL)
        self.assertEqual(await queue.get(), "你")
        self.assertEqual(queue.stats().pending_bytes, 3)
        self.assertEqual(queue.push("de"), PushResult.ACCEPTED)
        self.assertEqual(await queue.get(), b"abc")
        self.assertEqual(await queue.get(), "de")
        self.assertEqual(queue.stats().pending_bytes, 0)

    async def test_count_bound_does_not_evict_existing_final(self):
        queue = ClientQueue(max_messages=1, max_bytes=100)
        final = '{"segment":1,"final":true}'
        self.assertEqual(queue.push(final), PushResult.ACCEPTED)
        self.assertEqual(queue.push("new update"), PushResult.FULL)
        self.assertEqual(await queue.get(), final)

    async def test_oversized_payload_rejected_without_changing_backlog(self):
        queue = ClientQueue(max_messages=10, max_bytes=3)
        queue.push("ok")
        self.assertEqual(queue.push("four"), PushResult.TOO_LARGE)
        self.assertEqual(queue.stats().depth, 1)
        self.assertEqual(await queue.get(), "ok")

    async def test_waiting_consumer_wakes_on_push(self):
        queue = ClientQueue(max_messages=1, max_bytes=10)
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())
        queue.push("hello")
        self.assertEqual(await asyncio.wait_for(waiter, 1), "hello")

    async def test_close_wakes_waiter_and_rejects_pushes(self):
        queue = ClientQueue(max_messages=1, max_bytes=10)
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        queue.close()
        queue.close()
        with self.assertRaises(QueueClosed):
            await asyncio.wait_for(waiter, 1)
        self.assertEqual(queue.push("later"), PushResult.CLOSED)

    async def test_close_releases_backlog(self):
        queue = ClientQueue(max_messages=1, max_bytes=10)
        queue.push("pending")
        queue.close()
        self.assertEqual(queue.stats().depth, 0)
        self.assertEqual(queue.stats().pending_bytes, 0)
        self.assertTrue(queue.stats().closed)
        with self.assertRaises(QueueClosed):
            await queue.get()

    async def test_cancelled_waiter_does_not_consume_future_message(self):
        queue = ClientQueue(max_messages=1, max_bytes=10)
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        queue.push("next")
        self.assertEqual(await asyncio.wait_for(queue.get(), 1), "next")

    async def test_queues_are_independent(self):
        slow = ClientQueue(max_messages=1, max_bytes=10)
        normal = ClientQueue(max_messages=1, max_bytes=10)
        slow.push("old")
        self.assertEqual(slow.push("new"), PushResult.FULL)
        self.assertEqual(normal.push("new"), PushResult.ACCEPTED)
        self.assertEqual(await normal.get(), "new")
        self.assertEqual(slow.stats().depth, 1)

    async def test_oldest_age_tracks_next_pending_message(self):
        queue = ClientQueue(max_messages=2, max_bytes=10)
        with patch("delivery.client_queue.monotonic", side_effect=[10.0, 12.0, 15.0, 16.0]):
            queue.push("a")
            queue.push("b")
            self.assertEqual(queue.stats().oldest_pending_seconds, 5.0)
            await queue.get()
            self.assertEqual(queue.stats().oldest_pending_seconds, 4.0)
        await queue.get()
        self.assertEqual(queue.stats().oldest_pending_seconds, 0.0)

    async def test_invalid_configuration_and_payload(self):
        for bad in (0, -1, True, 1.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ClientQueue(max_messages=bad, max_bytes=10)
                with self.assertRaises(ValueError):
                    ClientQueue(max_messages=1, max_bytes=bad)
        queue = ClientQueue(max_messages=1, max_bytes=10)
        with self.assertRaises(TypeError):
            queue.push({"final": True})


class CaptionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_normal_mode_keeps_all_revisions(self):
        queue = ClientQueue(max_messages=3, max_bytes=100)
        for rev in (1, 2, 3):
            self.assertEqual(queue.push_caption(
                str(rev), segment=1, revision=rev, final=rev == 3,
            ), PushResult.ACCEPTED)
        self.assertEqual([await queue.get() for _ in range(3)], ["1", "2", "3"])

    async def test_coalescing_keeps_history_other_segments_and_finals_in_order(self):
        queue = ClientQueue(max_messages=5, max_bytes=100)
        queue.push("HISTORY")
        queue.push_caption("old1", segment=1, revision=1, final=False)
        queue.push_caption("other", segment=2, revision=1, final=False)
        queue.push_caption("old2", segment=1, revision=2, final=False)
        queue.push_caption("final3", segment=3, revision=1, final=True)
        self.assertEqual(queue.push_caption(
            "final1", segment=1, revision=3, final=True, coalesce=True,
        ), PushResult.ACCEPTED)
        self.assertEqual(queue.stats().depth, 4)
        self.assertEqual(queue.stats().pending_bytes, len("HISTORYotherfinal3final1"))
        self.assertEqual([await queue.get() for _ in range(4)],
                         ["HISTORY", "other", "final3", "final1"])

    async def test_coalescing_releases_utf8_byte_capacity(self):
        queue = ClientQueue(max_messages=2, max_bytes=6)
        queue.push_caption("\u4f60", segment=1, revision=1, final=False)
        queue.push(b"abc")
        self.assertEqual(queue.push_caption(
            "\u597d", segment=1, revision=2, final=False, coalesce=True,
        ), PushResult.ACCEPTED)
        self.assertEqual(queue.stats().pending_bytes, 6)
        self.assertEqual(await queue.get(), b"abc")
        self.assertEqual(await queue.get(), "\u597d")
        self.assertEqual(queue.stats().pending_bytes, 0)

    async def test_failed_replacement_keeps_old_provisional(self):
        queue = ClientQueue(max_messages=2, max_bytes=6)
        queue.push_caption("a", segment=1, revision=1, final=False)
        queue.push("12345")
        for payload, expected in (("bb", PushResult.FULL), ("1234567", PushResult.TOO_LARGE)):
            self.assertEqual(queue.push_caption(
                payload, segment=1, revision=2, final=True, coalesce=True,
            ), expected)
            self.assertEqual(queue.stats().depth, 2)
            self.assertEqual(queue.stats().pending_bytes, 6)
        self.assertEqual(await queue.get(), "a")
        self.assertEqual(await queue.get(), "12345")

    async def test_cannot_reclaim_final_or_another_segments_latest_revision(self):
        for segment, final in ((1, True), (2, False)):
            with self.subTest(segment=segment, final=final):
                queue = ClientQueue(max_messages=1, max_bytes=100)
                queue.push_caption("keep", segment=segment, revision=1, final=final)
                self.assertEqual(queue.push_caption(
                    "new", segment=1, revision=2, final=True, coalesce=True,
                ), PushResult.FULL)
                self.assertEqual(await queue.get(), "keep")

    async def test_waiter_receives_caption_and_closed_queue_rejects_it(self):
        queue = ClientQueue(max_messages=1, max_bytes=100)
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        queue.push_caption("new", segment=1, revision=1, final=False, coalesce=True)
        self.assertEqual(await asyncio.wait_for(waiter, 1), "new")
        queue.close()
        self.assertEqual(queue.push_caption(
            "final", segment=1, revision=2, final=True, coalesce=True,
        ), PushResult.CLOSED)

    async def test_replacement_age_starts_at_new_admission(self):
        queue = ClientQueue(max_messages=1, max_bytes=100)
        with patch("delivery.client_queue.monotonic", side_effect=[10.0, 20.0, 23.0]):
            queue.push_caption("old", segment=1, revision=1, final=False)
            queue.push_caption("new", segment=1, revision=2, final=False, coalesce=True)
            self.assertEqual(queue.stats().oldest_pending_seconds, 3.0)

    async def test_invalid_metadata_does_not_change_queue(self):
        queue = ClientQueue(max_messages=1, max_bytes=100)
        valid = dict(segment=1, revision=1, final=False, coalesce=True)
        for field, value in (("segment", True), ("revision", 0), ("final", 1), ("coalesce", 1)):
            with self.subTest(field=field):
                with self.assertRaises((TypeError, ValueError)):
                    queue.push_caption("x", **(valid | {field: value}))
        self.assertEqual(queue.stats().depth, 0)


class CrossSegmentSemanticCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def test_degraded_mode_preserves_latest_interleaved_provisional(self):
        q = ClientQueue(max_messages=10, max_bytes=10000)
        self.assertIs(q.push_caption("s1-p", segment=1, revision=1, final=False), PushResult.ACCEPTED)
        self.assertIs(q.push_caption("s1-f", segment=1, revision=2, final=True), PushResult.ACCEPTED)
        self.assertIs(q.push_caption("s2-p", segment=2, revision=1, final=False), PushResult.ACCEPTED)
        self.assertIs(q.push("opaque"), PushResult.ACCEPTED)
        self.assertIs(
            q.push_caption(
                "s3-p", segment=3, revision=1, final=False,
                coalesce=True,
            ),
            PushResult.ACCEPTED,
        )
        self.assertEqual(await q.get(), "s1-f")
        self.assertEqual(await q.get(), "s2-p")
        self.assertEqual(await q.get(), "opaque")
        self.assertEqual(await q.get(), "s3-p")

    async def test_normal_same_segment_coalescing_does_not_drop_other_segments(self):
        q = ClientQueue(max_messages=10, max_bytes=10000)
        q.push_caption("s1-p", segment=1, revision=1, final=False)
        q.push_caption("s2-p1", segment=2, revision=1, final=False)
        q.push_caption("s2-p2", segment=2, revision=2, final=False, coalesce=True)
        self.assertEqual(await q.get(), "s1-p")
        self.assertEqual(await q.get(), "s2-p2")

    async def test_queue_wide_cleanup_never_removes_finals(self):
        q = ClientQueue(max_messages=10, max_bytes=10000)
        q.push_caption("s1-f", segment=1, revision=3, final=True)
        q.push_caption("s2-f", segment=2, revision=3, final=True)
        q.push_caption(
            "s3-f", segment=3, revision=3, final=True,
            coalesce=True,
        )
        self.assertEqual(await q.get(), "s1-f")
        self.assertEqual(await q.get(), "s2-f")
        self.assertEqual(await q.get(), "s3-f")

    async def test_cleanup_removes_superseded_versions_across_entire_queue(self):
        q = ClientQueue(max_messages=5, max_bytes=1000)
        q.push_caption("s1-old", segment=1, revision=1, final=False)
        q.push_caption("s2-old", segment=2, revision=1, final=False)
        q.push_caption("s1-latest", segment=1, revision=2, final=False)
        q.push_caption("s2-final", segment=2, revision=2, final=True)
        q.push("history")
        self.assertIs(q.push_caption("s3-new", segment=3, revision=1,
                                     final=False, coalesce=True), PushResult.ACCEPTED)
        self.assertEqual(q.stats().pending_bytes,
                         len("s1-latest" + "s2-final" + "history" + "s3-new"))
        self.assertEqual([await q.get() for _ in range(4)],
                         ["s1-latest", "s2-final", "history", "s3-new"])

    async def test_full_queue_keeps_unsuperseded_active_segments(self):
        for max_bytes in (100, 4):
            with self.subTest(max_bytes=max_bytes):
                q = ClientQueue(max_messages=2, max_bytes=max_bytes)
                q.push_caption("aa", segment=10, revision=2, final=False)
                q.push_caption("bb", segment=11, revision=1, final=False)
                self.assertIs(q.push_caption("cc", segment=12, revision=1,
                                             final=False, coalesce=True), PushResult.FULL)
                self.assertEqual(q.stats().pending_bytes, 4)
                self.assertEqual([await q.get(), await q.get()], ["aa", "bb"])

    async def test_failed_global_cleanup_is_atomic(self):
        q = ClientQueue(max_messages=4, max_bytes=10)
        q.push_caption("aa", segment=1, revision=1, final=False)
        q.push_caption("bb", segment=1, revision=2, final=False)
        q.push_caption("cc", segment=2, revision=1, final=False)
        self.assertIs(q.push_caption("1234567", segment=3, revision=1,
                                     final=False, coalesce=True), PushResult.FULL)
        self.assertEqual(q.stats().pending_bytes, 6)
        self.assertEqual([await q.get() for _ in range(3)], ["aa", "bb", "cc"])


if __name__ == "__main__":
    unittest.main()
