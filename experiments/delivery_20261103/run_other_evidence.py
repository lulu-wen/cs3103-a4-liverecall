"""Remaining public workloads; optional privileged T5 uses an isolated netns."""

import argparse
import csv
import fcntl
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import time

from run_evidence import samples, group_metrics


def run_phase(root, python, label, clients, duration, extra):
    evidence = root / "evidence"
    source = root / "current"
    env = {key: value for key, value in os.environ.items() if not key.startswith("LIVERECALL_")}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    stem = evidence / label
    command = [python, "-u", "run_demo.py", "--clients", str(clients), "--duration", str(duration),
               "--runs", "3", "--port", str(port), "--output-stem", str(stem),
               "--quiet-server", *extra]
    print("START " + label + ": " + " ".join(command), flush=True)
    started = time.monotonic()
    pid_start = {}
    with (evidence / f"{label}.log").open("w") as log, (evidence / f"{label}_rss.csv").open("w", newline="") as rss:
        writer = csv.writer(rss)
        writer.writerow(["phase_elapsed_s", "server_pid", "server_elapsed_s", "rss_mib"])
        proc = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
        last_notice = started
        while proc.poll() is None:
            now = time.monotonic()
            for pid, memory in samples(proc.pid):
                pid_start.setdefault(pid, now)
                writer.writerow([round(now-started, 3), pid, round(now-pid_start[pid], 3), round(memory, 4)])
            rss.flush()
            if now-last_notice >= 30:
                print(f"RUNNING {label}: {now-started:.0f}s", flush=True)
                last_notice = now
            time.sleep(0.5)
        if proc.returncode:
            raise RuntimeError(f"{label} failed; inspect its log")
    segments = max(4, math.floor(max(3, duration-3)/0.75))
    runs = []
    for run in (1, 2, 3):
        public = json.loads(Path(f"{stem}_run{run:02d}.json").read_text())
        with Path(f"{stem}_run{run:02d}.csv").open(newline="") as file:
            rows = list(csv.DictReader(file))
        result = {"run": run, "public": public,
                  "all_clients": group_metrics(rows, 0, segments, segments*3)}
        if label == "t4":
            grouped = [{**row, "slow": row["disconnecting"]} for row in rows]
            result["unaffected"] = group_metrics(grouped, 0, segments, segments*3)
            result["disconnected"] = group_metrics(grouped, 1, segments, segments*3)
        runs.append(result)
    summary = {"command": command, "clients": clients, "duration_seconds": duration,
               "expected_segments": segments, "runs": runs,
               "elapsed_seconds": round(time.monotonic()-started, 2),
               "source_commit": (source/"REVISION.txt").read_text().strip(),
               "network_qdisc": subprocess.check_output(["tc", "qdisc", "show", "dev", "lo"], text=True).strip()}
    (evidence/f"{label}_evidence.json").write_text(json.dumps(summary, indent=2))
    print("DONE " + label, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--mode", choices=("remaining", "t5", "t5-child"), default="remaining")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    (root/"evidence").mkdir(exist_ok=True)
    if args.mode == "t5-child":
        # Refuse to shape traffic if not in a separate network namespace.
        if os.geteuid() != 0 or os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
            raise SystemExit("T5 requires root inside a separate network namespace; host network unchanged")
        subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
        source = root/"current"
        try:
            with (root/"evidence/t5_profile.log").open("w") as log:
                subprocess.run(["bash", "network_profiles/delay_loss.sh", "lo"], cwd=source,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
            run_phase(root, args.python, "t5", 100, 40, [])
        finally:
            with (root/"evidence/t5_reset.log").open("w") as log:
                subprocess.run(["bash", "network_profiles/reset.sh", "lo"], cwd=source,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        print("T5 complete; isolated namespace will now exit", flush=True)
        return
    print("Waiting for exclusive experiment lock (runs will not overlap)...", flush=True)
    with (root/"experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.mode == "t5":
            if os.geteuid() != 0:
                raise SystemExit("Run this mode with sudo in your terminal; do not send passwords in chat")
            subprocess.run(["unshare", "--net", args.python, str(Path(__file__).resolve()),
                            "--python", args.python, "--mode", "t5-child"], check=True)
        else:
            for label, clients, duration, extra in (
                ("t1", 20, 20, []), ("t2", 100, 40, []),
                ("t4", 100, 60, ["--disconnect-percent", "20", "--disconnect-at", "15", "--disconnect-seconds", "5"]),
            ):
                run_phase(root, args.python, label, clients, duration, extra)
    print("REQUESTED EXPERIMENTS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
