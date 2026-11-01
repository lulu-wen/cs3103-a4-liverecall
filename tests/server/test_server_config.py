"""Delivery configuration parsing and startup wiring."""

from contextlib import redirect_stderr
import io
import os
import unittest
from unittest.mock import patch

import solution_server as app


class ServerConfigTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_defaults_preserve_required_start_command(self):
        args = app.build_parser().parse_args(["--port", "8765"])
        self.assertEqual(args.port, 8765)
        self.assertEqual(args.queue_max_messages, 256)
        self.assertEqual(args.queue_max_bytes, 1024 * 1024)
        self.assertEqual(args.send_timeout, 10.0)
        self.assertEqual(args.close_timeout, 3.0)
        self.assertFalse(args.naive)
        self.assertEqual(args.experiment_send_delay_ms, 0.0)

    def test_naive_flag_disables_proactive_policy(self):
        args = app.build_parser().parse_args(["--naive"])
        self.assertTrue(args.naive)

    def test_experiment_send_delay_parses_milliseconds_to_seconds(self):
        args = app.build_parser().parse_args(["--experiment-send-delay-ms", "75"])
        self.assertAlmostEqual(args.experiment_send_delay_ms, 0.075)
        for bad in ("-1", "nan", "inf", "abc"):
            with self.subTest(bad=bad), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    app.build_parser().parse_args(["--experiment-send-delay-ms", bad])

    def test_explicit_cli_values_and_units(self):
        args = app.build_parser().parse_args([
            "--queue-max-messages", "32", "--queue-max-bytes", "8192",
            "--send-timeout", "0.25", "--close-timeout", "1.5",
        ])
        self.assertEqual(args.queue_max_messages, 32)
        self.assertEqual(args.queue_max_bytes, 8192)
        self.assertEqual(args.send_timeout, 0.25)
        self.assertEqual(args.close_timeout, 1.5)

    def test_environment_defaults_are_typed(self):
        os.environ.update({
            "LIVERECALL_QUEUE_MAX_MESSAGES": "64",
            "LIVERECALL_QUEUE_MAX_BYTES": "4096",
            "LIVERECALL_SEND_TIMEOUT": "2.5",
            "LIVERECALL_CLOSE_TIMEOUT": "0.5",
        })
        args = app.build_parser().parse_args([])
        self.assertEqual(args.queue_max_messages, 64)
        self.assertEqual(args.queue_max_bytes, 4096)
        self.assertEqual(args.send_timeout, 2.5)
        self.assertEqual(args.close_timeout, 0.5)

    def test_cli_overrides_even_invalid_environment_value(self):
        os.environ["LIVERECALL_QUEUE_MAX_MESSAGES"] = "invalid"
        args = app.build_parser().parse_args(["--queue-max-messages", "16"])
        self.assertEqual(args.queue_max_messages, 16)

    def test_invalid_cli_values_fail_before_startup(self):
        values = {
            "--queue-max-messages": ("0", "-1", "1.5", "abc"),
            "--queue-max-bytes": ("0", "-1", "1.5", "abc"),
            "--send-timeout": ("0", "-1", "nan", "inf", "-inf", "abc"),
            "--close-timeout": ("0", "-1", "nan", "inf", "-inf", "abc"),
        }
        for flag, bad_values in values.items():
            for value in bad_values:
                with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        app.build_parser().parse_args([f"{flag}={value}"])
                    self.assertEqual(caught.exception.code, 2)

    def test_invalid_environment_is_validated(self):
        for key, value in (
            ("LIVERECALL_QUEUE_MAX_MESSAGES", "0"),
            ("LIVERECALL_QUEUE_MAX_BYTES", "-1"),
            ("LIVERECALL_SEND_TIMEOUT", "nan"),
            ("LIVERECALL_CLOSE_TIMEOUT", "inf"),
        ):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}), \
                    redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    app.build_parser().parse_args([])
                self.assertEqual(caught.exception.code, 2)

    def test_keepalive_defaults_cli_and_environment(self):
        args = app.build_parser().parse_args([])
        self.assertEqual((args.keepalive_interval, args.keepalive_timeout), (2.0, 5.0))
        args = app.build_parser().parse_args(
            ["--keepalive-interval", "0.5", "--keepalive-timeout", "1.5"])
        self.assertEqual((args.keepalive_interval, args.keepalive_timeout), (0.5, 1.5))
        with patch.dict(os.environ, {"LIVERECALL_KEEPALIVE_INTERVAL": "20",
                                     "LIVERECALL_KEEPALIVE_TIMEOUT": "20"}):
            args = app.build_parser().parse_args([])
        self.assertEqual((args.keepalive_interval, args.keepalive_timeout), (20.0, 20.0))

    def test_invalid_keepalive_values_fail_before_startup(self):
        for flag in ("--keepalive-interval", "--keepalive-timeout"):
            for value in ("0", "-1", "nan", "inf", "abc"):
                with self.subTest(flag=flag, value=value), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as caught:
                        app.build_parser().parse_args([f"{flag}={value}"])
                    self.assertEqual(caught.exception.code, 2)

    def test_server_options_use_keepalive_settings(self):
        with patch.multiple(app, KEEPALIVE_INTERVAL=1.0, KEEPALIVE_TIMEOUT=4.0,
                            CLIENT_CLOSE_TIMEOUT=2.0):
            self.assertEqual(app.server_options(), {
                "max_size": 2**20, "ping_interval": 1.0, "ping_timeout": 4.0,
                "close_timeout": 2.0,
            })

    def test_main_applies_settings_before_event_loop_starts(self):
        captured = []

        async def fake_main_async(port):
            captured.append((port, app.QUEUE_MAX_MESSAGES, app.QUEUE_MAX_BYTES,
                             app.CLIENT_SEND_TIMEOUT, app.CLIENT_CLOSE_TIMEOUT,
                             app.KEEPALIVE_INTERVAL, app.KEEPALIVE_TIMEOUT))

        # Restore process-global settings so existing WebSocket tests stay independent.
        with patch.multiple(app, QUEUE_MAX_MESSAGES=256, QUEUE_MAX_BYTES=1048576,
                            CLIENT_SEND_TIMEOUT=10.0, CLIENT_CLOSE_TIMEOUT=3.0,
                            KEEPALIVE_INTERVAL=2.0, KEEPALIVE_TIMEOUT=5.0), \
                patch.object(app, "main_async", fake_main_async), \
                patch("sys.argv", ["solution_server.py", "--port", "9876",
                                   "--queue-max-messages", "7", "--queue-max-bytes", "2048",
                                   "--send-timeout", "0.5", "--close-timeout", "0.75",
                                   "--keepalive-interval", "1", "--keepalive-timeout", "3"]):
            app.main()
        self.assertEqual(captured, [(9876, 7, 2048, 0.5, 0.75, 1.0, 3.0)])


if __name__ == "__main__":
    unittest.main()
