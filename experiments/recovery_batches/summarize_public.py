"""Summarize reruns without changing the instructor's measurements."""

import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

HELPERS = Path(__file__).resolve().parents[1] / "delivery_20261103"
sys.path.insert(0, str(HELPERS))
from run_evidence import group_metrics


def median_range(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def summarize(evidence, output):
    result = {"source_snapshot": "f630bc443b7a", "tests": {}}
    for label in ("t1", "t2", "t3", "t4", "t5"):
        path = evidence / f"{label}_evidence.json"
        if not path.exists():
            continue
        phase = json.loads(path.read_text())
        metrics = {}
        keys = ["p50_ms", "p95_ms", "p99_ms", "fresh_pct", "fresh_final_pct",
                "final_completeness_pct", "normal_update_coverage_pct", "connection_errors",
                "duplicates", "stale", "recovered_history_items", "recovery_p95_ms"]
        for key in keys:
            values = []
            for run in phase["runs"]:
                if key in run["public"]:
                    values.append(run["public"][key])
            if values:
                metrics[key] = median_range(values)
        summary = {"clients": phase["clients"], "duration_seconds": phase["duration_seconds"],
                   "expected_segments": phase["expected_segments"], "metrics": metrics}
        if label == "t3":
            groups = {"normal": [], "slow": []}
            for run in phase["runs"]:
                csv_path = evidence / f"t3_run{run['run']:02d}.csv"
                with csv_path.open(newline="") as csv_file:
                    rows = list(csv.DictReader(csv_file))
                for name, flag in (("normal", 0), ("slow", 1)):
                    measured = group_metrics(rows, flag, phase["expected_segments"],
                                             phase["expected_segments"] * 3)
                    groups[name].append(measured)
            summary["groups"] = {}
            for name, runs in groups.items():
                group_summary = {}
                for key in runs[0]:
                    values = []
                    for run in runs:
                        if isinstance(run[key], (float, int)):
                            values.append(run[key])
                    if values:
                        group_summary[key] = median_range(values)
                summary["groups"][name] = group_summary
        result["tests"][label] = summary
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Wrote " + str(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("public_summary.json"))
    args = parser.parse_args()
    summarize(args.evidence, args.output)
