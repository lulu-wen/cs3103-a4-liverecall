"""Preserve normal FIFO delivery; use protected lanes under overload."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import monotonic

from .client_queue import ClientQueue, PushResult, QueueClosed, QueueStats, _PendingMessage


@dataclass(frozen=True)
class FinalAwareStats(QueueStats):
    final_depth: int = 0
    active_depth: int = 0
    final_bytes: int = 0
    active_bytes: int = 0
    degraded: bool = False


class FinalAwareQueue(ClientQueue):
    """NORMAL: original FIFO. DEGRADED: final/control FIFO + latest active map.

    The normal FIFO and protected FIFO each use max_messages/max_bytes. The active
    map has a separate max_active_segments limit and max_bytes budget. Only one
    representation is populated at a time, so pending payloads are bounded by
    max_messages + max_active_segments and 2 * max_bytes in degraded mode.
    """

    def __init__(
        self,
        *,
        max_messages: int,
        max_bytes: int,
        max_active_segments: int = 1000,
        final_burst: int = 4,
    ) -> None:
        super().__init__(max_messages=max_messages, max_bytes=max_bytes)
        for name, value in (("max_active_segments", max_active_segments),
                            ("final_burst", final_burst)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        self._max_active_segments = max_active_segments
        self._final_burst = final_burst
        self._final_backlog_target = min(4, max_messages)
        self._finals: deque[_PendingMessage] = deque()
        self._active: dict[int, _PendingMessage] = {}
        self._final_bytes = 0
        self._active_bytes = 0
        self._degraded = False
        self._final_streak = 0

    def push(self, payload: str | bytes) -> PushResult:
        if not self._degraded:
            return super().push(payload)
        return self._admit_degraded(payload)

    def push_caption(
        self,
        payload: str | bytes,
        *,
        segment: int,
        revision: int,
        final: bool,
        coalesce: bool = False,
    ) -> PushResult:
        for name, value in (("segment", segment), ("revision", revision)):
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(final) is not bool or type(coalesce) is not bool:
            raise TypeError("final and coalesce must be booleans")
        size = self._payload_size(payload)
        if self._closed:
            return PushResult.CLOSED
        if size > self._max_bytes:
            return PushResult.TOO_LARGE

        if not coalesce and self._restore_normal_if_fits(size):
            return super().push_caption(
                payload, segment=segment, revision=revision, final=final,
            )
        return self._admit_degraded(payload, segment, revision, final)

    @staticmethod
    def _payload_size(payload: str | bytes) -> int:
        if not isinstance(payload, (str, bytes)):
            raise TypeError("payload must be serialized str or bytes")
        if isinstance(payload, str):
            return len(payload.encode("utf-8"))
        return len(payload)

    def _restore_normal_if_fits(self, incoming_size: int) -> bool:
        if not self._degraded:
            return True
        depth = len(self._finals) + len(self._active)
        if depth + 1 > self._max_messages:
            return False
        if self._pending_bytes + incoming_size > self._max_bytes:
            return False

        # Restore arrival order for the retained backlog before normal admission.
        retained = list(self._finals) + list(self._active.values())
        retained.sort(key=lambda item: item.sequence)
        self._items = deque(retained)
        self._finals.clear()
        self._active.clear()
        self._final_bytes = 0
        self._active_bytes = 0
        self._final_streak = 0
        self._degraded = False
        return True

    def _admit_degraded(
        self,
        payload: str | bytes,
        segment: int | None = None,
        revision: int | None = None,
        final: bool | None = None,
    ) -> PushResult:
        size = self._payload_size(payload)
        if self._closed:
            return PushResult.CLOSED
        if size > self._max_bytes:
            return PushResult.TOO_LARGE

        # Build candidates first. A failed admission must leave the backlog intact.
        if self._degraded:
            pending = list(self._finals) + list(self._active.values())
        else:
            pending = list(self._items)
        incoming = _PendingMessage(
            payload, size, monotonic(), segment, revision, final, self._next_sequence,
        )
        pending.append(incoming)
        finals = []
        active = {}
        final_revisions = {}

        for item in pending:
            if item.final is False:
                previous = active.get(item.segment)
                if previous is None or item.revision > previous.revision:
                    active[item.segment] = item
            else:
                finals.append(item)  # Opaque control messages remain protected too.
                if item.final is True:
                    final_revisions[item.segment] = item.revision

        for active_segment, item in list(active.items()):
            final_revision = final_revisions.get(active_segment)
            if final_revision is not None and final_revision > item.revision:
                del active[active_segment]

        final_bytes = sum(item.size for item in finals)
        active_bytes = sum(item.size for item in active.values())
        if len(finals) > self._max_messages or final_bytes > self._max_bytes:
            return PushResult.FULL
        if len(active) > self._max_active_segments or active_bytes > self._max_bytes:
            return PushResult.FULL

        self._items.clear()
        self._finals = deque(finals)
        self._active = active
        self._final_bytes = final_bytes
        self._active_bytes = active_bytes
        self._pending_bytes = final_bytes + active_bytes
        self._next_sequence += 1
        self._degraded = True
        self._ready.set()
        return PushResult.ACCEPTED

    async def get(self) -> str | bytes:
        while True:
            if self._closed:
                raise QueueClosed("Client queue is closed")
            item = None
            if not self._degraded and self._items:
                item = self._items.popleft()
            elif self._degraded:
                # A fixed 4:1 split alone would cap final throughput at 80%.
                # Drain a growing final backlog first; share spare service below it.
                finals_urgent = len(self._finals) >= self._final_backlog_target
                if self._finals and (
                    not self._active or finals_urgent or self._final_streak < self._final_burst
                ):
                    item = self._finals.popleft()
                    self._final_bytes -= item.size
                    self._final_streak = min(self._final_streak + 1, self._final_burst)
                elif self._active:
                    active_segment = next(iter(self._active))
                    item = self._active.pop(active_segment)
                    self._active_bytes -= item.size
                    self._final_streak = 0
            if item is not None:
                self._pending_bytes -= item.size
                if not self._items and not self._finals and not self._active:
                    self._ready.clear()
                return item.payload
            self._ready.clear()
            await self._ready.wait()

    def close(self) -> None:
        self._finals.clear()
        self._active.clear()
        self._final_bytes = 0
        self._active_bytes = 0
        self._degraded = False
        self._final_streak = 0
        super().close()

    def stats(self) -> FinalAwareStats:
        if not self._degraded:
            normal = super().stats()
            return FinalAwareStats(
                normal.depth, normal.pending_bytes, normal.oldest_pending_seconds, normal.closed,
            )
        pending = list(self._finals) + list(self._active.values())
        age = 0.0
        if pending:
            age = monotonic() - min(item.queued_at for item in pending)
        return FinalAwareStats(
            depth=len(pending), pending_bytes=self._pending_bytes,
            oldest_pending_seconds=age, closed=self._closed,
            final_depth=len(self._finals), active_depth=len(self._active),
            final_bytes=self._final_bytes, active_bytes=self._active_bytes,
            degraded=True,
        )
