#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall delivery integration.

Each student connection has a bounded queue, independent sender, lifecycle
supervision and monitor. Source updates pass through revision state
(captions.state) and finals are kept in bounded history (captions.history).
Still incomplete; these mechanisms remain to integrate:
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
import math
import os
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from captions.history import History, history_message
from captions.state import CaptionState, Event
from delivery.client_monitor import ClientMonitor, DeliveryStats
from delivery.client_queue import ClientQueue, PushResult
from delivery.client_session import run_client_session
from delivery.overload_policy import OverloadMode, OverloadPolicy

# Provisional integration settings; tune alongside the semantic overload policy.
QUEUE_MAX_MESSAGES = 256
QUEUE_MAX_BYTES = 1024 * 1024
CLIENT_SEND_TIMEOUT = 10.0
CLIENT_CLOSE_TIMEOUT = 3.0
# Public load clients accept WebSocket messages up to 1 MiB after decompression.
CLIENT_MAX_MESSAGE_BYTES = 2**20
MAX_RECOVERY_SECONDS = 60.0
# P4: optimized semantic overload handling is enabled by default.
NAIVE_DELIVERY = False
# Experiment-only artificial delay before each client send; default grading path is zero.
EXPERIMENT_SEND_DELAY = 0.0

# Built-in WebSocket keepalive, tightened to drop clients that fall behind.
# A slow reader's backlog sits in its own buffers, where our queue and send
# times cannot see it. A pong, however, can only come back after the client
# has read every frame sent before the ping, so a client more than about
# KEEPALIVE_TIMEOUT seconds behind is closed by the library (code 1011,
# "keepalive ping timeout"). It reconnects with its resume cursor and gets the
# finals it missed in one HISTORY message. These pings replace the library's
# default 20 s keepalive rather than adding traffic.
KEEPALIVE_INTERVAL = 2.0
KEEPALIVE_TIMEOUT = 5.0

# Recovery history bounds (P1). The spec requires >= 60 s; the margin covers
# a client evicted for overload plus its reconnect time.
HISTORY_WINDOW_SECONDS = 90.0
HISTORY_MAX_ITEMS = 5000

# Authoritative revision state and bounded finalized-caption history (P1).
# Module-level so they survive a source reconnect (a new SOURCE_HELLO).
STATE = CaptionState()
HISTORY = History(window_seconds=HISTORY_WINDOW_SECONDS, max_items=HISTORY_MAX_ITEMS)


def reset_caption_state() -> None:
    """Start a fresh lecture state (used by tests; a new process starts fresh anyway)."""
    global STATE, HISTORY
    STATE = CaptionState()
    HISTORY = History(window_seconds=HISTORY_WINDOW_SECONDS, max_items=HISTORY_MAX_ITEMS)


# Active connection -> outgoing queue.
CLIENTS: dict[Any, ClientQueue] = {}
# Connection -> delivery monitor.
MONITORS: dict[Any, ClientMonitor] = {}
# Connection -> P4 per-client overload policy.
OVERLOAD_POLICIES: dict[Any, OverloadPolicy] = {}
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


