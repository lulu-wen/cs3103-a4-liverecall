"""On-demand client delivery snapshots; no polling tasks or retained samples."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from client_queue import ClientQueue, QueueStats


@dataclass(frozen=True)
class DeliveryStats:
    queue: QueueStats
    in_flight: bool
    in_flight_bytes: int
    in_flight_seconds: float
    sent_messages: int
    sent_payload_bytes: int
    send_errors: int
    send_timeouts: int
    last_send_seconds: float | None
    write_buffer_bytes: int | None


class ClientMonitor:
    """Use one instance per sender, on the same asyncio event loop.

    Counts describe local send() completions, not peer acknowledgements or
    caption correctness. Payload bytes exclude framing/compression. Snapshots
    do not mutate the queue or select any overload policy.
    """

    def __init__(self) -> None:
        self._started_at: float | None = None
        self._in_flight_bytes = 0
        self._sent_messages = 0
        self._sent_payload_bytes = 0
        self._send_errors = 0
        self._send_timeouts = 0
        self._last_send_seconds: float | None = None

    def send_started(self, payload: str | bytes) -> None:
        """Called by the sender after dequeue, before awaiting send()."""
        if self._started_at is not None:
            raise RuntimeError("Monitor already has an in-flight send")
        self._in_flight_bytes = (
            len(payload.encode("utf-8")) if isinstance(payload, str) else len(payload)
        )
        self._started_at = monotonic()

    def send_finished(self, outcome: str) -> None:
        """Finish an attempt: sent, error, timeout, or cancelled.

        Cancellation isn't counted as a transport failure. Timeouts have their
        own counter (they aren't also counted in send_errors).
        """
        if outcome not in ("sent", "error", "timeout", "cancelled"):
            raise ValueError("Unknown send outcome")
        if self._started_at is None:
            raise RuntimeError("No send is in flight")
        self._last_send_seconds = monotonic() - self._started_at
        if outcome == "sent":
            self._sent_messages += 1
            self._sent_payload_bytes += self._in_flight_bytes
        elif outcome == "error":
            self._send_errors += 1
        elif outcome == "timeout":
            self._send_timeouts += 1
        self._started_at = None
        self._in_flight_bytes = 0

    def snapshot(self, queue: ClientQueue, ws: object) -> DeliveryStats:
        """Read queue, sender and transport state without awaiting I/O.

        write_buffer_bytes is the asyncio transport output buffer only, not
        the OS TCP buffer or total memory usage. None means unavailable, which
        must not be interpreted as an empty buffer.
        """
        transport = getattr(ws, "transport", None)
        get_size = getattr(transport, "get_write_buffer_size", None)
        write_buffer_bytes = None
        if callable(get_size):
            try:
                size = get_size()
            except (OSError, RuntimeError, NotImplementedError):
                pass  # Closed/unsupported transports may not expose a size.
            else:
                if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                    write_buffer_bytes = size
        age = 0.0 if self._started_at is None else monotonic() - self._started_at
        return DeliveryStats(
            queue=queue.stats(),
            in_flight=self._started_at is not None,
            in_flight_bytes=self._in_flight_bytes,
            in_flight_seconds=age,
            sent_messages=self._sent_messages,
            sent_payload_bytes=self._sent_payload_bytes,
            send_errors=self._send_errors,
            send_timeouts=self._send_timeouts,
            last_send_seconds=self._last_send_seconds,
            write_buffer_bytes=write_buffer_bytes,
        )
