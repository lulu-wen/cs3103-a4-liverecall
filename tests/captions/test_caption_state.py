"""Unit tests for captions.state.CaptionState (revision/dedup rules)."""

import json
import unittest

from captions.state import CaptionState, is_well_formed


def cap(segment, revision, final=False, text=None, **extra):
    msg = {
        "type": "CAPTION_UPDATE", "session": "L1", "segment": segment,
        "revision": revision, "source_ts": 1760000000.5, "final": final,
        "text": text if text is not None else f"s{segment} r{revision}",
    }
    msg.update(extra)
    return msg


class CaptionStateTests(unittest.TestCase):
    def setUp(self):
        self.state = CaptionState()

    def apply(self, msg):
        # The server passes the parsed message and the exact string it received.
        return self.state.apply(msg, json.dumps(msg))

    def accepted(self, msg):
        return self.apply(msg) is not None

    def test_normal_sequence_is_accepted_in_order(self):
        for revision, final in ((1, False), (2, False), (3, True)):
            event = self.apply(cap(1, revision, final))
            self.assertIsNotNone(event)
            self.assertEqual((event.segment, event.revision, event.final), (1, revision, final))
        self.assertEqual(self.state.stats()["accepted"], 3)

    def test_older_revision_never_overwrites_newer(self):
        self.assertTrue(self.accepted(cap(1, 3)))
        self.assertFalse(self.accepted(cap(1, 2)))
        self.assertFalse(self.accepted(cap(1, 1)))
        self.assertEqual(self.state.stats()["stale"], 2)
        self.assertTrue(self.accepted(cap(1, 4)))

    def test_same_revision_is_duplicate(self):
        self.assertTrue(self.accepted(cap(1, 1)))
        self.assertFalse(self.accepted(cap(1, 1)))
        self.assertEqual(self.state.stats()["duplicate"], 1)

    def test_skipped_revisions_are_allowed(self):
        # No fixed number of revisions: 1 -> 5 -> final 9 is valid.
        self.assertTrue(self.accepted(cap(1, 1)))
        self.assertTrue(self.accepted(cap(1, 5)))
        self.assertTrue(self.accepted(cap(1, 9, final=True)))

    def test_single_revision_final(self):
        self.assertTrue(self.accepted(cap(1, 1, final=True)))

    def test_final_is_durable(self):
        self.assertTrue(self.accepted(cap(1, 3, final=True)))
        self.assertFalse(self.accepted(cap(1, 3, final=True)))   # duplicate final
        self.assertFalse(self.accepted(cap(1, 4)))               # newer after final
        self.assertFalse(self.accepted(cap(1, 2)))               # older after final
        self.assertFalse(self.accepted(cap(1, 5, final=True)))   # second final
        stats = self.state.stats()
        self.assertEqual((stats["duplicate"], stats["after_final"], stats["stale"]), (1, 2, 1))

    def test_final_not_newer_than_provisional_is_rejected(self):
        # Clients already hold revision 3; a "final" revision 2 would be stale to them.
        self.assertTrue(self.accepted(cap(1, 3)))
        self.assertFalse(self.accepted(cap(1, 2, final=True)))
        self.assertFalse(self.accepted(cap(1, 3, final=True)))
        self.assertEqual(self.state.stats()["stale_final"], 2)
        self.assertTrue(self.accepted(cap(1, 4, final=True)))

    def test_segment_gaps_and_interleaved_active_segments(self):
        self.assertTrue(self.accepted(cap(5, 1)))
        self.assertTrue(self.accepted(cap(9, 1)))      # gap, two active segments
        self.assertTrue(self.accepted(cap(5, 2, final=True)))
        self.assertTrue(self.accepted(cap(9, 2)))
        self.assertEqual(len(self.state.active_snapshot()), 1)

    def test_active_snapshot_holds_latest_provisional_only(self):
        self.apply(cap(2, 1))
        self.apply(cap(2, 2))
        self.apply(cap(1, 1))
        snapshot = [json.loads(raw) for raw in self.state.active_snapshot()]
        self.assertEqual([(m["segment"], m["revision"]) for m in snapshot], [(1, 1), (2, 2)])
        self.apply(cap(2, 3, final=True))
        self.assertEqual([json.loads(r)["segment"] for r in self.state.active_snapshot()], [1])

    def test_malformed_updates_are_rejected_not_raised(self):
        missing = cap(1, 1)
        del missing["revision"]
        bad = [
            None, [], "text", {"type": "CAPTION_UPDATE"}, missing,
            cap("3", 1), cap(1, "2"), cap(True, 1), cap(1, 1.0), cap(1, None),
            cap(0, 1), cap(-1, 1), cap(1, 0), cap(1, -1),
            cap(1, 1, source_ts="now"), cap(1, 1, source_ts=True),
            cap(1, 1, final="true"), cap(1, 1, final=1), cap(1, 1, text=5),
            {**cap(1, 1), "type": "SOMETHING_ELSE"},
        ]
        for msg in bad:
            with self.subTest(msg=msg):
                self.assertFalse(is_well_formed(msg))
                self.assertIsNone(self.state.apply(msg, "{}"))
        self.assertEqual(self.state.stats()["malformed"], len(bad))
        self.assertTrue(self.accepted(cap(1, 1)))  # state unaffected

    def test_binary_frame_is_rejected(self):
        msg = cap(1, 1)
        self.assertIsNone(self.state.apply(msg, json.dumps(msg).encode()))
        self.assertEqual(self.state.stats()["malformed"], 1)

    def test_original_string_is_forwarded_unchanged(self):
        # caption_source.py style (spaces after separators) plus an unknown field.
        msg = cap(7, 2, final=True, phase="warmup")
        raw = json.dumps(msg)
        event = self.state.apply(msg, raw)
        self.assertIs(event.raw, raw)
        self.assertEqual(json.loads(event.raw)["phase"], "warmup")

    def test_extra_or_missing_unused_fields_are_not_malformed(self):
        no_session = cap(1, 1)
        del no_session["session"]
        self.assertTrue(self.accepted(no_session))
        self.assertTrue(self.accepted(cap(2, 1, phase="T3", extra={"x": 1})))

    def test_integer_source_ts_is_accepted(self):
        self.assertTrue(self.accepted(cap(1, 1, source_ts=1760000000)))

    def test_remembered_finals_are_bounded(self):
        state = CaptionState(max_remembered_finals=3)
        for segment in range(1, 11):
            msg = cap(segment, 1, final=True)
            self.assertIsNotNone(state.apply(msg, json.dumps(msg)))
        stats = state.stats()
        self.assertEqual(stats["remembered_finals"], 3)
        self.assertEqual(stats["forgotten_upto"], 7)
        # A forgotten segment can never be reopened.
        self.assertIsNone(state.apply(cap(2, 9), "{}"))
        self.assertEqual(state.stats()["too_old"], 1)
        msg = cap(11, 1)
        self.assertIsNotNone(state.apply(msg, json.dumps(msg)))

    def test_active_segments_are_bounded(self):
        state = CaptionState(max_active_segments=2)
        for segment in (1, 2, 3):
            msg = cap(segment, 1)
            state.apply(msg, json.dumps(msg))
        segs = [json.loads(r)["segment"] for r in state.active_snapshot()]
        self.assertEqual(segs, [2, 3])  # lowest evicted

    def test_invalid_limits_rejected(self):
        for kwargs in ({"max_active_segments": 0}, {"max_remembered_finals": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CaptionState(**kwargs)


if __name__ == "__main__":
    unittest.main()
