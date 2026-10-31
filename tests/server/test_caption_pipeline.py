"""Loopback test: source -> revision state/history -> client, through the real server."""

import asyncio
from contextlib import AsyncExitStack
import json
import unittest

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

import solution_server as app


def cap(segment, revision, final=False, **extra):
    msg = {
        "type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
        "revision": revision, "source_ts": 1760000000.25, "final": final,
        "text": f"s{segment} r{revision}",
    }
    msg.update(extra)
    return msg


class CaptionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.reset_caption_state()
        self.stack = AsyncExitStack()
        self.server = await serve(app.handler, "127.0.0.1", 0, close_timeout=0.5)
        port = self.server.sockets[0].getsockname()[1]
        self.uri = f"ws://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.stack.aclose()
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)

    async def connect(self, hello):
        ws = await self.stack.enter_async_context(connect(self.uri, close_timeout=0.5))
        await ws.send(json.dumps(hello))
        return ws

    async def student(self):
        ws = await self.connect({
            "type": "CLIENT_HELLO", "client_id": 1, "session": "L1",
            "last_segment": 0, "last_revision": 0, "history_seconds": 30,
        })
        self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(), 3))["type"], "HISTORY")
        return ws

    async def drain(self, ws, quiet=0.3):
        received = []
        while True:
            try:
                received.append(json.loads(await asyncio.wait_for(ws.recv(), quiet)))
            except asyncio.TimeoutError:
                return received

    async def test_only_new_information_reaches_clients(self):
        student = await self.student()
        source = await self.connect({"type": "SOURCE_HELLO", "session": "L1"})
        sent = [
            cap(1, 1), cap(1, 3), cap(1, 2),          # 2 is stale
            cap(1, 4, final=True), cap(1, 4, final=True),  # duplicate final
            cap(1, 5),                                 # after final
            {"type": "CAPTION_UPDATE", "segment": "x"},  # malformed
            "not json at all",
            cap(2, 1, phase="extra field"),            # unknown field ignored
            {"type": "SOURCE_DONE", "session": "L1"},
        ]
        for msg in sent:
            await source.send(msg if isinstance(msg, str) else json.dumps(msg))
        received = await self.drain(student)
        self.assertEqual(
            [(m["segment"], m["revision"], m["final"]) for m in received],
            [(1, 1, False), (1, 3, False), (1, 4, True), (2, 1, False)],
        )
        self.assertEqual(received[-1]["phase"], "extra field")  # forwarded unchanged
        stats = app.STATE.stats()
        self.assertEqual((stats["stale"], stats["duplicate"], stats["after_final"],
                          stats["malformed"]), (1, 1, 1, 1))
        self.assertEqual(len(app.HISTORY), 1)
        self.assertEqual([json.loads(r)["segment"] for r in app.HISTORY.since(0, 0)], [1])

    async def test_state_survives_source_reconnect(self):
        student = await self.student()
        first = await self.connect({"type": "SOURCE_HELLO", "session": "L1"})
        await first.send(json.dumps(cap(1, 2, final=True)))
        await first.close()
        second = await self.connect({"type": "SOURCE_HELLO", "session": "L1"})
        await second.send(json.dumps(cap(1, 2, final=True)))   # replayed final
        await second.send(json.dumps(cap(2, 1)))
        received = await self.drain(student)
        self.assertEqual([(m["segment"], m["revision"]) for m in received], [(1, 2), (2, 1)])


if __name__ == "__main__":
    unittest.main()
