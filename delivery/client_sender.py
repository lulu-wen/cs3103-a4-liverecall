"""One outgoing sender coroutine per client; independent of caption policy."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Iterable
from typing import Protocol

from .client_monitor import ClientMonitor
from .client_queue import ClientQueue, QueueClosed


logger = logging.getLogger(__name__)


class ClientConnection(Protocol):
    """The small part of a WebSocket connection needed by the sender."""

    async def send(self, message: str | bytes) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


async def run_client_sender(
    ws: ClientConnection,
    queue: ClientQueue,
    *,
    send_timeout: float = 10.0,
    close_timeout: float = 3.0,
    monitor: ClientMonitor | None = None,
    experiment_send_delay: float = 0.0,
    initial_messages: Iterable[str | bytes] = (),
) -> None:
    """Consume one client's queue and send payloads sequentially.

    Start exactly one task per connection. The connection handler owns this
    task: on disconnect it must cancel and await it, even if the queue is empty.
    Queue close is an abort, not a drain; an already in-flight send can finish.

    A failed/timed-out send is not retried: delivery is uncertain, so recovery
    belongs to the reconnect protocol. Send errors and cancellation propagate
    to the owner after cleanup. Only one send can be in flight per client.

    The limits here are configurable safety deadlines, not lag detection or a
    semantic overload policy. Queue rejection remains the producer's concern.
    initial_messages must be a bounded snapshot iterator, not a live producer.
    """
    for name, value in (("send_timeout", send_timeout), ("close_timeout", close_timeout)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be a positive finite number")
    if (isinstance(experiment_send_delay, bool)
            or not isinstance(experiment_send_delay, (int, float))
            or not math.isfinite(experiment_send_delay)
            or experiment_send_delay < 0):
        raise ValueError("experiment_send_delay must be a non-negative finite number")

    close_code = 1000
    close_reason = "Sender stopped"
    initial = iter(initial_messages)
    try:
        while True:
            try:
                # The immutable recovery prefix precedes every queued live update.
                if initial is not None:
                    try:
                        payload = next(initial)
                    except StopIteration:
                        initial = None  # Release snapshot references after recovery.
                        continue
                else:
                    # Wait when empty; send timing starts only after selecting a payload.
                    payload = await queue.get()
            except QueueClosed:
                return
            outcome = "error"
            if monitor is not None:
                monitor.send_started(payload)
            try:
                # Experiment-only hook: simulate a constrained per-client sender without
                # slowing source->server traffic. Disabled by default and intentionally
                # outside send_timeout so it cannot masquerade as a network send failure.
                if experiment_send_delay:
                    await asyncio.sleep(experiment_send_delay)
                # Limit the actual WebSocket send attempt, excluding queue and experiment delay.
                await asyncio.wait_for(ws.send(payload), timeout=send_timeout)
                outcome = "sent"
            except asyncio.TimeoutError:
                outcome = "timeout"
                close_code = 1013
                close_reason = "Send deadline exceeded"
                raise
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            finally:
                if monitor is not None:
                    monitor.send_finished(outcome)
            # Yield execution without a fixed delay or rate limit.
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        close_reason = "Sender cancelled"
        raise
    except Exception:
        if close_code == 1000:
            close_code = 1011
            close_reason = "Send failed"
        raise
    finally:
        # Abort queued delivery before awaiting socket close; this does not drain the queue.
        queue.close()
        # Initiate socket closure and bound the wait for the closing handshake.
        try:
            await asyncio.wait_for(
                ws.close(code=close_code, reason=close_reason), timeout=close_timeout
            )
        except Exception:
            # Preserve the original send failure/cancellation for the owner.
            logger.warning("Client connection cleanup failed", exc_info=True)
