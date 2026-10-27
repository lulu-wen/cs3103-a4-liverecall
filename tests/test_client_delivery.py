"""Per-client delivery integration using controlled peers, not T3 benchmarks."""

import asyncio
import json
import unittest

from delivery.client_monitor import ClientMonitor
from delivery.client_queue import ClientQueue, PushResult
from delivery.client_session import run_client_session


class ControlledConnection:
    def __init__(self, *, blocked=False, error=None):
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.closed = asyncio.Event()
        self.messages = []
        self.error = error

    async def send(self, payload):
        await self.release.wait()
        if self.error:
            raise self.error
        self.messages.append(payload)

    async def wait_closed(self):
        await self.closed.wait()

    async def close(self, code=1000, reason=""):
        self.closed.set()


class ClientDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clients = {}
        self.tasks = []
        self.tasks_before = set(asyncio.all_tasks())

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        leftovers = {
            task for task in asyncio.all_tasks()
            if task not in self.tasks_before and task is not asyncio.current_task()
        }
        for task in leftovers:
            task.cancel()
        if leftovers:
            await asyncio.gather(*leftovers, return_exceptions=True)
        self.assertFalse(leftovers, "Delivery child tasks leaked")
        self.assertEqual(self.clients, {})

    async def wait_until(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0)

        await asyncio.wait_for(poll(), 5)

    def start_client(self, ws):
        queue = ClientQueue(max_messages=4, max_bytes=256)
        monitor = ClientMonitor()
        task = asyncio.create_task(run_client_session(
            ws, queue, self.clients, monitor=monitor, send_timeout=30
        ))
        self.tasks.append(task)
        return queue, monitor, task

    async def test_100_clients_with_10_blocked_readers_and_recovery(self):
        connections = []
        history = '{"type":"HISTORY","items":[]}'
        for index in range(100):
            ws = ControlledConnection(blocked=index < 10)
            queue, monitor, task = self.start_client(ws)
            self.assertEqual(queue.push(history), PushResult.ACCEPTED)
            connections.append((ws, queue, monitor, task, [history]))

        await self.wait_until(lambda: len(self.clients) == 100 and all(
            monitor.snapshot(queue, ws).in_flight if index < 10
            else monitor.snapshot(queue, ws).sent_messages == 1
            for index, (ws, queue, monitor, _, _) in enumerate(connections)
        ))
        rejected = 0
        for number in range(12):
            payload = json.dumps({"segment": number // 2 + 1, "final": bool(number % 2)})
            for index, (ws, queue, monitor, _, accepted) in enumerate(connections):
                result = queue.push(payload)
                if result is PushResult.ACCEPTED:
                    accepted.append(payload)
                else:
                    self.assertLess(index, 10)
                    self.assertEqual(result, PushResult.FULL)
                    rejected += 1
                stats = monitor.snapshot(queue, ws)
                self.assertLessEqual(stats.queue.depth, 4)
                self.assertLessEqual(stats.queue.pending_bytes, 256)
            # No network await in the producer loop above. Normal senders must
            # complete while the ten slow senders remain blocked.
            await self.wait_until(lambda: all(
                monitor.snapshot(queue, ws).sent_messages == len(accepted)
                for ws, queue, monitor, _, accepted in connections[10:]
            ))

        self.assertGreater(rejected, 0)
        self.assertTrue(all(not ws.messages for ws, *_ in connections[:10]))
        for ws, *_ in connections[:10]:
            ws.release.set()
        await self.wait_until(lambda: all(
            monitor.snapshot(queue, ws).sent_messages == len(accepted)
            for ws, queue, monitor, _, accepted in connections
        ))
        for ws, queue, monitor, _, accepted in connections:
            self.assertEqual(ws.messages, accepted)
            self.assertEqual(monitor.snapshot(queue, ws).sent_messages, len(accepted))
            ws.closed.set()
        await asyncio.wait_for(asyncio.gather(*self.tasks), 5)
        for ws, queue, monitor, _, _ in connections:
            stats = monitor.snapshot(queue, ws)
            self.assertTrue(stats.queue.closed)
            self.assertEqual(stats.queue.pending_bytes, 0)
            self.assertFalse(stats.in_flight)

    async def test_failed_session_does_not_unregister_healthy_client(self):
        broken = ControlledConnection(error=OSError("broken"))
        healthy = ControlledConnection()
        broken_queue, _, broken_task = self.start_client(broken)
        healthy_queue, monitor, healthy_task = self.start_client(healthy)
        await self.wait_until(lambda: len(self.clients) == 2)
        broken_queue.push("trigger failure")
        with self.assertRaisesRegex(OSError, "broken"):
            await asyncio.wait_for(broken_task, 1)
        self.assertNotIn(broken, self.clients)
        self.assertIn(healthy, self.clients)
        self.assertEqual(healthy_queue.push("still usable"), PushResult.ACCEPTED)
        await self.wait_until(lambda: monitor.snapshot(healthy_queue, healthy).sent_messages == 1)
        self.assertEqual(healthy.messages, ["still usable"])
        healthy.closed.set()
        await asyncio.wait_for(healthy_task, 1)


if __name__ == "__main__":
    unittest.main()
