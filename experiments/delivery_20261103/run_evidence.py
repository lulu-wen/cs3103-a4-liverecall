"""Run unchanged public workloads and sample only our runner's server children.

Remote layout: before/, current/, evidence/, plus a shared Python interpreter.
This observer never changes host traffic control or instructor measurement code.
"""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import time


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * p / 100
    lo, hi = math.floor(index), math.ceil(index)
    return values[lo] + (values[hi] - values[lo]) * (index - lo)


def group_metrics(rows, slow, expected_finals, expected_updates):
    subset = [row for row in rows if row.get("slow") == str(slow)]
    clients = {row["client_id"] for row in subset if row["client_id"]}
    live = [row for row in subset if row["event_type"] == "CAPTION_UPDATE"
            and row["status"] == "accepted"]
    latencies = [float(row["latency_ms"]) for row in live if row["latency_ms"]]
    finals = {}
    for row in subset:
        if (row["event_type"] in ("CAPTION_UPDATE", "HISTORY_ITEM")
                and row["status"] in ("accepted", "recovered") and row["final"] == "1"):
            key = (row["client_id"], int(row["segment"]))
            latency = float(row["latency_ms"]) if row["latency_ms"] else float("inf")
            finals[key] = min(finals.get(key, float("inf")), latency)
    revisions = {(row["client_id"], row["segment"], row["revision"]) for row in live}
    denominator = len(clients) * expected_finals
    return {
        "clients": len(clients),
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "fresh_pct": 100 * sum(value <= 500 for value in latencies) / len(latencies) if latencies else None,
        "final_completeness_pct": 100 * len(finals) / denominator if denominator else None,
        "fresh_final_pct": 100 * sum(value <= 500 for value in finals.values()) / denominator if denominator else None,
        "live_update_coverage_pct": 100 * len(revisions) / (len(clients) * expected_updates) if clients else None,
        "unique_finals": len(finals), "expected_finals": denominator,
        "recovered_items": sum(row["event_type"] == "HISTORY_ITEM" and row["status"] == "recovered" for row in subset),
        "connections": sum(row["event_type"] == "CONNECT" for row in subset),
        "connection_errors": sum(row["event_type"] == "CONNECT_ERROR" for row in subset),
    }


def samples(runner_pid):
    try:
        children = Path(f"/proc/{runner_pid}/task/{runner_pid}/children").read_text().split()
    except OSError:
        return []
    found = []
    for child in children:
        try:
            directory = Path(f"/proc/{child}")
            if b"solution_server.py" not in (directory / "cmdline").read_bytes().split(b"\0"):
                continue
            status = (directory / "status").read_text().splitlines()
            rss = next(int(line.split()[1]) / 1024 for line in status if line.startswith("VmRSS:"))
            found.append((int(child), rss))
        except (OSError, StopIteration):
            continue
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    root = Path.cwd()
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("LIVERECALL_"):
            del env[key]
    metadata = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "os_release": Path("/etc/os-release").read_text(), "architecture": platform.machine(),
        "cpu_count": os.cpu_count(), "kernel": platform.release(), "loadavg_start": os.getloadavg(),
        "python": subprocess.check_output([args.python, "--version"], text=True).strip(),
        "websockets": subprocess.check_output([args.python, "-c", "import websockets;print(websockets.__version__)"], text=True).strip(),
        "rss_interval_seconds": 0.5, "network": "same-host loopback; no traffic-control changes",
        "versions": {},
        "common_settings": {"queue_max_messages": 256, "queue_max_bytes": 1048576,
                            "send_timeout_seconds": 10, "close_timeout_seconds": 3},
        "notes": "Before/after compares complete default versions, not an isolated single optimization.",
    }
    for label in ("before", "current"):
        source = root / label
        metadata["versions"][label] = {
            "commit": (source / "REVISION.txt").read_text().strip(),
            "harness_sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
                               for name in ("run_demo.py", "load_clients.py", "analyse_results.py", "caption_source.py")},
        }
    if metadata["versions"]["before"]["harness_sha256"] != metadata["versions"]["current"]["harness_sha256"]:
        raise RuntimeError("Before/after public harness mismatch")
    metadata["versions"]["before"].update(keepalive_interval=20, keepalive_timeout=20)
    metadata["versions"]["current"].update(keepalive_interval=2, keepalive_timeout=5)
    (evidence / "metadata.json").write_text(json.dumps(metadata, indent=2))
    with (evidence / "unit-tests.log").open("w") as log:
        subprocess.run([args.python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=root / "current", env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    print("Current-version Ubuntu unit tests passed", flush=True)

    summary = {}
    phases = [("current_t3", "current", 60, 3), ("before_t3", "before", 60, 3),
              ("current_long", "current", 300, 1)]
    for label, version, duration, runs in phases:
        source = root / version
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        stem = str(evidence / label)
        command = [args.python, "-u", "run_demo.py", "--clients", "100", "--duration", str(duration),
                   "--runs", str(runs), "--slow-percent", "10", "--slow-ms", "500", "--port", str(port),
                   "--output-stem", stem, "--quiet-server"]
        print("START " + label + ": " + " ".join(command), flush=True)
        start = time.monotonic()
        pid_start = {}
        with (evidence / f"{label}.log").open("w") as log, (evidence / f"{label}_rss.csv").open("w", newline="") as rss:
            writer = csv.writer(rss)
            writer.writerow(["phase_elapsed_s", "server_pid", "server_elapsed_s", "rss_mib"])
            proc = subprocess.Popen(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT)
            last_notice = start
            while proc.poll() is None:
                now = time.monotonic()
                for pid, memory in samples(proc.pid):
                    pid_start.setdefault(pid, now)
                    writer.writerow([round(now - start, 3), pid, round(now - pid_start[pid], 3), round(memory, 4)])
                rss.flush()
                if now - last_notice >= 30:
                    print(f"RUNNING {label}: {now - start:.0f}s; server processes={len(pid_start)}", flush=True)
                    last_notice = now
                time.sleep(0.5)
            if proc.returncode:
                raise RuntimeError(f"{label} failed; see its log")
        segments = max(4, math.floor(max(3, duration - 1 - 2) / 0.75))
        run_results = []
        for run in range(1, runs + 1):
            suffix = f"_run{run:02d}" if runs > 1 else ""
            public = json.loads(Path(stem + suffix + ".json").read_text())
            with Path(stem + suffix + ".csv").open(newline="") as file:
                rows = list(csv.DictReader(file))
            run_results.append({"run": run, "public": public,
                                "normal": group_metrics(rows, 0, segments, segments * 3),
                                "slow": group_metrics(rows, 1, segments, segments * 3)})
        summary[label] = {"command": command, "commit": metadata["versions"][version]["commit"],
                          "duration_seconds": duration, "expected_segments": segments, "runs": run_results,
                          "elapsed_seconds": round(time.monotonic() - start, 2)}
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2))
        print("DONE " + label, flush=True)
    metadata["loadavg_end"] = os.getloadavg()
    (evidence / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print("ALL EXPERIMENTS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
