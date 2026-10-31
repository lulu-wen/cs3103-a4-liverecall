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

    def start_client(self, ws, *, max_messages=4, max_bytes=256):
        queue = ClientQueue(max_messages=max_messages, max_bytes=max_bytes)
        monitor = ClientMonitor()
        task = asyncio.create_task(run_client_session(
            ws, queue, self.clients, monitor=monitor, send_timeout=30
        ))
        self.tasks.append(task)
        return queue, monitor, task

    async def test_coalescing_preserves_in_flight_revision_and_final_delivery(self):
        slow = ControlledConnection(blocked=True)
        healthy = ControlledConnection()
        queue, monitor, task = self.start_client(slow, max_bytes=1024)
        healthy_queue, healthy_monitor, healthy_task = self.start_client(healthy)

        def caption(segment, revision, final=False):
            return json.dumps({
                "type": "CAPTION_UPDATE", "segment": segment,
                "revision": revision, "final": final,
                "text": "\u5b57\u5e55", "source_ts": 100.0,
            }, ensure_ascii=False)

        first = caption(1, 1)
        self.assertEqual(queue.push_caption(
            first, segment=1, revision=1, final=False,
        ), PushResult.ACCEPTED)
        await self.wait_until(lambda: monitor.snapshot(queue, slow).in_flight)
        self.assertEqual(queue.stats().depth, 0)

        queue.push_caption(caption(1, 2), segment=1, revision=2, final=False)
        other_final = caption(2, 1, True)
        queue.push_caption(other_final, segment=2, revision=1, final=True)
        queue.push_caption(caption(1, 3), segment=1, revision=3, final=False)
        latest_final = caption(1, 4, True)
        self.assertEqual(queue.push_caption(
            latest_final, segment=1, revision=4, final=True, coalesce=True,
        ), PushResult.ACCEPTED)
        stats = monitor.snapshot(queue, slow)
        self.assertEqual(stats.queue.depth, 2)
        self.assertEqual(stats.queue.pending_bytes,
                         len((other_final + latest_final).encode("utf-8")))
        self.assertEqual(stats.in_flight_bytes, len(first.encode("utf-8")))

        healthy_queue.push("healthy update")
        await self.wait_until(lambda: healthy_monitor.snapshot(
            healthy_queue, healthy,
        ).sent_messages == 1)
        self.assertEqual(healthy.messages, ["healthy update"])
        self.assertEqual(slow.messages, [])

        slow.release.set()
        await self.wait_until(lambda: monitor.snapshot(queue, slow).sent_messages == 3)
        self.assertEqual(slow.messages, [first, other_final, latest_final])
        stats = monitor.snapshot(queue, slow)
        self.assertEqual(stats.sent_payload_bytes,
                         sum(len(payload.encode("utf-8")) for payload in slow.messages))
        self.assertEqual(stats.queue.pending_bytes, 0)
        self.assertFalse(stats.in_flight)
        slow.closed.set()
        healthy.closed.set()
        await asyncio.wait_for(asyncio.gather(task, healthy_task), 5)

    async def test_normal_mode_sends_history_and_every_caption_revision(self):
        ws = ControlledConnection(blocked=True)
        queue, monitor, task = self.start_client(ws, max_bytes=1024)
        history = '{"type":"HISTORY","items":[]}'
        queue.push(history)
        await self.wait_until(lambda: monitor.snapshot(queue, ws).in_flight)
        expected = [history]
        for revision in (1, 2, 3):
            payload = json.dumps({
                "type": "CAPTION_UPDATE", "segment": 1,
                "revision": revision, "final": revision == 3,
                "text": "caption", "source_ts": 100.0,
            })
            self.assertEqual(queue.push_caption(
                payload, segment=1, revision=revision, final=revision == 3,
            ), PushResult.ACCEPTED)
            expected.append(payload)
        ws.release.set()
        await self.wait_until(lambda: monitor.snapshot(queue, ws).sent_messages == 4)
        self.assertEqual(ws.messages, expected)
        ws.closed.set()
        await asyncio.wait_for(task, 5)

    async def test_rejected_replacement_still_sends_original_backlog(self):
        ws = ControlledConnection(blocked=True)
        queue, monitor, task = self.start_client(ws, max_messages=1, max_bytes=64)
        queue.push("in flight")
        await self.wait_until(lambda: monitor.snapshot(queue, ws).in_flight)
        old = '{"segment":1,"revision":1,"final":false}'
        queue.push_caption(old, segment=1, revision=1, final=False)
        # The replacement itself cannot fit, so the old caption must survive.
        self.assertEqual(queue.push_caption(
            "x" * 65, segment=1, revision=2, final=True, coalesce=True,
        ), PushResult.TOO_LARGE)
        self.assertEqual(queue.stats().pending_bytes, len(old))
        ws.release.set()
        await self.wait_until(lambda: monitor.snapshot(queue, ws).sent_messages == 2)
        self.assertEqual(ws.messages, ["in flight", old])
        ws.closed.set()
        await asyncio.wait_for(task, 5)

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

    async def test_revision_burst_keeps_latest_active_and_completed_segments_bounded(self):
        ws = ControlledConnection(blocked=True)
        queue, monitor, task = self.start_client(ws, max_messages=5, max_bytes=2048)
        history = '{"type":"HISTORY","items":[]}'
        queue.push(history)
        await self.wait_until(lambda: monitor.snapshot(queue, ws).in_flight)
        latest = {}
        # Policy is deliberately enabled by this test, not by the real server.
        for revision in range(1, 101):
            for segment in range(1, 6):
                final = revision == 100 and segment <= 3
                payload = json.dumps({
                    "type": "CAPTION_UPDATE", "segment": segment,
                    "revision": revision, "final": final,
                    "text": "caption " + "x" * (revision % 20), "source_ts": 100.0,
                })
                self.assertEqual(queue.push_caption(
                    payload, segment=segment, revision=revision,
                    final=final, coalesce=True,
                ), PushResult.ACCEPTED)
                latest[segment] = payload
                stats = queue.stats()
                self.assertLessEqual(stats.depth, 5)
                self.assertEqual(stats.pending_bytes,
                                 sum(len(value.encode()) for value in latest.values()))
                self.assertLessEqual(stats.pending_bytes, 2048)
        self.assertEqual(queue.stats().depth, 5)
        ws.release.set()
        await self.wait_until(lambda: monitor.snapshot(queue, ws).sent_messages == 6)
        self.assertEqual(ws.messages, [history, *latest.values()])
        delivered = [json.loads(payload) for payload in ws.messages[1:]]
        self.assertEqual([item["segment"] for item in delivered if item["final"]], [1, 2, 3])
        self.assertTrue(all(item["revision"] == 100 for item in delivered))
        ws.closed.set()
        await asyncio.wait_for(task, 5)

    async def test_disconnect_waves_release_blocked_senders_without_affecting_healthy_client(self):
        healthy = ControlledConnection()
        healthy_queue, healthy_monitor, healthy_task = self.start_client(healthy)
        await self.wait_until(lambda: healthy in self.clients)
        for wave in range(3):
            blocked = []
            for _ in range(10):
                ws = ControlledConnection(blocked=True)
                queue, monitor, task = self.start_client(ws)
                queue.push("in flight")
                blocked.append((ws, queue, monitor, task))
            await self.wait_until(lambda: all(
                monitor.snapshot(queue, ws).in_flight
                for ws, queue, monitor, _ in blocked
            ))
            for ws, queue, _, _ in blocked:
                queue.push("pending")
                ws.closed.set()
            await asyncio.wait_for(asyncio.gather(*(entry[3] for entry in blocked)), 5)
            for ws, queue, monitor, _ in blocked:
                self.assertTrue(queue.stats().closed)
                self.assertEqual(queue.stats().depth, 0)
                self.assertEqual(queue.stats().pending_bytes, 0)
                self.assertFalse(monitor.snapshot(queue, ws).in_flight)
                self.assertEqual(ws.messages, [])
            self.assertEqual(set(self.clients), {healthy})
            self.assertFalse([
                task for task in asyncio.all_tasks()
                if task.get_name().startswith("client-")
                and task.get_name() not in ("client-sender", "client-disconnect")
            ])
            # Only the persistent healthy connection should still own these tasks.
            owned = [task for task in asyncio.all_tasks()
                     if task.get_name().startswith("client-")]
            self.assertEqual(len(owned), 2)
            healthy_queue.push(f"wave {wave}")
            await self.wait_until(lambda: healthy_monitor.snapshot(
                healthy_queue, healthy,
            ).sent_messages == wave + 1)
        self.assertEqual(healthy.messages, ["wave 0", "wave 1", "wave 2"])
        healthy.closed.set()
        await asyncio.wait_for(healthy_task, 5)


if __name__ == "__main__":
    unittest.main()
