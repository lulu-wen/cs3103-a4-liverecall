"""Server-side caption revision state: validation, ordering and deduplication.

One CaptionState holds the authoritative view of the lecture's segments.
``apply()`` decides whether an incoming CAPTION_UPDATE is new information;
only accepted updates are forwarded to clients and (if final) stored in
recovery history. Use from a single asyncio event loop.

Rules (assignment section 4.1):
  * an update is accepted only if its segment is not yet final AND its
    revision is strictly greater than the highest revision accepted so far;
  * a final caption is durable: nothing for that segment is accepted again;
  * updates whose fields have the wrong type are rejected as malformed, never
    raised, so one bad message cannot stop the source handler;
  * unknown fields are ignored: the source's original string is forwarded
    unchanged, so extra fields simply pass through to clients.

All retained state is bounded: active (non-final) segments and remembered
finalized segments are both capped; updates for segments older than the
forgotten range are rejected as ``too_old``.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic
from typing import Any

# Rejection reasons, also used as stats() keys.
ACCEPTED = "accepted"
MALFORMED = "malformed"
STALE = "stale"                # revision lower than one already accepted
DUPLICATE = "duplicate"        # same revision as one already accepted
AFTER_FINAL = "after_final"    # newer revision for an already-final segment
STALE_FINAL = "stale_final"    # a final whose revision is not newer than an accepted provisional
TOO_OLD = "too_old"            # segment older than the bounded remembered range

REASONS = (ACCEPTED, MALFORMED, STALE, DUPLICATE, AFTER_FINAL, STALE_FINAL, TOO_OLD)


@dataclass(frozen=True)
class Event:
    """An accepted caption update.

    ``raw`` is the source's original CAPTION_UPDATE string, forwarded as-is
    and shared by every client queue and by recovery history. ``received_at``
    is the server's monotonic receive time (not the source's clock).
    """

    segment: int
    revision: int
    final: bool
    raw: str
    received_at: float


def is_well_formed(msg: Any) -> bool:
    """True if the fields the server uses have the types caption_source.py sends.

    Only these fields are checked; any extra fields are allowed. ``type(x) is
    int`` (not isinstance) because in Python ``True`` is also an int.
    """
    return (isinstance(msg, dict)
            and msg.get("type") == "CAPTION_UPDATE"
            and type(msg.get("segment")) is int
            and type(msg.get("revision")) is int
            and type(msg.get("final")) is bool
            and type(msg.get("source_ts")) in (int, float)
            and isinstance(msg.get("text"), str))


class CaptionState:
    """Authoritative per-segment revision state for one lecture session."""

    def __init__(self, *, max_active_segments: int = 1000,
                 max_remembered_finals: int = 10000) -> None:
        for name, value in (("max_active_segments", max_active_segments),
                            ("max_remembered_finals", max_remembered_finals)):
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        self._max_active = max_active_segments
        self._max_finals = max_remembered_finals
        # segment -> latest accepted provisional Event (not yet final).
        self._active: dict[int, Event] = {}
        # segment -> final revision, in insertion order for bounded eviction.
        self._finalized: OrderedDict[int, int] = OrderedDict()
        # Every segment <= this number has been forgotten and is treated as closed.
        self._forgotten_upto = 0
        self._counts = {reason: 0 for reason in REASONS}

    def apply(self, msg: Any, raw: str) -> Event | None:
        """Accept a source update if it is new information; else return None.

        ``msg`` is the parsed message, ``raw`` the exact string received from
        the source (forwarded unchanged if accepted).
        """
        reason, event = self._decide(msg, raw)
        self._counts[reason] += 1
        return event

    def _decide(self, msg: Any, raw: str) -> tuple[str, Event | None]:
        # Text frames only: a bytes frame would reach browsers as a Blob.
        if not isinstance(raw, str) or not is_well_formed(msg):
            return MALFORMED, None
        segment, revision, final = msg["segment"], msg["revision"], msg["final"]

        if segment <= self._forgotten_upto:
            return TOO_OLD, None

        final_revision = self._finalized.get(segment)
        if final_revision is not None:
            # Durable final: never overwritten, whatever revision arrives.
            if revision == final_revision:
                return DUPLICATE, None
            return (AFTER_FINAL if revision > final_revision else STALE), None

        current = self._active.get(segment)
        if current is not None and revision <= current.revision:
            if final:
                # A "final" that is not newer than what clients already hold
                # cannot be shown (clients would classify it as stale), so it
                # is rejected and counted separately to make the anomaly visible.
                return STALE_FINAL, None
            return (DUPLICATE if revision == current.revision else STALE), None

        event = Event(segment, revision, final, raw, monotonic())
        if final:
            self._active.pop(segment, None)
            self._remember_final(segment, revision)
        else:
            self._active[segment] = event
            self._bound_active()
        return ACCEPTED, event

    def _remember_final(self, segment: int, revision: int) -> None:
        self._finalized[segment] = revision
        while len(self._finalized) > self._max_finals:
            old_segment, _ = self._finalized.popitem(last=False)
            self._forgotten_upto = max(self._forgotten_upto, old_segment)
        # Drop any active segment that has fallen into the forgotten range.
        for seg in [s for s in self._active if s <= self._forgotten_upto]:
            del self._active[seg]

    def _bound_active(self) -> None:
        # A source that abandons segments without finals must not grow memory:
        # evict the lowest-numbered active segments beyond the cap.
        while len(self._active) > self._max_active:
            del self._active[min(self._active)]

    def active_snapshot(self) -> list[str]:
        """Latest provisional revision of each active segment, oldest segment first.

        For late join / reconnect: send these as live CAPTION_UPDATEs after
        HISTORY (HISTORY itself must contain finals only).
        """
        return [self._active[seg].raw for seg in sorted(self._active)]

    def stats(self) -> dict[str, int]:
        """Counters per decision reason plus current bounded-state sizes."""
        return {
            **self._counts,
            "active_segments": len(self._active),
            "remembered_finals": len(self._finalized),
            "forgotten_upto": self._forgotten_upto,
        }
