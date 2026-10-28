#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall delivery integration.

Each student connection has a bounded queue, independent sender, lifecycle
supervision and monitor. Still incomplete; these mechanisms remain to integrate:
  - revision-state management or deduplication
  - recent-history recovery or late-join recovery
  - semantic coalescing / backpressure
  - constrained-bandwidth overload handling

Required evaluator command:
    python solution_server.py --port 8765

Teams should replace or substantially extend the implementation while keeping
this command-line interface and the protocol in the assignment specification.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from delivery.client_monitor import ClientMonitor, DeliveryStats
from delivery.client_queue import ClientQueue, PushResult
from delivery.client_session import run_client_session

# Provisional integration settings; tune alongside the semantic overload policy.
QUEUE_MAX_MESSAGES = 256
QUEUE_MAX_BYTES = 1024 * 1024
CLIENT_SEND_TIMEOUT = 10.0
CLIENT_CLOSE_TIMEOUT = 3.0

# Active connection -> outgoing queue.
CLIENTS: dict[Any, ClientQueue] = {}
# Connection -> delivery monitor.
MONITORS: dict[Any, ClientMonitor] = {}
# Connection -> overload-close task; prevent duplicates and let handlers await cleanup.
EVICTIONS: dict[Any, asyncio.Task[None]] = {}


async def close_overloaded_client(ws, queue: ClientQueue) -> None:
    """Temporary fail-fast fallback; it cannot guarantee recovery of finals."""
    try:
        # Already unregistered from client dict, so broadcasts no longer enqueue updates here.
        # Request 1013 first; closing the queue first could let the sender use 1000.
        await asyncio.wait_for(
            ws.close(code=1013, reason="Outgoing queue capacity exceeded"),
            timeout=CLIENT_CLOSE_TIMEOUT,
        )
    except (asyncio.TimeoutError, ConnectionClosed, OSError) as exc:
        print(f"overloaded client close failed: {exc!r}", flush=True)
    finally:
        # After the close attempt, discard queued data and reject further pushes.
        queue.close()


def broadcast_to_queues(raw_message: str | bytes) -> None:
    """Admit updates without awaiting any client's network I/O.

    Replacement point for the future semantic overload policy: it must handle
    rejected updates, especially finals, instead of silently ignoring FULL.
    """
    # Snapshot the registry because rejected clients are removed during iteration.
    for ws, queue in list(CLIENTS.items()):
        # Enqueue without waiting for network I/O; each sender handles delivery.
        result = queue.push(raw_message)
        if result is PushResult.ACCEPTED:
            continue
        # Stop admitting updates for this client.
        CLIENTS.pop(ws, None)
        print(f"client unregistered: queue admission={result.value}", flush=True)
        if ws not in EVICTIONS:
            # Close in a separate task so the source does not wait on this socket.
            EVICTIONS[ws] = asyncio.create_task(
                close_overloaded_client(ws, queue), name="client-overload-close"
            )


def get_client_stats(ws) -> DeliveryStats:
    """Snapshot an active client's queue/sender/transport for future policy use."""
    return MONITORS[ws].snapshot(CLIENTS[ws], ws)


async def handle_source(ws, hello: dict[str, Any]) -> None:
    session = hello.get("session")
    print(f"source connected: session={session}", flush=True)

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")
            if msg_type == "CAPTION_UPDATE":
                # Revision validation/history are still pending. Forward raw
                # updates unchanged, but never wait on client socket writes.
                broadcast_to_queues(raw)
                # Give other tasks a turn even when source frames are already buffered.
                await asyncio.sleep(0)
            elif msg_type == "SOURCE_DONE":
                # Test-control message. The specification does not require it
                # to be forwarded to clients.
                print("source finished", flush=True)
            else:
                # Forward compatibility: ignore fields/messages this baseline
                # doesn't understand rather than crashing.
                continue
    except ConnectionClosed:
        pass
    finally:
        print("source disconnected", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    # Queue HISTORY before live updates; it is still empty until recovery is implemented.
    queue = ClientQueue(max_messages=QUEUE_MAX_MESSAGES, max_bytes=QUEUE_MAX_BYTES)
    result = queue.push(json.dumps({"type": "HISTORY", "items": []}))
    if result is not PushResult.ACCEPTED:
        queue.close()
        await ws.close(code=1013, reason="Initial history exceeds queue capacity")
        return

    monitor = ClientMonitor()
    MONITORS[ws] = monitor
    print(f"client connected: id={client_id}", flush=True)
    try:
        # No await between HISTORY admission and registration: live updates cannot interleave.
        # This await returns only after the session finishes its own cleanup.
        await run_client_session(
            ws, queue, CLIENTS, monitor=monitor,
            send_timeout=CLIENT_SEND_TIMEOUT, close_timeout=CLIENT_CLOSE_TIMEOUT,
        )
    except (ConnectionClosed, asyncio.TimeoutError, OSError) as exc:
        print(f"client send ended: id={client_id}; reason={exc!r}", flush=True)
    finally:
        # The session handles CLIENTS, queue and sender; remove server-owned extras here.
        if MONITORS.get(ws) is monitor:
            del MONITORS[ws]
        close_task = EVICTIONS.pop(ws, None)
        if close_task is not None:
            # The close task was already scheduled; wait for it rather than starting it here.
            await close_task
        print(f"client disconnected: id={client_id}; clients={len(CLIENTS)}", flush=True)


async def handler(ws) -> None:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        hello = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError):
        await ws.close(code=1008, reason="Expected a valid hello message")
        return
    except ConnectionClosed:
        return

    if not isinstance(hello, dict):
        await ws.close(code=1008, reason="Expected a hello object")
        return

    msg_type = hello.get("type")
    # Both connection types use one port; the first hello selects the handler.
    if msg_type == "SOURCE_HELLO":
        await handle_source(ws, hello)
    elif msg_type == "CLIENT_HELLO":
        await handle_client(ws, hello)
    else:
        await ws.close(code=1008, reason="Expected SOURCE_HELLO or CLIENT_HELLO")


async def main_async(port: int) -> None:
    async with serve(
        handler,
        "0.0.0.0",
        port,
        max_size=2**20,
        ping_interval=20,
        ping_timeout=20,
        close_timeout=CLIENT_CLOSE_TIMEOUT,
    ):
        print(f"LiveRecall listening on ws://0.0.0.0:{port}", flush=True)
        # Keep listening until the server is cancelled or stopped.
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
