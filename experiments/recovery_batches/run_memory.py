"""Repeat the supplementary 300-second RSS observation on the new snapshot."""

import argparse
import csv
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

HELPERS = Path(__file__).resolve().parents[1] / "delivery_20261103"
sys.path.insert(0, str(HELPERS))
from run_evidence import samples


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    evidence = root / "evidence"
    print("Waiting for experiment lock: supplementary RSS", flush=True)
    with (root / "experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        command = [args.python, "-u", "run_demo.py", "--clients", "100", "--duration", "300",
                   "--runs", "1", "--slow-percent", "10", "--slow-ms", "500", "--port", str(port),
                   "--output-stem", str(evidence / "rss300"), "--quiet-server"]
        environment = {}
        for name, value in os.environ.items():
            if not name.startswith("LIVERECALL_"):
                environment[name] = value
        (evidence / "rss300_command.json").write_text(json.dumps(command, indent=2))
        with (evidence / "rss300.log").open("w") as log, \
                (evidence / "rss300_rss.csv").open("w", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["phase_elapsed_s", "server_pid", "server_elapsed_s", "rss_mib"])
            process = subprocess.Popen(command, cwd=root / "current", env=environment,
                                       stdout=log, stderr=subprocess.STDOUT)
            started = time.monotonic()
            first_seen = {}
            last_notice = started
            while process.poll() is None:
                now = time.monotonic()
                for pid, rss in samples(process.pid):
                    if pid not in first_seen:
                        first_seen[pid] = now
                    writer.writerow([round(now-started, 3), pid,
                                     round(now-first_seen[pid], 3), rss])
                csv_file.flush()
                if now - last_notice >= 30:
                    print(f"RSS running: {now-started:.0f}s", flush=True)
                    last_notice = now
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError("RSS workload failed; inspect rss300.log")
        print("SUPPLEMENTARY RSS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
