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


if __name__ == "__main__":
    unittest.main()
