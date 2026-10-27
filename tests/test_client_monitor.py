import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client_monitor import ClientMonitor
from client_queue import ClientQueue
from client_sender import run_client_sender
from client_session import run_client_session


class FakeConnection:
    def __init__(self, *, blocked=False, error=None):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        if not blocked:
            self.release.set()
        self.error = error
        self.buffer_size = 0
        self.transport = SimpleNamespace(get_write_buffer_size=lambda: self.buffer_size)

    async def send(self, payload):
        self.started.set()
        await self.release.wait()
        if self.error:
            raise self.error

    async def close(self, code=1000, reason=""):
        self.closed.set()

    async def wait_closed(self):
        await self.closed.wait()


class ClientMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.queue = ClientQueue(max_messages=10, max_bytes=1024)
        self.monitor = ClientMonitor()

    def start_task(self, coroutine):
        task = asyncio.create_task(coroutine)

        async def cleanup():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        return task

    async def test_snapshot_is_read_only_and_transport_size_is_live(self):
        ws = FakeConnection()
        self.queue.push("pending")
        ws.buffer_size = 123
        first = self.monitor.snapshot(self.queue, ws)
        self.assertEqual(first.queue.depth, 1)
        self.assertEqual(first.queue.pending_bytes, 7)
        self.assertEqual(first.write_buffer_bytes, 123)
        self.assertFalse(first.in_flight)
        self.assertIsNone(first.last_send_seconds)
        with self.assertRaises(FrozenInstanceError):
            first.sent_messages = 99
        ws.buffer_size = 0
        self.assertEqual(self.monitor.snapshot(self.queue, ws).write_buffer_bytes, 0)
        self.assertEqual(first.write_buffer_bytes, 123)
        self.assertEqual(await self.queue.get(), "pending")

    async def test_unknown_transport_is_not_reported_as_zero(self):
        def unavailable():
            raise RuntimeError("transport closed")

        for ws in (
            object(),
            SimpleNamespace(transport=None),
            SimpleNamespace(transport=SimpleNamespace(get_write_buffer_size=unavailable)),
            SimpleNamespace(transport=SimpleNamespace(get_write_buffer_size=lambda: -1)),
        ):
            with self.subTest(ws=ws):
                self.assertIsNone(self.monitor.snapshot(self.queue, ws).write_buffer_bytes)

    async def test_monotonic_timing_and_utf8_payload_accounting(self):
        ws = FakeConnection()
        with patch("client_monitor.monotonic", side_effect=[10.0, 12.0, 13.0]):
            self.monitor.send_started("\u4f60")
            during = self.monitor.snapshot(self.queue, ws)
            self.assertEqual(during.in_flight_seconds, 2.0)
            self.assertEqual(during.in_flight_bytes, 3)
            self.monitor.send_finished("sent")
        after = self.monitor.snapshot(self.queue, ws)
        self.assertFalse(after.in_flight)
        self.assertEqual(after.in_flight_bytes, 0)
        self.assertEqual(after.in_flight_seconds, 0)
        self.assertEqual(after.sent_messages, 1)
        self.assertEqual(after.sent_payload_bytes, 3)
        self.assertEqual(after.last_send_seconds, 3)

    async def test_blocked_send_visible_even_after_queue_is_empty(self):
        ws = FakeConnection(blocked=True)
        self.queue.push("hello")
        task = self.start_task(run_client_sender(ws, self.queue, monitor=self.monitor))
        await asyncio.wait_for(ws.started.wait(), 1)
        stats = self.monitor.snapshot(self.queue, ws)
        self.assertEqual(stats.queue.depth, 0)
        self.assertTrue(stats.in_flight)
        self.assertEqual(stats.in_flight_bytes, 5)
        self.assertEqual(stats.sent_messages, 0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        stats = self.monitor.snapshot(self.queue, ws)
        self.assertFalse(stats.in_flight)
        self.assertEqual(stats.send_errors, 0)
        self.assertEqual(stats.send_timeouts, 0)

    async def test_sender_success_counts_text_and_binary(self):
        ws = FakeConnection()
        self.queue.push("\u4f60")
        self.queue.push(b"ab")
        task = self.start_task(run_client_sender(ws, self.queue, monitor=self.monitor))

        async def wait_for_completion():
            while self.monitor.snapshot(self.queue, ws).sent_messages != 2:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_completion(), 1)
        self.queue.close()
        await asyncio.wait_for(task, 1)
        stats = self.monitor.snapshot(self.queue, ws)
        self.assertEqual(stats.sent_messages, 2)
        self.assertEqual(stats.sent_payload_bytes, 5)
        self.assertFalse(stats.in_flight)

    async def test_timeout_and_send_error_have_separate_counters(self):
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                queue = ClientQueue(max_messages=1, max_bytes=100)
                monitor = ClientMonitor()
                ws = FakeConnection(blocked=timeout, error=None if timeout else OSError("broken"))
                queue.push("message")
                task = self.start_task(run_client_sender(
                    ws, queue, send_timeout=0.02, monitor=monitor
                ))
                with self.assertRaises(asyncio.TimeoutError if timeout else OSError):
                    await asyncio.wait_for(task, 1)
                stats = monitor.snapshot(queue, ws)
                self.assertEqual(stats.send_timeouts, int(timeout))
                self.assertEqual(stats.send_errors, int(not timeout))
                self.assertEqual(stats.sent_messages, 0)
                self.assertFalse(stats.in_flight)

    async def test_session_passes_monitor_and_preserves_final_snapshot(self):
        ws, clients = FakeConnection(blocked=True), {}
        self.queue.push("pending")
        task = self.start_task(run_client_session(
            ws, self.queue, clients, monitor=self.monitor
        ))
        await asyncio.wait_for(ws.started.wait(), 1)
        self.assertTrue(self.monitor.snapshot(self.queue, ws).in_flight)
        ws.closed.set()
        await asyncio.wait_for(task, 1)
        stats = self.monitor.snapshot(self.queue, ws)
        self.assertFalse(stats.in_flight)
        self.assertTrue(stats.queue.closed)
        self.assertEqual(stats.queue.pending_bytes, 0)
        self.assertEqual(clients, {})

    async def test_monitor_state_cannot_mix_two_senders(self):
        self.monitor.send_started(b"")
        self.assertTrue(self.monitor.snapshot(self.queue, object()).in_flight)
        with self.assertRaises(RuntimeError):
            self.monitor.send_started("second")
        self.monitor.send_finished("cancelled")
        with self.assertRaises(RuntimeError):
            self.monitor.send_finished("sent")


if __name__ == "__main__":
    unittest.main()
