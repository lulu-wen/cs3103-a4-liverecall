"""Loopback WebSocket integration; not Ubuntu performance measurements."""

import asyncio
from contextlib import AsyncExitStack
import json
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import solution_server as app
from delivery.overload_policy import OverloadMode, OverloadPolicy, OverloadThresholds


class ServerDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Tests reuse segment numbers; start each from a fresh lecture state.
        app.reset_caption_state()
        self.assertFalse(app.CLIENTS)
        self.assertFalse(app.MONITORS)
        self.assertFalse(app.EVICTIONS)
        self.stack = AsyncExitStack()
        self.server = await serve(app.handler, "127.0.0.1", 0, close_timeout=0.5)
        port = self.server.sockets[0].getsockname()[1]
        self.uri = f"ws://127.0.0.1:{port}"

    async def asyncTearDown(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)
        await self.stack.aclose()
        self.assertFalse(app.CLIENTS)
        self.assertFalse(app.MONITORS)
        self.assertFalse(app.EVICTIONS)
        owned_tasks = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_name().startswith("client-")
        ]
        self.assertEqual(owned_tasks, [])

    async def wait_until(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(poll(), 3)

    async def student(self, client_id):
        ws = await self.stack.enter_async_context(connect(self.uri, close_timeout=0.5))
        await ws.send(json.dumps({
            "type": "CLIENT_HELLO", "client_id": client_id, "session": "L1",
            "last_segment": 0, "last_revision": 0, "history_seconds": 30,
        }))
        return ws

    async def source(self):
        ws = await self.stack.enter_async_context(connect(self.uri, close_timeout=0.5))
        await ws.send('{"type":"SOURCE_HELLO","session":"L1"}')
        return ws

    async def receive(self, ws):
        return json.loads(await asyncio.wait_for(ws.recv(), 3))

    def caption(self, revision=1):
        return {
            "type": "CAPTION_UPDATE", "session": "L1", "segment": 1,
            "revision": revision, "source_ts": 1760000000.0,
            "final": False, "text": f"revision {revision}",
        }

    async def test_history_then_ordered_live_updates_for_two_clients(self):
        a, b = await self.student(1), await self.student(2)
        for ws in (a, b):
            self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": []})
        source = await self.source()
        updates = [self.caption(index) for index in range(1, 21)]
        updates[-1]["final"] = True
        for update in updates:
            await source.send(json.dumps(update))
        for ws in (a, b):
            received = [await self.receive(ws) for _ in updates]
            self.assertEqual(received, updates)
        await self.wait_until(lambda: all(
            app.get_client_stats(ws).sent_messages == 21 for ws in app.CLIENTS
        ))
        self.assertEqual(len(app.MONITORS), 2)
        for ws in app.CLIENTS:
            stats = app.get_client_stats(ws)
            self.assertEqual(stats.queue.depth, 0)
            self.assertIsNotNone(stats.write_buffer_bytes)

    async def test_repeated_disconnect_cleans_registries(self):
        for number in range(5):
            ws = await self.student(number)
            await self.receive(ws)
            server_ws = next(iter(app.CLIENTS))
            queue = app.CLIENTS[server_ws]
            await ws.close()
            await self.wait_until(lambda: not app.MONITORS)
            self.assertFalse(app.CLIENTS)
            self.assertTrue(queue.stats().closed)
            self.assertEqual(queue.stats().pending_bytes, 0)

    async def test_shutdown_releases_idle_clients(self):
        ws = await self.student(1)
        await self.receive(ws)
        queue = next(iter(app.CLIENTS.values()))
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 3)
        await asyncio.wait_for(ws.wait_closed(), 3)
        self.assertFalse(app.CLIENTS)
        self.assertFalse(app.MONITORS)
        self.assertTrue(queue.stats().closed)

    async def test_non_object_hello_is_rejected(self):
        ws = await self.stack.enter_async_context(connect(self.uri))
        await ws.send("[]")
        await asyncio.wait_for(ws.wait_closed(), 3)
        self.assertEqual(ws.close_code, 1008)
        self.assertFalse(app.CLIENTS)

    async def test_oversized_payload_closes_only_small_queue_client(self):
        with patch.object(app, "QUEUE_MAX_BYTES", 80):
            small = await self.student(1)
            await self.receive(small)
        normal = await self.student(2)
        await self.receive(normal)
        source = await self.source()
        update = self.caption()
        self.assertGreater(len(json.dumps(update).encode("utf-8")), 80)
        await source.send(json.dumps(update))
        self.assertEqual(await self.receive(normal), update)
        await asyncio.wait_for(small.wait_closed(), 3)
        self.assertEqual(small.close_code, 1013)
        await self.wait_until(lambda: len(app.MONITORS) == 1 and not app.EVICTIONS)

    async def test_source_disconnect_does_not_unregister_students(self):
        student = await self.student(1)
        await self.receive(student)
        source = await self.source()
        await source.send(json.dumps(self.caption(1)))
        self.assertEqual(await self.receive(student), self.caption(1))
        await source.close()
        self.assertEqual(len(app.CLIENTS), 1)
        replacement = await self.source()
        await replacement.send(json.dumps(self.caption(2)))
        self.assertEqual(await self.receive(student), self.caption(2))

    async def test_queue_full_closes_only_blocked_client_with_1013(self):
        # Gate one server-side send deterministically. Sockets/handshakes are
        # real, but this models a blocked send, not a real TCP slow-reader test.
        original_session = app.run_client_session
        blocked = asyncio.Event()
        release = asyncio.Event()
        chosen = []

        async def controlled_session(ws, queue, clients, **kwargs):
            if not chosen:
                chosen.append(ws)
                original_send = ws.send

                async def gated_send(payload):
                    if json.loads(payload).get("type") == "CAPTION_UPDATE":
                        blocked.set()
                        await release.wait()
                    await original_send(payload)

                with patch.object(ws, "send", gated_send):
                    await original_session(ws, queue, clients, **kwargs)
            else:
                await original_session(ws, queue, clients, **kwargs)

        with patch.object(app, "run_client_session", controlled_session), \
                patch.object(app, "QUEUE_MAX_MESSAGES", 2):
            slow = await self.student(1)
            await self.receive(slow)
            normal = await self.student(2)
            await self.receive(normal)
            source = await self.source()
            await source.send(json.dumps(self.caption(1)))
            await asyncio.wait_for(blocked.wait(), 3)
            self.assertEqual(await self.receive(normal), self.caption(1))
            for revision in (2, 3, 4):
                update = self.caption(revision)
                update.update(segment=revision, final=True)
                await source.send(json.dumps(update))
                self.assertEqual(await self.receive(normal), update)
            await asyncio.wait_for(slow.wait_closed(), 3)
            self.assertEqual(slow.close_code, 1013)
            await self.wait_until(lambda: len(app.MONITORS) == 1 and not app.EVICTIONS)
            self.assertEqual(len(app.CLIENTS), 1)
            await source.send(json.dumps(self.caption(5)))
            self.assertEqual(await self.receive(normal), self.caption(5))

    async def check_full_queue_coalesces(self, *, byte_limited):
        original_session = app.run_client_session
        blocked = asyncio.Event()
        release = asyncio.Event()
        chosen = []

        async def controlled_session(ws, queue, clients, **kwargs):
            if not chosen:
                chosen.append(ws)
                original_send = ws.send

                async def gated_send(payload):
                    if json.loads(payload).get("type") == "CAPTION_UPDATE":
                        blocked.set()
                        await release.wait()
                    await original_send(payload)

                with patch.object(ws, "send", gated_send):
                    await original_session(ws, queue, clients, **kwargs)
            else:
                await original_session(ws, queue, clients, **kwargs)

        count = 10 if byte_limited else 2
        byte_limit = len(json.dumps(self.caption()).encode()) * 3 // 2 if byte_limited else 4096
        try:
            with patch.object(app, "run_client_session", controlled_session):
                with patch.object(app, "QUEUE_MAX_MESSAGES", count), \
                        patch.object(app, "QUEUE_MAX_BYTES", byte_limit):
                    slow = await self.student(1)
                    await self.receive(slow)
                normal = await self.student(2)
                await self.receive(normal)
                source = await self.source()
                first = self.caption(1)
                await source.send(json.dumps(first))
                await asyncio.wait_for(blocked.wait(), 3)
                self.assertEqual(await self.receive(normal), first)
                for revision in (2, 3, 4):
                    update = self.caption(revision)
                    update["final"] = revision == 4
                    await source.send(json.dumps(update))
                    self.assertEqual(await self.receive(normal), update)
                queue = app.CLIENTS[chosen[0]]
                self.assertEqual(queue.stats().depth, 1)
                self.assertEqual(queue.stats().pending_bytes, len(json.dumps(update).encode()))
                self.assertEqual(len(app.CLIENTS), 2)
                self.assertFalse(app.EVICTIONS)
                release.set()
                self.assertEqual(await self.receive(slow), first)
                self.assertEqual(await self.receive(slow), update)
                await self.wait_until(lambda: app.get_client_stats(chosen[0]).sent_messages == 3)
                self.assertEqual(queue.stats().depth, 0)
                self.assertEqual(queue.stats().pending_bytes, 0)
        finally:
            release.set()

    async def test_count_full_coalesces_obsolete_revisions_before_eviction(self):
        await self.check_full_queue_coalesces(byte_limited=False)

    async def test_degraded_interleaved_segments_retain_each_latest_update(self):
        original_session = app.run_client_session
        blocked, release = asyncio.Event(), asyncio.Event()
        chosen = []

        async def gated_session(ws, queue, clients, **kwargs):
            if chosen:
                return await original_session(ws, queue, clients, **kwargs)
            chosen.append(ws)
            original_send = ws.send

            async def gated_send(payload):
                if json.loads(payload).get("type") == "CAPTION_UPDATE":
                    blocked.set()
                    await release.wait()
                await original_send(payload)

            with patch.object(ws, "send", gated_send):
                await original_session(ws, queue, clients, **kwargs)

        try:
            with patch.object(app, "run_client_session", gated_session), \
                    patch.object(app, "NAIVE_DELIVERY", False):
                slow = await self.student(1)
                await self.receive(slow)
                normal = await self.student(2)
                await self.receive(normal)
                app.OVERLOAD_POLICIES[chosen[0]] = OverloadPolicy(
                    max_messages=app.QUEUE_MAX_MESSAGES, max_bytes=app.QUEUE_MAX_BYTES,
                    thresholds=OverloadThresholds(enter_queue_ratio=0),
                )
                source = await self.source()
                first = self.caption(1)
                await source.send(json.dumps(first))
                await asyncio.wait_for(blocked.wait(), 3)
                self.assertEqual(await self.receive(normal), first)
                updates = [self.caption(2), self.caption(1), self.caption(3), self.caption(1)]
                for update, segment in zip(updates, (1, 2, 1, 3)):
                    update["segment"] = segment
                    await source.send(json.dumps(update))
                    self.assertEqual(await self.receive(normal), update)
                queue = app.CLIENTS[chosen[0]]
                self.assertIs(app.OVERLOAD_POLICIES[chosen[0]].mode, OverloadMode.DEGRADED)
                self.assertEqual(queue.stats().depth, 3)
                self.assertFalse(app.EVICTIONS)
                release.set()
                self.assertEqual(await self.receive(slow), first)
                # Replacing a map entry keeps that segment's existing service turn.
                self.assertEqual([await self.receive(slow) for _ in range(3)],
                                 [updates[2], updates[1], updates[3]])
        finally:
            release.set()

    async def test_new_provisional_cannot_evict_queued_finals_on_full_fallback(self):
        original_session = app.run_client_session
        blocked, release = asyncio.Event(), asyncio.Event()
        chosen = []

        async def gated_session(ws, queue, clients, **kwargs):
            if chosen:
                return await original_session(ws, queue, clients, **kwargs)
            chosen.append(ws)
            original_send = ws.send

            async def send(raw):
                if json.loads(raw).get("type") == "CAPTION_UPDATE":
                    blocked.set()
                    await release.wait()
                await original_send(raw)

            with patch.object(ws, "send", send):
                await original_session(ws, queue, clients, **kwargs)

        try:
            with patch.object(app, "run_client_session", gated_session), \
                    patch.object(app, "NAIVE_DELIVERY", True):
                with patch.object(app, "QUEUE_MAX_MESSAGES", 2):
                    slow = await self.student(1)
                    await self.receive(slow)
                normal = await self.student(2)
                await self.receive(normal)
                source = await self.source()
                updates = []
                for segment in range(1, 5):
                    message = self.caption()
                    message.update(segment=segment, final=segment < 4)
                    updates.append(message)
                    await source.send(json.dumps(message))
                    self.assertEqual(await self.receive(normal), message)
                    if segment == 1:
                        await asyncio.wait_for(blocked.wait(), 3)
                stats = app.CLIENTS[chosen[0]].stats()
                self.assertEqual((stats.final_depth, stats.active_depth), (2, 1))
                self.assertFalse(app.EVICTIONS)
                self.assertEqual(len(app.CLIENTS), 2)
                release.set()
                self.assertEqual([await self.receive(slow) for _ in range(4)], updates)
        finally:
            release.set()

    async def test_byte_full_coalesces_obsolete_revisions_before_eviction(self):
        await self.check_full_queue_coalesces(byte_limited=True)


if __name__ == "__main__":
    unittest.main()
