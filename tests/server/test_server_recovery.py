"""Recovery initialization through real loopback WebSockets."""

import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import json
import time
import unittest
from unittest.mock import patch

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from captions.history import History, history_message
from captions.state import Event
import solution_server as app


class ServerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.reset_caption_state()
        self.stack = AsyncExitStack()
        self.server = await serve(app.handler, "127.0.0.1", 0, close_timeout=0.5)
        self.uri = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)
        await self.stack.aclose()
        self.assertEqual(app.CLIENTS, {})
        self.assertEqual(app.MONITORS, {})
        self.assertEqual(app.EVICTIONS, {})
        self.assertFalse([
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_name().startswith("client-")
        ])

    def caption(self, segment, revision=1, final=True, text="test"):
        return {"type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
                "revision": revision, "final": final, "source_ts": time.time(), "text": text}

    def seed(self, segment, revision=1, final=True, text="test"):
        msg = self.caption(segment, revision, final, text)
        raw = json.dumps(msg, ensure_ascii=False)
        event = app.STATE.apply(msg, raw)
        self.assertIsNotNone(event)
        if event.final:
            app.HISTORY.add(event)
        return msg, raw

    async def student(self, **overrides):
        hello = {"type": "CLIENT_HELLO", "client_id": 1, "session": "L1",
                 "last_segment": 0, "last_revision": 0, "history_seconds": 60}
        hello.update(overrides)
        ws = await self.stack.enter_async_context(connect(self.uri, close_timeout=0.5))
        await ws.send(json.dumps(hello))
        return ws

    async def receive(self, ws):
        raw = await asyncio.wait_for(ws.recv(), 3)
        return json.loads(raw)

    async def wait_until(self, predicate):
        async def poll():
            while not predicate():
                await asyncio.sleep(0)
        await asyncio.wait_for(poll(), 3)

    async def assert_rejected_without_context(self, ws, code=1013):
        with self.assertRaises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 3)
        self.assertEqual(ws.close_code, code)
        self.assertEqual(app.CLIENTS, {})
        self.assertEqual(app.MONITORS, {})

    async def test_history_then_latest_active_then_live_final(self):
        first, _ = self.seed(1)
        self.seed(2, revision=1, final=False)
        active, _ = self.seed(2, revision=3, final=False)
        ws = await self.student()
        self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": [first]})
        self.assertEqual(await self.receive(ws), active)

        source = await self.stack.enter_async_context(connect(self.uri))
        await source.send('{"type":"SOURCE_HELLO","session":"L1"}')
        final = self.caption(2, revision=4)
        await source.send(json.dumps(final))
        self.assertEqual(await self.receive(ws), final)

    async def test_resume_excludes_known_finals_but_includes_active(self):
        self.seed(1, revision=3)
        second, _ = self.seed(2, revision=2)
        active, _ = self.seed(3, revision=4, final=False)
        ws = await self.student(last_segment=1, last_revision=3)
        self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": [second]})
        self.assertEqual(await self.receive(ws), active)

    async def test_zero_history_still_sends_active_state(self):
        self.seed(1)
        active, _ = self.seed(2, final=False)
        ws = await self.student(history_seconds=0)
        self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": []})
        self.assertEqual(await self.receive(ws), active)

    async def test_history_fits_default_client_limit_with_larger_queue(self):
        # Count UTF-8 bytes, not Python characters or compressed wire bytes.
        for segment in (1, 2, 3):
            self.seed(segment, text="\u4f60" * 140000)
        with patch.object(app, "QUEUE_MAX_BYTES", 2 * 1024 * 1024):
            ws = await self.student()
            raw = await asyncio.wait_for(ws.recv(), 3)
        self.assertLessEqual(len(raw.encode("utf-8")), 2**20)
        history = json.loads(raw)
        self.assertEqual([item["segment"] for item in history["items"]], [2, 3])
        self.assertTrue(all(item["final"] for item in history["items"]))

    async def test_initial_count_overflow_sends_no_partial_history(self):
        self.seed(1)
        self.seed(2, final=False)
        with patch.object(app, "QUEUE_MAX_MESSAGES", 1):
            ws = await self.student()
            await self.assert_rejected_without_context(ws)

    async def test_initial_total_bytes_overflow_sends_no_partial_history(self):
        _, final_raw = self.seed(1, text="x" * 100)
        _, active_raw = self.seed(2, final=False, text="y" * 100)
        limit = max(len(history_message([final_raw]).encode()), len(active_raw.encode()))
        with patch.object(app, "QUEUE_MAX_BYTES", limit):
            ws = await self.student()
            await self.assert_rejected_without_context(ws)

    async def test_active_payload_must_also_fit_client_message_limit(self):
        _, raw = self.seed(1, final=False, text="x" * 100)
        with patch.object(app, "CLIENT_MAX_MESSAGE_BYTES", len(raw.encode()) - 1):
            ws = await self.student()
            await self.assert_rejected_without_context(ws)

    async def test_tiny_queue_rejects_even_empty_history(self):
        with patch.object(app, "QUEUE_MAX_BYTES", 1):
            ws = await self.student()
            await self.assert_rejected_without_context(ws)

    async def test_history_window_is_capped_at_sixty_seconds(self):
        history = History(clock=lambda: 1000.0)
        old = self.caption(1)
        recent = self.caption(2)
        history.add(Event(1, 1, True, json.dumps(old), 930.0))
        history.add(Event(2, 1, True, json.dumps(recent), 970.0))
        with patch.object(app, "HISTORY", history):
            for seconds in (120, 10**400):
                with self.subTest(seconds=seconds):
                    ws = await self.student(history_seconds=seconds)
                    self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": [recent]})

    async def test_invalid_recovery_fields_close_with_policy_error(self):
        for fields in (
            {"last_segment": -1}, {"last_segment": "1"}, {"last_segment": True},
            {"last_revision": None}, {"last_revision": 1.5},
            {"history_seconds": -1}, {"history_seconds": "30"},
            {"history_seconds": True}, {"history_seconds": float("nan")},
            {"history_seconds": float("inf")},
        ):
            with self.subTest(fields=fields):
                ws = await self.student(**fields)
                await self.assert_rejected_without_context(ws, code=1008)

    async def test_single_message_queue_can_send_history_without_active(self):
        first, _ = self.seed(1)
        with patch.object(app, "QUEUE_MAX_MESSAGES", 1):
            ws = await self.student()
            self.assertEqual(await self.receive(ws), {"type": "HISTORY", "items": [first]})

    async def test_join_during_source_burst_has_no_missing_or_duplicate_finals(self):
        self.seed(1)
        source = await self.stack.enter_async_context(connect(self.uri))
        await source.send('{"type":"SOURCE_HELLO","session":"L1"}')

        async def publish():
            for segment in range(2, 21):
                await source.send(json.dumps(self.caption(segment)))
                await asyncio.sleep(0)

        task = asyncio.create_task(publish())
        try:
            ws = await self.student()
            history = await self.receive(ws)
            self.assertEqual(history["type"], "HISTORY")
            finals = [item["segment"] for item in history["items"]]
            while len(finals) < 20:
                msg = await self.receive(ws)
                if msg["final"]:
                    finals.append(msg["segment"])
            await task
            self.assertEqual(Counter(finals), Counter(range(1, 21)))
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_ten_seconds_offline_recovers_missing_finals_then_latest_active(self):
        # Advance the history clock, not wall time: no ten-second sleep needed.
        now = [1000.0]
        history = History(clock=lambda: now[0])
        with patch.object(app, "HISTORY", history), patch(
            "captions.state.monotonic", side_effect=lambda: now[0],
        ):
            first, _ = self.seed(1, revision=3)
            old_ws = await self.student()
            self.assertEqual(await self.receive(old_ws), {"type": "HISTORY", "items": [first]})
            await old_ws.close()
            await self.wait_until(lambda: not app.MONITORS)
            now[0] += 5
            second, _ = self.seed(2, revision=3)
            now[0] += 5
            third, _ = self.seed(3, revision=2)
            self.seed(4, revision=1, final=False)
            active, _ = self.seed(4, revision=4, final=False)
            resumed = await self.student(last_segment=1, last_revision=3)
            self.assertEqual(await self.receive(resumed), {
                "type": "HISTORY", "items": [second, third],
            })
            self.assertEqual(await self.receive(resumed), active)
            source = await self.stack.enter_async_context(connect(self.uri))
            await source.send('{"type":"SOURCE_HELLO","session":"L1"}')
            # Replayed/stale updates must not appear before the new final.
            for message in (second, self.caption(4, revision=2, final=False)):
                await source.send(json.dumps(message))
            final = self.caption(4, revision=5)
            await source.send(json.dumps(final))
            self.assertEqual(await self.receive(resumed), final)

    async def test_evicted_client_recovers_finals_while_healthy_client_continues(self):
        original_session = app.run_client_session
        blocked = asyncio.Event()
        release = asyncio.Event()
        chosen = []

        async def gated_session(ws, queue, clients, **kwargs):
            if not chosen:
                chosen.append(ws)
                original_send = ws.send

                async def gated_send(payload):
                    blocked.set()
                    await release.wait()
                    await original_send(payload)

                ws.send = gated_send
            await original_session(ws, queue, clients, **kwargs)

        try:
            with patch.object(app, "run_client_session", gated_session):
                with patch.object(app, "QUEUE_MAX_MESSAGES", 1):
                    slow = await self.student()
                    await asyncio.wait_for(blocked.wait(), 3)
                healthy = await self.student(client_id=2)
                self.assertEqual(await self.receive(healthy), {"type": "HISTORY", "items": []})
                source = await self.stack.enter_async_context(connect(self.uri))
                await source.send('{"type":"SOURCE_HELLO","session":"L1"}')
                finals = [self.caption(1, revision=3), self.caption(2, revision=3)]
                for message in finals:
                    await source.send(json.dumps(message))
                    self.assertEqual(await self.receive(healthy), message)
                await asyncio.wait_for(slow.wait_closed(), 3)
                self.assertEqual(slow.close_code, 1013)
                await self.wait_until(lambda: len(app.MONITORS) == 1 and not app.EVICTIONS)
                resumed = await self.student(client_id=1)
                self.assertEqual(await self.receive(resumed), {"type": "HISTORY", "items": finals})
                live = self.caption(3, revision=3)
                await source.send(json.dumps(live))
                self.assertEqual(await self.receive(healthy), live)
                self.assertEqual(await self.receive(resumed), live)
        finally:
            release.set()


