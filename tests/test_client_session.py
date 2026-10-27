import asyncio
import unittest

from delivery.client_queue import ClientQueue, PushResult
from delivery.client_session import run_client_session


class FakeConnection:
    def __init__(self, *, send_error=None):
        self.closed = asyncio.Event()
        self.sending = asyncio.Event()
        self.release_send = asyncio.Event()
        self.closing = asyncio.Event()
        self.release_close = asyncio.Event()
        self.release_close.set()
        self.send_error = send_error
        self.waiter_stopped = False
        self.close_calls = 0

    async def send(self, payload):
        self.sending.set()
        if self.send_error:
            raise self.send_error
        await self.release_send.wait()

    async def wait_closed(self):
        try:
            await self.closed.wait()
        finally:
            self.waiter_stopped = True

    async def close(self, code=1000, reason=""):
        self.close_calls += 1
        self.closing.set()
        await self.release_close.wait()
        self.closed.set()


class ClientSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = {}
        self.tasks_before = set(asyncio.all_tasks())

    async def asyncTearDown(self):
        leftovers = {
            task for task in asyncio.all_tasks()
            if task not in self.tasks_before and task is not asyncio.current_task()
        }
        for task in leftovers:
            task.cancel()
        if leftovers:
            await asyncio.gather(*leftovers, return_exceptions=True)
        self.assertFalse(leftovers, "Client tasks leaked after session cleanup")

    async def start_session(self, ws, queue=None, **kwargs):
        if queue is None:
            queue = ClientQueue(max_messages=10, max_bytes=1024)
        task = asyncio.create_task(run_client_session(ws, queue, self.clients, **kwargs))

        async def cleanup():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cleanup)
        # Give the session and its child tasks a chance to start.
        for _ in range(3):
            await asyncio.sleep(0)
        return queue, task

    def assert_clean(self, ws, queue):
        self.assertNotIn(ws, self.clients)
        self.assertTrue(queue.stats().closed)
        self.assertEqual(queue.stats().depth, 0)
        self.assertEqual(queue.stats().pending_bytes, 0)
        self.assertEqual(queue.push("later"), PushResult.CLOSED)
        self.assertTrue(ws.waiter_stopped)

    async def test_disconnect_with_empty_queue(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws)
        self.assertIs(self.clients[ws], queue)
        ws.closed.set()
        await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)

    async def test_disconnect_cancels_blocked_send_and_releases_pending(self):
        ws = FakeConnection()
        queue = ClientQueue(max_messages=10, max_bytes=1024)
        queue.push("in flight")
        queue.push("pending")
        queue, task = await self.start_session(ws, queue)
        await asyncio.wait_for(ws.sending.wait(), 1)
        ws.closed.set()
        await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)

    async def test_handler_cancellation_propagates_after_cleanup(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_clean(ws, queue)
        self.assertTrue(ws.closed.is_set())

    async def test_send_failure_closes_session_and_propagates(self):
        ws = FakeConnection(send_error=OSError("broken connection"))
        queue, task = await self.start_session(ws)
        queue.push("message")
        with self.assertRaisesRegex(OSError, "broken connection"):
            await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)

    async def test_queue_close_also_ends_connection_lifecycle(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws)
        queue.close()
        await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)
        self.assertTrue(ws.closed.is_set())

    async def test_send_timeout_unregisters_client(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws, send_timeout=0.02)
        queue.push("blocked")
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)

    async def test_stuck_close_still_releases_tasks_and_registry(self):
        ws = FakeConnection()
        ws.release_close.clear()
        queue, task = await self.start_session(ws, close_timeout=0.02)
        ws.closed.set()
        with self.assertLogs(level="WARNING"):
            await asyncio.wait_for(task, 1)
        self.assert_clean(ws, queue)

    async def test_repeated_cancellation_does_not_abandon_cleanup(self):
        ws = FakeConnection()
        ws.release_close.clear()
        queue, task = await self.start_session(ws)
        task.cancel()
        await asyncio.wait_for(ws.closing.wait(), 1)
        self.assertNotIn(ws, self.clients)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        ws.release_close.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_clean(ws, queue)

    async def test_replacement_registry_entry_is_preserved(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws)
        replacement = ClientQueue(max_messages=1, max_bytes=10)
        self.clients[ws] = replacement
        ws.closed.set()
        await asyncio.wait_for(task, 1)
        self.assertIs(self.clients[ws], replacement)
        self.assertFalse(replacement.stats().closed)
        self.assertTrue(queue.stats().closed)

    async def test_duplicate_registration_does_not_damage_existing_client(self):
        ws = FakeConnection()
        queue, task = await self.start_session(ws)
        other = ClientQueue(max_messages=1, max_bytes=10)
        with self.assertRaisesRegex(ValueError, "already registered"):
            await run_client_session(ws, other, self.clients)
        self.assertIs(self.clients[ws], queue)
        self.assertFalse(queue.stats().closed)
        ws.closed.set()
        await asyncio.wait_for(task, 1)

    async def test_many_disconnects_leave_no_registered_clients_or_tasks(self):
        sessions = []
        for _ in range(25):
            ws = FakeConnection()
            queue, task = await self.start_session(ws)
            sessions.append((ws, queue, task))
        self.assertEqual(len(self.clients), 25)
        for ws, _, _ in sessions:
            ws.closed.set()
        await asyncio.wait_for(asyncio.gather(*(task for _, _, task in sessions)), 2)
        self.assertEqual(self.clients, {})
        for ws, queue, _ in sessions:
            self.assert_clean(ws, queue)


if __name__ == "__main__":
    unittest.main()
