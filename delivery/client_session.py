"""Own the registry entry, queue and tasks for one client connection."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable
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
    # Parameters after * require names, e.g. send_timeout=10.
    *,
    send_timeout: float = 10.0,
    close_timeout: float = 3.0,
    monitor: ClientMonitor | None = None,
    experiment_send_delay: float = 0.0,
    initial_messages: Iterable[str | bytes] = (),
) -> None:
    """Register a client and supervise its sender until disconnect or failure.

    The caller may supply a bounded recovery prefix. Registration and task
    creation contain no await, so producers on the same event loop cannot
    interleave live updates before registration. Build the recovery snapshot
    without yielding before entering this coroutine.

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

    clients[ws] = queue  # Make this queue available to the broadcaster.
    # The session creates and owns both tasks; the server does not supply their results.
    sender = asyncio.create_task(
        run_client_sender(
            ws, queue, send_timeout=send_timeout, close_timeout=close_timeout,
            monitor=monitor, experiment_send_delay=experiment_send_delay,
            initial_messages=initial_messages,
        ),
        name="client-sender",
    )
    # wait_closed() observes closure; it does not initiate it.
    disconnected = asyncio.create_task(ws.wait_closed(), name="client-disconnect")
    failure: BaseException | None = None  # Preserve the original error during cleanup.
    try:
        # Either task finishing starts teardown; wait does not cancel the other task.
        done, _ = await asyncio.wait(
            (sender, disconnected), return_when=asyncio.FIRST_COMPLETED
        )
        for task in (sender, disconnected):
            if task in done:
                # Read only finished tasks; result() re-raises errors or cancellation.
                task.result()
    except BaseException as exc:
        failure = exc
        raise  # The finally block runs before this error reaches the server.
    finally:
        # Unregister before awaiting cleanup; identity protects a replacement queue.
        if clients.get(ws) is queue:
            del clients[ws]
        queue.close()

        async def cleanup() -> None:
            for task in (sender, disconnected):
                if not task.done():
                    task.cancel()  # Request cancellation; still await actual completion.
            # Await both tasks and collect errors so one failure cannot interrupt teardown.
            results = await asyncio.gather(sender, disconnected, return_exceptions=True)
            # Ensure closure even if the sender was cancelled before its finally could run.
            # Repeated close calls are safe; 1000 means normal closure.
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

        # Shield cleanup from repeated cancellation, but still await it before returning.
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
