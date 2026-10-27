"""Own the registry entry, queue and tasks for one client connection."""

from __future__ import annotations

import asyncio
import logging
import math
from typing import MutableMapping, Protocol

from .client_monitor import ClientMonitor
from .client_queue import ClientQueue
from .client_sender import ClientConnection, run_client_sender


logger = logging.getLogger(__name__)


class SessionConnection(ClientConnection, Protocol):
    async def wait_closed(self) -> None: ...


async def run_client_session(
    ws: SessionConnection,
    queue: ClientQueue,
    clients: MutableMapping[SessionConnection, ClientQueue],
    *,
    send_timeout: float = 10.0,
    close_timeout: float = 3.0,
    monitor: ClientMonitor | None = None,
) -> None:
    """Register a client and supervise its sender until disconnect or failure.

    P2 may enqueue HISTORY before calling this function. Registration and task
    creation contain no await, so producers on the same event loop cannot
    interleave live updates before registration. History construction itself
    must still be coordinated with P1/P2.

    The caller owns ws/queue until this coroutine starts; after entry, this
    function owns their cleanup. Use one session per connection. Queue-full
    policy remains with the producer/P4. This does not read client messages.
    """
    if ws in clients:
        raise ValueError("Connection already registered")
    if queue.stats().closed:
        raise ValueError("Cannot register a closed queue")

    # Validate before transferring ownership or creating tasks.
    for name, value in (("send_timeout", send_timeout), ("close_timeout", close_timeout)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be a positive finite number")

    clients[ws] = queue
    sender = asyncio.create_task(
        run_client_sender(
            ws, queue, send_timeout=send_timeout, close_timeout=close_timeout,
            monitor=monitor,
        ),
        name="client-sender",
    )
    disconnected = asyncio.create_task(ws.wait_closed(), name="client-disconnect")
    failure: BaseException | None = None
    try:
        done, _ = await asyncio.wait(
            (sender, disconnected), return_when=asyncio.FIRST_COMPLETED
        )
        for task in (sender, disconnected):
            if task in done:
                task.result()  # Propagate sender/connection errors after cleanup.
    except BaseException as exc:
        failure = exc
        raise
    finally:
        # Stop new broadcasts before yielding during cleanup. Identity protects
        # a replacement registry entry from deletion by an older session.
        if clients.get(ws) is queue:
            del clients[ws]
        queue.close()

        async def cleanup() -> None:
            for task in (sender, disconnected):
                if not task.done():
                    task.cancel()
            results = await asyncio.gather(sender, disconnected, return_exceptions=True)
            # Always request close, including when sender was cancelled before
            # its coroutine started (so its finally block never ran). WebSocket
            # close is idempotent; this can also retry failed sender cleanup.
            try:
                await asyncio.wait_for(
                    ws.close(code=1000, reason="Client session ended"),
                    timeout=close_timeout,
                )
            except Exception:
                logger.warning("Session connection cleanup failed", exc_info=True)
            # Observe errors that raced with wait_closed(), not just tasks in
            # the original 'done' set. Keep an existing handler error primary.
            for result in results:
                if isinstance(result, Exception) and result is not failure:
                    if failure is None:
                        raise result
                    logger.warning("Additional client task failure: %r", result)

        # Shield cleanup from repeated handler cancellation. Keep a strong task
        # reference and await it: no background cleanup is left behind.
        cleanup_task = asyncio.create_task(cleanup(), name="client-cleanup")
        cancelled_during_cleanup = False
        while not cleanup_task.done():
            try:
                await asyncio.shield(cleanup_task)
            except asyncio.CancelledError:
                cancelled_during_cleanup = True
        cleanup_task.result()
        if cancelled_during_cleanup and failure is None:
            raise asyncio.CancelledError
