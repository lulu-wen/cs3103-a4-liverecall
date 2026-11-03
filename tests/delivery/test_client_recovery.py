"""Recovery batching must preserve data and obey encoded message limits."""

import json
import unittest

from captions.history import history_message
from delivery.client_recovery import RecoveryPlan


class RecoveryPlanTests(unittest.TestCase):
    def test_empty_recovery_still_has_one_history(self):
        plan = RecoveryPlan.build([], [], max_message_bytes=100)
        self.assertEqual(list(plan.messages()), [history_message([])])

    def test_batches_preserve_all_finals_in_order_then_active(self):
        finals = [json.dumps({"segment": n, "final": True, "text": "你" * 10},
                             ensure_ascii=False) for n in range(7)]
        active = ['{"segment":8,"final":false}']
        limit = len(history_message(finals[:2]).encode("utf-8"))
        plan = RecoveryPlan.build(finals, active, max_message_bytes=limit)
        frames = list(plan.messages())
        self.assertEqual(len(plan.batches), 4)
        self.assertTrue(all(len(frame.encode("utf-8")) <= limit for frame in frames))
        self.assertEqual([item for frame in frames[:-1] for item in json.loads(frame)["items"]],
                         [json.loads(raw) for raw in finals])
        self.assertEqual(frames[-1], active[0])

    def test_single_final_exact_utf8_boundary(self):
        raw = '{"text":"字幕"}'
        limit = len(history_message([raw]).encode("utf-8"))
        self.assertEqual(list(RecoveryPlan.build([raw], [], max_message_bytes=limit).messages()),
                         [history_message([raw])])
        with self.assertRaises(ValueError):
            RecoveryPlan.build([raw], [], max_message_bytes=limit - 1)

    def test_preflight_rejects_late_oversized_item(self):
        with self.assertRaises(ValueError):
            RecoveryPlan.build(['{}', '"' + 'x' * 200 + '"'], [], max_message_bytes=100)
        with self.assertRaises(ValueError):
            RecoveryPlan.build(['{}'], ['x' * 101], max_message_bytes=100)

    def test_snapshot_is_independent_of_later_list_changes(self):
        finals, active = ['{}'], ['active']
        plan = RecoveryPlan.build(finals, active, max_message_bytes=100)
        finals.clear()
        active.clear()
        self.assertEqual(list(plan.messages()), [history_message(['{}']), 'active'])


if __name__ == "__main__":
    unittest.main()
