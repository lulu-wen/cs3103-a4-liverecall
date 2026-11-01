import sys
import unittest
from types import SimpleNamespace

import run_demo


class RunDemoServerCommandTests(unittest.TestCase):
    def test_default_server_command_is_optimized(self):
        args = SimpleNamespace(port=9876, naive_server=False, experiment_send_delay_ms=0.0)
        self.assertEqual(
            run_demo.server_command(args),
            [sys.executable, "-u", "solution_server.py", "--port", "9876"],
        )

    def test_naive_server_flag_is_forwarded_only_to_server(self):
        args = SimpleNamespace(port=8765, naive_server=True, experiment_send_delay_ms=0.0)
        self.assertEqual(
            run_demo.server_command(args),
            [sys.executable, "-u", "solution_server.py", "--port", "8765", "--naive"],
        )

    def test_experiment_send_delay_is_forwarded_only_when_nonzero(self):
        args = SimpleNamespace(port=8765, naive_server=False, experiment_send_delay_ms=75.0)
        self.assertEqual(
            run_demo.server_command(args),
            [sys.executable, "-u", "solution_server.py", "--port", "8765",
             "--experiment-send-delay-ms", "75.0"],
        )


if __name__ == "__main__":
    unittest.main()
