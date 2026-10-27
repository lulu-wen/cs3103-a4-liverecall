"""One outgoing sender coroutine per client; independent of caption policy."""

from __future__ import annotations

import asyncio
import logging
import math
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
    """
    for name, value in (("send_timeout", send_timeout), ("close_timeout", close_timeout)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value <= 0):
            raise ValueError(f"{name} must be a positive finite number")

    close_code = 1000
    close_reason = "Sender stopped"
    try:
        while True:
            try:
                payload = await queue.get()
            except QueueClosed:
                return
            outcome = "error"
            if monitor is not None:
                monitor.send_started(payload)
            try:
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
            # get()/send() may both complete without yielding. Let receivers,
            # other senders, and timeout callbacks run even with a full queue.
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
        queue.close()
        # A cancelled/timed-out send must not be followed by another send on
        # this connection. Close it, bounding the closing handshake as well.
        try:
            await asyncio.wait_for(
                ws.close(code=close_code, reason=close_reason), timeout=close_timeout
            )
        except Exception:
            # Preserve the original send failure/cancellation for the owner.
            logger.warning("Client connection cleanup failed", exc_info=True)
