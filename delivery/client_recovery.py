"""Bounded, immutable recovery snapshot streamed before live queue delivery."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from captions.history import history_message


@dataclass(frozen=True)
class RecoveryPlan:
    """Reference existing bounded state; serialize only one HISTORY batch at a time.

    Build without awaiting, immediately before session registration. Finals and
    active messages must come from the server's bounded History/CaptionState.
    This does not extend retention or recover entries already pruned by History.
    """

    finals: tuple[str, ...]
    active: tuple[str, ...]
    batches: tuple[tuple[int, int], ...]
    max_message_bytes: int

    @classmethod
    def build(
        cls,
        finals: Sequence[str],
        active: Sequence[str],
        *,
        max_message_bytes: int,
    ) -> RecoveryPlan:
        if type(max_message_bytes) is not int or max_message_bytes <= 0:
            raise ValueError("max_message_bytes must be a positive integer")

        overhead = len(history_message([]).encode("utf-8"))

        if overhead > max_message_bytes:
            raise ValueError("Empty HISTORY exceeds the message limit")

        # Preflight every item before sending any partial recovery response.
        for raw in active:
            if len(raw.encode("utf-8")) > max_message_bytes:
                raise ValueError("Active caption exceeds the message limit")

        batches = []
        start = 0
        used = overhead

        for index, raw in enumerate(finals):
            size = len(raw.encode("utf-8"))

            if size + overhead > max_message_bytes:
                raise ValueError("One final cannot fit a HISTORY message")

            cost = size + (1 if index > start else 0)

            if used + cost > max_message_bytes:
                batches.append((start, index))
                start = index
                used = overhead
                cost = size

            used += cost

        batches.append((start, len(finals)))  # Empty recovery still sends HISTORY.

        return cls(
            tuple(finals),
            tuple(active),
            tuple(batches),
            max_message_bytes,
        )

    def messages(self) -> Iterator[str]:
        for start, end in self.batches:
            yield history_message(self.finals[start:end])

        yield from self.active