def broadcast_to_queues(event: Event) -> None:
    """Admit updates without awaiting any client's network I/O.

    Preserve all revisions while capacity permits. Retry FULL once with
    same-segment coalescing before falling back to eviction. Lag detection and
    degraded-mode transitions remain the overload policy's responsibility.
    """
    # Snapshot the registry because rejected clients are removed during iteration.
    for ws, queue in list(CLIENTS.items()):
        # P4 chooses whether P3's existing coalescing mechanism should be active.
        # In naive mode, preserve the pre-P4 behavior and only coalesce after FULL.
        coalesce = False
        policy = OVERLOAD_POLICIES.get(ws)
        if not NAIVE_DELIVERY and policy is not None:
            monitor = MONITORS.get(ws)
            if monitor is not None:
                mode = policy.update(monitor.snapshot(queue, ws))
                coalesce = mode is OverloadMode.DEGRADED

        # Enqueue without waiting for network I/O; each sender handles delivery.
        result = queue.push_caption(
            event.raw, segment=event.segment, revision=event.revision, final=event.final,
            coalesce=coalesce, drop_older_segments=coalesce,
        )
        if result is PushResult.FULL:
            # The failed push leaves the backlog intact. Retry atomically,
            # removing only older queued provisionals replaced by this event.
            result = queue.push_caption(
                event.raw, segment=event.segment, revision=event.revision,
                final=event.final, coalesce=True, drop_older_segments=True,
            )
        if result is PushResult.ACCEPTED:
            continue
        # Stop admitting updates for this client.
        CLIENTS.pop(ws, None)
        OVERLOAD_POLICIES.pop(ws, None)
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
                # Only new information is forwarded: stale, duplicate,
                # after-final and malformed updates are dropped and counted.
                event = STATE.apply(msg, raw)
                if event is not None:
                    if event.final:
                        # Store before broadcasting; no await in between, so a
                        # client registering now sees it in HISTORY or live.
                        HISTORY.add(event)
                    # The source's original string, shared by every client
                    # queue; never wait on client socket writes.
                    broadcast_to_queues(event)
                # Give other tasks a turn even when source frames are already buffered.
                await asyncio.sleep(0)
            elif msg_type == "SOURCE_DONE":
                # Test-control message. The specification does not require it
                # to be forwarded to clients.
                print(f"source finished; caption state: {STATE.stats()}", flush=True)
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
    client_last_segment = hello.get("last_segment", 0)
    client_last_revision = hello.get("last_revision", 0)
    client_history_seconds = hello.get("history_seconds", 30)
    # Validate before querying history; bool is not a valid integer cursor.
    valid_cursor = all(
        type(value) is int and value >= 0
        for value in (client_last_segment, client_last_revision)
    )
    valid_window = (
        type(client_history_seconds) in (int, float)
        and client_history_seconds >= 0
        and (type(client_history_seconds) is int or math.isfinite(client_history_seconds))
    )
    if not valid_cursor or not valid_window:
        await ws.close(code=1008, reason="Invalid recovery cursor or history window")
        return
    client_history_seconds = min(client_history_seconds, MAX_RECOVERY_SECONDS)

    # Prepare one FIFO: finalized context first, then current provisional state.
    # No await until registration, so source updates cannot slip into this gap.
    queue = ClientQueue(max_messages=QUEUE_MAX_MESSAGES, max_bytes=QUEUE_MAX_BYTES)
    items = HISTORY.since(
        last_segment=client_last_segment,
        last_revision=client_last_revision,
        seconds=client_history_seconds,
        max_bytes=min(QUEUE_MAX_BYTES, CLIENT_MAX_MESSAGE_BYTES),
    )
    initial_messages = [history_message(items), *STATE.active_snapshot()]
    for message in initial_messages:
        if len(message.encode("utf-8")) > CLIENT_MAX_MESSAGE_BYTES:
            result = PushResult.TOO_LARGE
        else:
            result = queue.push(message)
        if result is not PushResult.ACCEPTED:
            break
    if result is not PushResult.ACCEPTED:
        # No sender has started: discard the whole batch, never send a partial
        # initial context or register a client with a missing active snapshot.
        queue.close()
        await ws.close(code=1013, reason="Initial context exceeds delivery limits")
        return

    monitor = ClientMonitor()
    MONITORS[ws] = monitor
    OVERLOAD_POLICIES[ws] = OverloadPolicy(
        max_messages=QUEUE_MAX_MESSAGES, max_bytes=QUEUE_MAX_BYTES
    )
    print(f"client connected: id={client_id}", flush=True)
    try:
        # No await between HISTORY admission and registration: live updates cannot interleave.
        # This await returns only after the session finishes its own cleanup.
        await run_client_session(
            ws, queue, CLIENTS, monitor=monitor,
            send_timeout=CLIENT_SEND_TIMEOUT, close_timeout=CLIENT_CLOSE_TIMEOUT,
            experiment_send_delay=EXPERIMENT_SEND_DELAY,
        )
    except (ConnectionClosed, asyncio.TimeoutError, OSError) as exc:
        print(f"client send ended: id={client_id}; reason={exc!r}", flush=True)
    finally:
        if dropped_for_lag(ws):
            print(f"client dropped (fell behind, keepalive timeout): id={client_id}", flush=True)
        # The session handles CLIENTS, queue and sender; remove server-owned extras here.
        if MONITORS.get(ws) is monitor:
            del MONITORS[ws]
        OVERLOAD_POLICIES.pop(ws, None)
        close_task = EVICTIONS.pop(ws, None)
        if close_task is not None:
            # The close task was already scheduled; wait for it rather than starting it here.
            await close_task
        print(f"client disconnected: id={client_id}; clients={len(CLIENTS)}", flush=True)


def dropped_for_lag(ws) -> bool:
    """True if the library closed this connection because its pong timed out."""
    sent = getattr(getattr(ws, "protocol", None), "close_sent", None)
    return (sent is not None and sent.code == 1011
            and "keepalive" in (sent.reason or ""))


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


def server_options() -> dict[str, Any]:
    """Keyword arguments for websockets.serve(), shared by main_async and tests."""
    return {
        "max_size": 2**20,
        "ping_interval": KEEPALIVE_INTERVAL,
        "ping_timeout": KEEPALIVE_TIMEOUT,
        "close_timeout": CLIENT_CLOSE_TIMEOUT,
    }


