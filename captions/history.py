"""Bounded server-side history of finalized captions for recovery/late join.

History keeps finals in arrival order (oldest -> newest), each already
serialized once by CaptionState. It is bounded two ways:
  * by time: finals received more than ``window_seconds`` ago (server
    monotonic clock) are pruned; the assignment requires >= 60 s;
  * by count: at most ``max_items`` finals, as a hard memory cap under bursts.

The time bound uses the server's own clock, not ``source_ts``: retention is a
server resource decision and must not depend on the source's clock being
correct or synchronized.
"""

from __future__ import annotations

from collections import deque
from time import monotonic
from typing import Callable

from .state import Event

MIN_WINDOW_SECONDS = 60.0

_HISTORY_PREFIX = '{"type":"HISTORY","items":['
_HISTORY_SUFFIX = "]}"


def history_message(items: list[str]) -> str:
    """Build the HISTORY message from pre-serialized items without re-encoding."""
    return _HISTORY_PREFIX + ",".join(items) + _HISTORY_SUFFIX


def _utf8_len(text: str) -> int:
    # json.dumps uses ensure_ascii=True by default, so this is normally len(text).
    return len(text.encode("utf-8"))


class History:
    """Rolling window of finalized captions. Use from one asyncio event loop."""

    def __init__(self, *, window_seconds: float = 90.0, max_items: int = 5000,
                 clock: Callable[[], float] = monotonic) -> None:
        if not window_seconds >= MIN_WINDOW_SECONDS:
            raise ValueError(f"window_seconds must be >= {MIN_WINDOW_SECONDS:g}")
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
            raise ValueError("max_items must be a positive integer")
        self._window = float(window_seconds)
        self._max_items = max_items
        self._clock = clock
        self._items: deque[Event] = deque()
        self._pruned_by_count = 0

    def add(self, event: Event) -> None:
        """Store an accepted final. Non-finals are rejected loudly (a bug)."""
        if not event.final:
            raise ValueError("History stores finalized captions only")
        self._items.append(event)
        if len(self._items) > self._max_items:
            self._items.popleft()
            self._pruned_by_count += 1
        self.prune()

    def prune(self) -> None:
        """Drop finals older than the time window."""
        cutoff = self._clock() - self._window
        while self._items and self._items[0].received_at < cutoff:
            self._items.popleft()

    def since(self, last_segment: int, last_revision: int,
              seconds: float | None = None,
              max_bytes: int | None = None) -> list[str]:
        """Finals the client has not reported, oldest first.

        * Excludes finals at or before the client's resume point
          (``last_segment``/``last_revision`` = latest final it holds).
        * ``seconds`` (optional) further limits items to those received in the
          last ``seconds``; how it applies to resuming clients is the recovery
          owner's decision.
        * ``max_bytes`` (optional) bounds the size of the complete HISTORY
          message built by ``history_message``; when the items do not fit, the
          OLDEST are dropped so the client keeps the most recent context.
        """
        self.prune()
        if seconds is not None and seconds <= 0:
            return []  # the client asked for no recovery context
        cutoff = None if seconds is None else self._clock() - seconds
        budget = None
        if max_bytes is not None:
            budget = max_bytes - len(_HISTORY_PREFIX) - len(_HISTORY_SUFFIX)
            if budget <= 0:
                return []

        selected: list[str] = []
        used = 0
        # Walk newest -> oldest so a byte budget naturally keeps the newest items.
        for event in reversed(self._items):
            if cutoff is not None and event.received_at < cutoff:
                break  # arrival order: everything further left is older still
            if not (event.segment > last_segment
                    or (event.segment == last_segment and event.revision > last_revision)):
                continue
            if budget is not None:
                cost = _utf8_len(event.raw) + (1 if selected else 0)  # comma
                if used + cost > budget:
                    break
                used += cost
            selected.append(event.raw)
        selected.reverse()
        return selected

    def __len__(self) -> int:
        return len(self._items)

    def stats(self) -> dict[str, float | int]:
        oldest = (self._clock() - self._items[0].received_at) if self._items else 0.0
        return {
            "items": len(self._items),
            "oldest_age_seconds": oldest,
            "window_seconds": self._window,
            "max_items": self._max_items,
            "pruned_by_count": self._pruned_by_count,
        }
