"""Unit tests for captions.history.History (bounded finalized-caption window)."""

import json
import unittest

from captions.history import History, history_message
from captions.state import Event


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def final(segment, received_at, revision=3, text=None):
    msg = {
        "type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
        "revision": revision, "source_ts": 1760000000.0 + segment, "final": True,
        "text": text if text is not None else f"segment {segment}",
    }
    return Event(segment, revision, True, json.dumps(msg, separators=(",", ":")), received_at)


def segments(items):
    return [json.loads(raw)["segment"] for raw in items]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.history = History(window_seconds=90, max_items=1000, clock=self.clock)

    def fill(self, count, spacing=1.0):
        # Segment n received at time 1000 + n*spacing; clock ends at the last one.
        for n in range(1, count + 1):
            self.clock.now = 1000.0 + n * spacing
            self.history.add(final(n, self.clock.now))

    def test_since_returns_unseen_finals_oldest_first(self):
        self.fill(10)
        self.assertEqual(segments(self.history.since(0, 0)), list(range(1, 11)))
        self.assertEqual(segments(self.history.since(7, 3)), [8, 9, 10])
        self.assertEqual(self.history.since(10, 3), [])
        self.assertEqual(self.history.since(99, 1), [])

    def test_same_segment_higher_revision_is_included(self):
        self.fill(3)
        self.assertEqual(segments(self.history.since(3, 2)), [3])

    def test_time_window_prunes_old_finals(self):
        self.fill(200)                         # 200 s of finals, 1 per second
        self.assertEqual(len(self.history), 91)  # received within the last 90 s
        self.assertEqual(segments(self.history.since(0, 0))[0], 110)
        self.clock.now += 1000                 # long silence: prune on read
        self.assertEqual(self.history.since(0, 0), [])
        self.assertEqual(len(self.history), 0)

    def test_keeps_at_least_sixty_seconds(self):
        self.fill(200)
        oldest_age = self.history.stats()["oldest_age_seconds"]
        self.assertGreaterEqual(oldest_age, 60)

    def test_count_cap_bounds_memory_under_bursts(self):
        history = History(window_seconds=90, max_items=50, clock=self.clock)
        for n in range(1, 501):                 # 500 finals in the same instant
            history.add(final(n, self.clock.now))
        self.assertEqual(len(history), 50)
        self.assertEqual(segments(history.since(0, 0))[0], 451)
        self.assertEqual(history.stats()["pruned_by_count"], 450)

    def test_seconds_limits_context(self):
        self.fill(60)                           # clock = 1060
        self.assertEqual(segments(self.history.since(0, 0, seconds=15)), list(range(45, 61)))
        self.assertEqual(self.history.since(0, 0, seconds=0), [])

    def test_max_bytes_drops_oldest_and_message_fits(self):
        self.fill(40)
        full = history_message(self.history.since(0, 0))
        limit = len(full) // 2
        items = self.history.since(0, 0, max_bytes=limit)
        message = history_message(items)
        self.assertLessEqual(len(message.encode("utf-8")), limit)
        self.assertEqual(segments(items)[-1], 40)             # newest kept
        self.assertEqual(segments(items), list(range(segments(items)[0], 41)))  # contiguous
        self.assertEqual(self.history.since(0, 0, max_bytes=10), [])

    def test_max_bytes_counts_non_ascii(self):
        self.history.add(final(1, self.clock.now, text="é" * 10))
        raw = self.history.since(0, 0)
        exact = len(history_message(raw).encode("utf-8"))
        self.assertEqual(len(self.history.since(0, 0, max_bytes=exact)), 1)
        self.assertEqual(self.history.since(0, 0, max_bytes=exact - 1), [])

    def test_history_message_is_valid_json(self):
        self.fill(3)
        message = json.loads(history_message(self.history.since(0, 0)))
        self.assertEqual(message["type"], "HISTORY")
        self.assertTrue(all(item["final"] for item in message["items"]))
        self.assertEqual(json.loads(history_message([])), {"type": "HISTORY", "items": []})

    def test_rejects_non_final_and_bad_limits(self):
        provisional = Event(1, 1, False, "{}", self.clock.now)
        with self.assertRaises(ValueError):
            self.history.add(provisional)
        for kwargs in ({"window_seconds": 59}, {"max_items": 0}, {"max_items": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                History(**kwargs)


if __name__ == "__main__":
    unittest.main()