async def main_async(port: int) -> None:
    async with serve(handler, "0.0.0.0", port, **server_options()):
        print(f"LiveRecall listening on ws://0.0.0.0:{port}", flush=True)
        print(
            f"Delivery settings: queue_max_messages={QUEUE_MAX_MESSAGES}; "
            f"queue_max_bytes={QUEUE_MAX_BYTES}; "
            f"send_timeout={CLIENT_SEND_TIMEOUT}s; close_timeout={CLIENT_CLOSE_TIMEOUT}s; "
            f"keepalive={KEEPALIVE_INTERVAL}s/{KEEPALIVE_TIMEOUT}s; "
            f"overload_policy={'naive' if NAIVE_DELIVERY else 'semantic'}; "
            f"experiment_send_delay_ms={EXPERIMENT_SEND_DELAY * 1000:.1f}",
            flush=True,
        )
        # Keep listening until the server is cancelled or stopped.
        await asyncio.Future()


def positive_int(value: str) -> int:
    """Reject non-positive capacities before starting the server."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_seconds(value: str) -> float:
    """Reject zero, negative and non-finite delivery deadlines."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be positive finite seconds") from None
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be positive finite seconds")
    return number



def nonnegative_milliseconds(value: str) -> float:
    """Parse an experiment delay in milliseconds and return seconds."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be non-negative finite milliseconds") from None
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be non-negative finite milliseconds")
    return number / 1000.0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LiveRecall WebSocket server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--naive", action="store_true",
        help="Disable proactive P4 overload mode; retain queue-full fallback only",
    )
    parser.add_argument(
        "--experiment-send-delay-ms", type=nonnegative_milliseconds, default=0.0,
        help="EXPERIMENT ONLY: artificial delay before each per-client send; disabled by default",
    )
    # String environment defaults are validated by argparse just like CLI values.
    # Explicit CLI values take precedence, including over invalid env defaults.
    parser.add_argument(
        "--queue-max-messages", type=positive_int,
        default=os.getenv("LIVERECALL_QUEUE_MAX_MESSAGES", QUEUE_MAX_MESSAGES),
        help="Maximum queued messages per client; env: LIVERECALL_QUEUE_MAX_MESSAGES",
    )
    parser.add_argument(
        "--queue-max-bytes", type=positive_int,
        default=os.getenv("LIVERECALL_QUEUE_MAX_BYTES", QUEUE_MAX_BYTES),
        help="Maximum queued payload bytes per client; env: LIVERECALL_QUEUE_MAX_BYTES",
    )
    parser.add_argument(
        "--send-timeout", type=positive_seconds,
        default=os.getenv("LIVERECALL_SEND_TIMEOUT", CLIENT_SEND_TIMEOUT),
        help="Seconds allowed per send attempt; env: LIVERECALL_SEND_TIMEOUT",
    )
    parser.add_argument(
        "--close-timeout", type=positive_seconds,
        default=os.getenv("LIVERECALL_CLOSE_TIMEOUT", CLIENT_CLOSE_TIMEOUT),
        help="Seconds allowed per close attempt; env: LIVERECALL_CLOSE_TIMEOUT",
    )
    parser.add_argument(
        "--keepalive-interval", type=positive_seconds,
        default=os.getenv("LIVERECALL_KEEPALIVE_INTERVAL", KEEPALIVE_INTERVAL),
        help="Seconds between keepalive pings; env: LIVERECALL_KEEPALIVE_INTERVAL",
    )
    parser.add_argument(
        "--keepalive-timeout", type=positive_seconds,
        default=os.getenv("LIVERECALL_KEEPALIVE_TIMEOUT", KEEPALIVE_TIMEOUT),
        help="Drop a client whose pong is this late (it fell behind); "
             "env: LIVERECALL_KEEPALIVE_TIMEOUT",
    )
    return parser


def main() -> None:
    global QUEUE_MAX_MESSAGES, QUEUE_MAX_BYTES, CLIENT_SEND_TIMEOUT, CLIENT_CLOSE_TIMEOUT
    global NAIVE_DELIVERY, EXPERIMENT_SEND_DELAY, KEEPALIVE_INTERVAL, KEEPALIVE_TIMEOUT
    parser = build_parser()
    args = parser.parse_args()
    # Set once before the event loop starts; all handlers use the same settings.
    QUEUE_MAX_MESSAGES = args.queue_max_messages
    QUEUE_MAX_BYTES = args.queue_max_bytes
    CLIENT_SEND_TIMEOUT = args.send_timeout
    CLIENT_CLOSE_TIMEOUT = args.close_timeout
    NAIVE_DELIVERY = args.naive
    EXPERIMENT_SEND_DELAY = args.experiment_send_delay_ms
    KEEPALIVE_INTERVAL = args.keepalive_interval
    KEEPALIVE_TIMEOUT = args.keepalive_timeout
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
