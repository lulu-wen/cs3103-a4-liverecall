import asyncio
import unittest

from client_queue import ClientQueue
from client_sender import run_client_sender


class FakeConnection:
    def __init__(self, *, blocked=False, error=None, blocked_close=False, expected_messages=1):
        self.messages = []
        self.started = asyncio.Event()
        self.sent = asyncio.Event()
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.error = error
        self.blocked_close = blocked_close
        self.closed = None
        self.expected_messages = expected_messages

    async def send(self, message):
        self.started.set()
        await self.release.wait()
        if self.error is not None:
            raise self.error
        self.messages.append(message)
        if len(self.messages) >= self.expected_messages:
            self.sent.set()

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)
        if self.blocked_close:
            await asyncio.Event().wait()


class ClientSenderTests(unittest.IsolatedAsyncioTestCase):
    def queue(self):
        return ClientQueue(max_messages=10, max_bytes=1024)

    def start_sender(self, ws, queue, **kwargs):
        task = asyncio.create_task(run_client_sender(ws, queue, **kwargs))

        async def cleanup():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        return task

    async def test_fifo_and_normal_queue_close(self):
        queue = self.queue()
        ws = FakeConnection(expected_messages=3)
        for payload in ('{"type":"HISTORY","items":[]}', "update", b"binary"):
            queue.push(payload)
        task = self.start_sender(ws, queue)
        await asyncio.wait_for(ws.sent.wait(), 1)
        self.assertEqual(ws.messages, ['{"type":"HISTORY","items":[]}', "update", b"binary"])
        queue.close()
        await asyncio.wait_for(task, 1)
        self.assertEqual(ws.closed[0], 1000)

    async def test_slow_sender_does_not_block_normal_sender(self):
        slow_ws = FakeConnection(blocked=True)
        normal_ws = FakeConnection()
        slow_queue, normal_queue = self.queue(), self.queue()
        slow_queue.push("slow")
        normal_queue.push("normal")
        slow_task = self.start_sender(slow_ws, slow_queue)
        normal_task = self.start_sender(normal_ws, normal_queue)
        await asyncio.wait_for(normal_ws.sent.wait(), 1)
        self.assertEqual(normal_ws.messages, ["normal"])
        self.assertEqual(slow_ws.messages, [])
        self.assertFalse(slow_task.done())
        normal_queue.close()
        await asyncio.wait_for(normal_task, 1)

    async def test_send_timeout_closes_connection_and_discards_pending(self):
        ws, queue = FakeConnection(blocked=True), self.queue()
        queue.push("in flight")
        queue.push("pending")
        task = self.start_sender(ws, queue, send_timeout=0.02)
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(task, 1)
        self.assertEqual(ws.closed[0], 1013)
        self.assertTrue(queue.stats().closed)
        self.assertEqual(queue.stats().pending_bytes, 0)
        self.assertEqual(ws.messages, [])

    async def test_send_error_propagates_without_retry(self):
        ws, queue = FakeConnection(error=OSError("disconnected")), self.queue()
        queue.push("first")
        queue.push("second")
        task = self.start_sender(ws, queue)
        with self.assertRaisesRegex(OSError, "disconnected"):
            await asyncio.wait_for(task, 1)
        self.assertEqual(ws.closed[0], 1011)
        self.assertTrue(queue.stats().closed)
        self.assertEqual(ws.messages, [])

    async def test_cancellation_while_waiting_and_while_sending(self):
        for sending in (False, True):
            with self.subTest(sending=sending):
                ws, queue = FakeConnection(blocked=True), self.queue()
                if sending:
                    queue.push("in flight")
                task = self.start_sender(ws, queue)
                if sending:
                    await asyncio.wait_for(ws.started.wait(), 1)
                else:
                    await asyncio.sleep(0)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                self.assertTrue(queue.stats().closed)
                self.assertIsNotNone(ws.closed)

    async def test_close_deadline_preserves_original_send_error(self):
        ws = FakeConnection(error=OSError("send failed"), blocked_close=True)
        queue = self.queue()
        queue.push("message")
        task = self.start_sender(ws, queue, close_timeout=0.02)
        with self.assertLogs("client_sender", level="WARNING"):
            with self.assertRaisesRegex(OSError, "send failed"):
                await asyncio.wait_for(task, 1)
        self.assertTrue(queue.stats().closed)

    async def test_invalid_deadlines(self):
        for name in ("send_timeout", "close_timeout"):
            for bad in (0, -1, True, float("nan"), float("inf")):
                with self.subTest(name=name, bad=bad):
                    with self.assertRaises(ValueError):
                        await run_client_sender(FakeConnection(), self.queue(), **{name: bad})


if __name__ == "__main__":
    unittest.main()
