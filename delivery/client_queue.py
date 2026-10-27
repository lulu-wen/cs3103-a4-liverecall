"""Server-side bounded FIFO for one client's outgoing WebSocket messages.

Use on one asyncio event loop, with one consumer (sender task) per queue.
This module does not interpret captions or decide overload policy. The caller
must handle rejected pushes; no existing message is silently discarded.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from enum import Enum
from time import monotonic


class PushResult(Enum):
    ACCEPTED = "accepted"
    FULL = "full"
    TOO_LARGE = "too_large"
    CLOSED = "closed"


class QueueClosed(Exception):
    """The queue is closed; the sender should stop consuming."""


@dataclass(frozen=True)
class QueueStats:
    depth: int
    pending_bytes: int
    oldest_pending_seconds: float
    closed: bool


@dataclass(frozen=True)
class _PendingMessage:
    payload: str | bytes
    size: int
    queued_at: float


class ClientQueue:
    """FIFO bounded by both message count and encoded payload bytes.

    Limits cover queued payloads, excluding Python object overhead, WebSocket
    framing/buffers, and the one message already taken by the sender. Pair this
    queue with transport monitoring and a send timeout when wiring the sender.
    """

    def __init__(self, *, max_messages: int, max_bytes: int) -> None:
        # Require explicit limits: capacity is a policy choice for integration.
        for name, value in (("max_messages", max_messages), ("max_bytes", max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._max_messages = max_messages
        self._max_bytes = max_bytes
        self._items: deque[_PendingMessage] = deque()
        self._pending_bytes = 0
        self._closed = False
        self._ready = asyncio.Event()

    def push(self, payload: str | bytes) -> PushResult:
        """Enqueue without awaiting network I/O or free capacity.

        FULL means the current backlog prevents admission. TOO_LARGE means this
        payload cannot fit even in an empty queue. In either case, the caller
        retains responsibility for delivery/recovery, particularly for finals.
        """
        if not isinstance(payload, (str, bytes)):
            raise TypeError("payload must be serialized str or bytes")
        if self._closed:
            return PushResult.CLOSED
        size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        if size > self._max_bytes:
            return PushResult.TOO_LARGE
        if (len(self._items) >= self._max_messages
                or self._pending_bytes + size > self._max_bytes):
            return PushResult.FULL
        self._items.append(_PendingMessage(payload, size, monotonic()))
        self._pending_bytes += size
        self._ready.set()
        return PushResult.ACCEPTED

    async def get(self) -> str | bytes:
        """Wait for the next payload; raise QueueClosed after close().

        Cancellation while waiting does not remove a queued message.
        """
        while True:
            if self._closed:
                raise QueueClosed("Client queue is closed")
            if self._items:
                item = self._items.popleft()
                self._pending_bytes -= item.size
                if not self._items:
                    self._ready.clear()
                return item.payload
            # No await between inspecting the queue and clearing the event:
            # producers on the same event loop cannot slip into that gap.
            self._ready.clear()
            await self._ready.wait()

    def close(self) -> None:
        """Abort pending delivery, free queued payloads, and wake the sender.

        Intended for client teardown, not graceful draining. The owner must
        also cancel/await any sender already blocked in WebSocket send().
        Repeated calls are safe.
        """
        self._closed = True
        self._items.clear()
        self._pending_bytes = 0
        self._ready.set()

    def stats(self) -> QueueStats:
        """Snapshot application backlog using a monotonic clock.

        Transport write-buffer size belongs to the WebSocket/sender layer and
        is deliberately not measured here.
        """
        age = monotonic() - self._items[0].queued_at if self._items else 0.0
        return QueueStats(len(self._items), self._pending_bytes, age, self._closed)