class KeepaliveDropTests(unittest.IsolatedAsyncioTestCase):
    """A client that stops reading is dropped by the tightened keepalive."""

    async def asyncSetUp(self):
        app.reset_caption_state()
        self.stack = AsyncExitStack()
        settings = patch.multiple(app, KEEPALIVE_INTERVAL=0.1, KEEPALIVE_TIMEOUT=0.3,
                                  CLIENT_CLOSE_TIMEOUT=0.2)
        settings.start()
        self.addCleanup(settings.stop)
        # Same serve() options as the real server, just faster timings.
        self.server = await serve(app.handler, "127.0.0.1", 0, **app.server_options())
        self.uri = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        await self.stack.aclose()
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)
        await self.wait_until(lambda: not app.CLIENTS and not app.MONITORS)
        self.assertEqual(app.EVICTIONS, {})

    async def wait_until(self, predicate, timeout=5):
        async def poll():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(poll(), timeout)

    def caption(self, segment, revision, final):
        return {"type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
                "revision": revision, "final": final, "source_ts": time.time(),
                "text": f"s{segment} r{revision} " + "x" * 300}

    async def student(self, client_id, last_segment=0, last_revision=0, **kwargs):
        ws = await self.stack.enter_async_context(
            connect(self.uri, close_timeout=0.2, **kwargs))
        await ws.send(json.dumps({
            "type": "CLIENT_HELLO", "client_id": client_id, "session": "L1",
            "last_segment": last_segment, "last_revision": last_revision,
            "history_seconds": 30,
        }))
        return ws

    async def test_stuck_reader_dropped_healthy_reader_unaffected_and_resume_recovers(self):
        received = []

        async def read_all(ws):
            async for raw in ws:
                received.append(json.loads(raw))

        healthy = await self.student(1)
        reader = asyncio.create_task(read_all(healthy))
        await self.wait_until(lambda: len(app.CLIENTS) == 1)
        healthy_server_ws = next(iter(app.CLIENTS))
        # Never reads: its library stops reading after 2 frames, so the
        # server's keepalive pings wait unanswered behind the backlog.
        await self.student(7, max_queue=2)
        await self.wait_until(lambda: len(app.CLIENTS) == 2)
        stuck_server_ws = next(ws for ws in app.CLIENTS if ws is not healthy_server_ws)

        source = await self.stack.enter_async_context(connect(self.uri))
        await source.send('{"type":"SOURCE_HELLO","session":"L1"}')
        sent = []
        segment = 0
        while stuck_server_ws in app.CLIENTS or segment < 5:
            segment += 1
            self.assertLess(segment, 200, "stuck client was never dropped")
            for revision, final in ((1, False), (2, False), (3, True)):
                sent.append((segment, revision))
                await source.send(json.dumps(self.caption(segment, revision, final)))
                await asyncio.sleep(0.01)

        self.assertTrue(app.dropped_for_lag(stuck_server_ws))
        self.assertIn(healthy_server_ws, app.CLIENTS)
        # Normal Update Coverage: the healthy reader still got every revision.
        await self.wait_until(lambda: len(received) == len(sent) + 1)
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        self.assertEqual(received[0]["type"], "HISTORY")
        self.assertEqual([(m["segment"], m["revision"]) for m in received[1:]], sent)

        # The dropped client resumes from the last final it processed and
        # receives every later final in one HISTORY message.
        resumed = await self.student(7, last_segment=1, last_revision=3)
        history = json.loads(await asyncio.wait_for(resumed.recv(), 3))
        self.assertEqual(history["type"], "HISTORY")
        self.assertEqual([(i["segment"], i["final"]) for i in history["items"]],
                         [(s, True) for s in range(2, segment + 1)])


if __name__ == "__main__":
    unittest.main()
