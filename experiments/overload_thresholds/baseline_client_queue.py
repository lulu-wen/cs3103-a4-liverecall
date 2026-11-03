"""Frozen pre-fix queue for reproducing the cross-segment dropping experiment.

EXPERIMENT ONLY. The unsafe lower-segment rule is intentionally preserved here;
the server must import delivery.client_queue instead.

Server-side bounded FIFO for one client's outgoing WebSocket messages.

Use on one asyncio event loop, with one consumer (sender task) per queue.
Caption metadata allows opt-in replacement of obsolete provisionals. The caller
decides when to enable it and must handle rejected pushes.
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
    segment: int | None = None
    revision: int | None = None
    final: bool | None = None


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
        return self._push(payload)

    def push_caption(
        self, payload: str | bytes, *, segment: int, revision: int,
        final: bool, coalesce: bool = False, drop_older_segments: bool = False,
    ) -> PushResult:
        """Admit an already validated caption, optionally replacing obsolete provisionals.

        Metadata must describe the serialized payload. The upstream caption state
        owns revision validation; this queue neither parses JSON nor keeps a
        second caption history. Normal mode retains every revision. Coalescing
        removes only queued, lower revisions of this segment with final=False.
        With coalesce=True, lower provisional revisions of this segment are
        replaceable. With drop_older_segments=True, queued provisional updates
        from lower-numbered segments are also replaceable; this is safe for the
        required source, which completes each segment before starting the next.
        Finals and opaque messages are never removed. Rejection leaves the
        entire backlog unchanged, including replaceable revisions.
        """
        for name, value in (("segment", segment), ("revision", revision)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (type(final) is not bool or type(coalesce) is not bool
                or type(drop_older_segments) is not bool):
            raise TypeError("final, coalesce and drop_older_segments must be booleans")
        return self._push(
            payload, segment, revision, final, coalesce, drop_older_segments
        )

    def _push(
        self, payload: str | bytes, segment: int | None = None,
        revision: int | None = None, final: bool | None = None,
        coalesce: bool = False, drop_older_segments: bool = False,
    ) -> PushResult:
        if not isinstance(payload, (str, bytes)):
            raise TypeError("payload must be serialized str or bytes")
        if self._closed:
            return PushResult.CLOSED
        size = len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        if size > self._max_bytes:
            return PushResult.TOO_LARGE
        obsolete = []
        if coalesce:
            obsolete = [
                item for item in self._items
                if item.final is False
                and (
                    (item.segment == segment and item.revision < revision)
                    or (
                        drop_older_segments
                        and item.segment is not None
                        and segment is not None
                        and item.segment < segment
                    )
                )
            ]
        removed_bytes = sum(item.size for item in obsolete)
        if (len(self._items) - len(obsolete) >= self._max_messages
                or self._pending_bytes - removed_bytes + size > self._max_bytes):
            return PushResult.FULL
        # Commit only after admission succeeds. Keep the remaining FIFO order;
        # append the new update after all messages that arrived before it.
        if obsolete:
            removed_ids = {id(item) for item in obsolete}
            remaining = deque()
            for item in self._items:
                if id(item) not in removed_ids:
                    remaining.append(item)
            self._items = remaining
        self._items.append(_PendingMessage(payload, size, monotonic(), segment, revision, final))
        self._pending_bytes += size - removed_bytes
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
