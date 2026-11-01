"""P4 overload-policy unit tests."""

import unittest

from delivery.client_monitor import DeliveryStats
from delivery.client_queue import QueueStats
from delivery.overload_policy import OverloadMode, OverloadPolicy


def stats(*, depth=0, pending_bytes=0, oldest=0.0, in_flight=False, in_flight_seconds=0.0,
          write_buffer_bytes=None):
    return DeliveryStats(
        queue=QueueStats(depth, pending_bytes, oldest, False),
        in_flight=in_flight,
        in_flight_bytes=100 if in_flight else 0,
        in_flight_seconds=in_flight_seconds,
        sent_messages=0,
        sent_payload_bytes=0,
        send_errors=0,
        send_timeouts=0,
        last_send_seconds=None,
        write_buffer_bytes=write_buffer_bytes,
    )


class OverloadPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = OverloadPolicy(max_messages=100, max_bytes=1000)

    def test_healthy_client_stays_normal(self):
        self.assertIs(self.policy.update(stats(depth=10, pending_bytes=100)), OverloadMode.NORMAL)

    def test_message_pressure_enters_degraded(self):
        self.assertIs(self.policy.update(stats(depth=50)), OverloadMode.DEGRADED)
        self.assertEqual(self.policy.transitions_to_degraded, 1)

    def test_byte_pressure_enters_degraded(self):
        self.assertIs(self.policy.update(stats(pending_bytes=500)), OverloadMode.DEGRADED)

    def test_old_backlog_enters_degraded(self):
        self.assertIs(self.policy.update(stats(oldest=0.5)), OverloadMode.DEGRADED)

    def test_blocked_send_enters_degraded(self):
        self.assertIs(
            self.policy.update(stats(in_flight=True, in_flight_seconds=0.5)),
            OverloadMode.DEGRADED,
        )

    def test_hysteresis_prevents_early_exit(self):
        self.policy.update(stats(depth=60))
        self.assertIs(self.policy.update(stats(depth=30)), OverloadMode.DEGRADED)
        self.assertEqual(self.policy.transitions_to_normal, 0)

    def test_recovered_client_returns_normal(self):
        self.policy.update(stats(depth=60))
        self.assertIs(self.policy.update(stats(depth=10)), OverloadMode.NORMAL)
        self.assertEqual(self.policy.transitions_to_normal, 1)

    def test_in_flight_must_recover_before_exit(self):
        self.policy.update(stats(depth=60))
        self.assertIs(
            self.policy.update(stats(depth=10, in_flight=True, in_flight_seconds=0.2)),
            OverloadMode.DEGRADED,
        )
        self.assertIs(
            self.policy.update(stats(depth=10, in_flight=True, in_flight_seconds=0.1)),
            OverloadMode.NORMAL,
        )

    def test_missing_transport_buffer_is_not_treated_as_pressure(self):
        self.assertIs(self.policy.update(stats(write_buffer_bytes=None)), OverloadMode.NORMAL)

    def test_policy_instances_are_independent(self):
        other = OverloadPolicy(max_messages=100, max_bytes=1000)
        self.policy.update(stats(depth=80))
        self.assertIs(self.policy.mode, OverloadMode.DEGRADED)
        self.assertIs(other.mode, OverloadMode.NORMAL)


if __name__ == "__main__":
    unittest.main()
