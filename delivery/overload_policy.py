"""Per-client overload state for semantic caption delivery.

The policy observes existing delivery measurements; it never owns queue data or
network I/O.  NORMAL preserves every revision.  DEGRADED lets the producer opt
into ClientQueue's queue-wide removal of superseded provisional revisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .client_monitor import DeliveryStats


class OverloadMode(Enum):
    NORMAL = "normal"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class OverloadThresholds:
    """Hysteresis thresholds used by one client's policy.

    Ratios make the policy scale with configured queue capacities.  Age and
    in-flight time catch a blocked sender before a large queue necessarily
    fills.  Exit thresholds are deliberately lower than entry thresholds.
    """

    enter_queue_ratio: float = 0.50
    exit_queue_ratio: float = 0.20
    enter_oldest_seconds: float = 0.50
    exit_oldest_seconds: float = 0.15
    enter_in_flight_seconds: float = 0.50
    exit_in_flight_seconds: float = 0.15


class OverloadPolicy:
    """Choose NORMAL/DEGRADED independently for one client connection."""

    def __init__(
        self,
        *,
        max_messages: int,
        max_bytes: int,
        thresholds: OverloadThresholds | None = None,
    ) -> None:
        for name, value in (("max_messages", max_messages), ("max_bytes", max_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self._max_messages = max_messages
        self._max_bytes = max_bytes
        self._thresholds = thresholds or OverloadThresholds()
        self._mode = OverloadMode.NORMAL
        self._transitions_to_degraded = 0
        self._transitions_to_normal = 0

    @property
    def mode(self) -> OverloadMode:
        return self._mode

    @property
    def transitions_to_degraded(self) -> int:
        return self._transitions_to_degraded

    @property
    def transitions_to_normal(self) -> int:
        return self._transitions_to_normal

    def update(self, stats: DeliveryStats) -> OverloadMode:
        """Update state from one non-blocking monitor snapshot.

        Transport write-buffer size is intentionally not required: the monitor
        documents that it can be unavailable and that it doesn't include the
        OS TCP buffer.  Queue pressure plus send age are stable, explainable
        application-level signals for this policy.
        """
        queue_ratio = max(
            stats.queue.depth / self._max_messages,
            stats.queue.pending_bytes / self._max_bytes,
        )
        t = self._thresholds

        if self._mode is OverloadMode.NORMAL:
            unhealthy = (
                queue_ratio >= t.enter_queue_ratio
                or stats.queue.oldest_pending_seconds >= t.enter_oldest_seconds
                or (stats.in_flight and stats.in_flight_seconds >= t.enter_in_flight_seconds)
            )
            if unhealthy:
                self._mode = OverloadMode.DEGRADED
                self._transitions_to_degraded += 1
        else:
            healthy = (
                queue_ratio <= t.exit_queue_ratio
                and stats.queue.oldest_pending_seconds <= t.exit_oldest_seconds
                and (not stats.in_flight or stats.in_flight_seconds <= t.exit_in_flight_seconds)
            )
            if healthy:
                self._mode = OverloadMode.NORMAL
                self._transitions_to_normal += 1

        return self._mode
