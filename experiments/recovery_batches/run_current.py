"""Run the unchanged public tests against an isolated exported source snapshot."""

import argparse
import fcntl
import json
from pathlib import Path
import platform
import subprocess
import sys

HELPERS = Path(__file__).resolve().parents[1] / "delivery_20261103"
sys.path.insert(0, str(HELPERS))
from run_other_evidence import run_phase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)
    current = root / "current"
    metadata = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source": (current / "REVISION.txt").read_text().strip(),
        "network": "same-host loopback, no impairment for T1-T4",
        "notes": "Independent full public rerun; unchanged instructor measurement tools",
    }
    (evidence / "rerun_metadata.json").write_text(json.dumps(metadata, indent=2))
    print("Waiting for exclusive experiment lock", flush=True)
    with (root / "experiment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with (evidence / "unit-tests.log").open("w") as log:
            subprocess.run([args.python, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                           cwd=current, stdout=log, stderr=subprocess.STDOUT, check=True)
        with (evidence / "startup-check.log").open("w") as log:
            subprocess.run([args.python, "submission_check.py"], cwd=current,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        for version in ("before", "current"):
            command = [args.python, "experiments/recovery_batches/run_validation.py",
                       "--output", str(evidence / f"recovery_{version}.json")]
            with (evidence / f"recovery_{version}.log").open("w") as log:
                subprocess.run(command, cwd=root / version, stdout=log,
                               stderr=subprocess.STDOUT, check=True)
            print("DONE recovery validation: " + version, flush=True)
        workloads = [
            ("t1", 20, 20, []),
            ("t2", 100, 40, []),
            ("t3", 100, 60, ["--slow-percent", "10", "--slow-ms", "500"]),
            ("t4", 100, 60, ["--disconnect-percent", "20", "--disconnect-at", "15",
                              "--disconnect-seconds", "5"]),
        ]
        for label, clients, duration, extra in workloads:
            run_phase(root, args.python, label, clients, duration, extra)
    print("NONPRIVILEGED EXPERIMENTS COMPLETE", flush=True)


if __name__ == "__main__":
    main()
