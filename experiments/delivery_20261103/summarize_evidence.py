"""Summarize recorded runs and create publication-ready static figures."""

import csv
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "evidence"
FIGURES = ROOT / "figures"


def stats(values):
    values = [value for value in values if isinstance(value, (int, float))]
    return {"median": statistics.median(values), "min": min(values), "max": max(values)} if values else None


def rss_rows(label):
    with (RAW / f"{label}_rss.csv").open(newline="") as file:
        return [{"pid": int(row["server_pid"]), "seconds": float(row["server_elapsed_s"]),
                 "mib": float(row["rss_mib"])} for row in csv.DictReader(file)]


def summarize():
    source = json.loads((RAW / "summary.json").read_text())
    result = {}
    keys = ("p50_ms", "p95_ms", "p99_ms", "fresh_pct", "fresh_final_pct",
            "final_completeness_pct", "live_update_coverage_pct", "connections",
            "connection_errors", "recovered_items")
    for label, phase in source.items():
        data = {"commit": phase["commit"], "run_count": len(phase["runs"]),
                "command": phase["command"], "expected_segments": phase["expected_segments"]}
        for role in ("normal", "slow"):
            data[role] = {key: stats([run[role].get(key) for run in phase["runs"]]) for key in keys}
        for key in ("duplicates", "stale", "invalid_history_nonfinal", "connections",
                    "connection_errors", "final_completeness_pct", "recovered_history_items",
                    "p50_ms", "p95_ms", "p99_ms", "fresh_pct", "fresh_final_pct",
                    "recovery_p50_ms", "recovery_p95_ms"):
            data.setdefault("public", {})[key] = stats([run["public"].get(key) for run in phase["runs"]])
        rows = rss_rows(label)
        pids = list(dict.fromkeys(row["pid"] for row in rows))
        data["rss"] = {"server_processes": len(pids),
                       "peak_mib_by_run": [max(row["mib"] for row in rows if row["pid"] == pid) for pid in pids],
                       "samples": len(rows)}
        if label == "current_long":
            duration = max(row["seconds"] for row in rows)
            early = [row["mib"] for row in rows if 10 <= row["seconds"] <= 30]
            late = [row["mib"] for row in rows if row["seconds"] >= duration - 30]
            warm = [row for row in rows if row["seconds"] >= 60]
            slope = float(np.polyfit([row["seconds"] / 60 for row in warm], [row["mib"] for row in warm], 1)[0])
            data["rss"].update(duration_seconds=duration, early_10_30s_median_mib=statistics.median(early),
                               last_30s_median_mib=statistics.median(late),
                               after_60s_slope_mib_per_minute=slope,
                               after_60s_min_mib=min(row["mib"] for row in warm),
                               after_60s_max_mib=max(row["mib"] for row in warm))
        result[label] = data
    (ROOT / "analysis_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def figures(summary):
    FIGURES.mkdir(exist_ok=True)
    plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titlesize": 10, "axes.labelsize": 9, "legend.fontsize": 8,
                         "pdf.fonttype": 42, "ps.fonttype": 42})
    colors = {"before_t3": "#32658A", "current_t3": "#CF6A28"}
    labels = {"before_t3": "Before (acef9a3)", "current_t3": "Current (6dbd7da)"}
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.55), layout="constrained")
    x = np.arange(2)
    for index, version in enumerate(colors):
        offset = (-0.18, 0.18)[index]
        values = [summary[version][role]["p95_ms"] for role in ("normal", "slow")]
        y = [value["median"] for value in values]
        errors = [[value["median"] - value["min"] for value in values],
                  [value["max"] - value["median"] for value in values]]
        axes[0].bar(x + offset, y, 0.32, color=colors[version], label=labels[version],
                    yerr=errors, capsize=3, error_kw={"elinewidth": 0.8})
        for pos, value in zip(x + offset, y):
            axes[0].annotate(f"{value:,.1f}", (pos, value), xytext=(0, 5),
                             textcoords="offset points", ha="center", fontsize=8)
        completeness = [summary[version][role]["final_completeness_pct"] for role in ("normal", "slow")]
        y2 = [value["median"] for value in completeness]
        axes[1].bar(x + offset, y2, 0.32, color=colors[version],
                    yerr=[[v["median"] - v["min"] for v in completeness],
                          [v["max"] - v["median"] for v in completeness]], capsize=3,
                    error_kw={"elinewidth": 0.8})
        for pos, value in zip(x + offset, y2):
            axes[1].text(pos, value + 1.8, f"{value:.1f}%", ha="center", fontsize=8)
    axes[0].set(yscale="log", ylabel="P95 live-caption age (ms; log scale)",
                xticks=x, xticklabels=["90 normal clients", "10 slow clients"], title="Live-caption tail latency")
    axes[0].set_ylim(top=max(summary[v][r]["p95_ms"]["max"] for v in colors for r in ("normal", "slow")) * 4)
    axes[0].legend(loc="upper left", frameon=False)
    axes[1].set(ylabel="Unique final completeness (%)", ylim=(0, 112),
                xticks=x, xticklabels=["90 normal clients", "10 slow clients"], title="Finals received by the 60 s cutoff")
    for axis in axes:
        axis.grid(axis="y", alpha=0.18)
        axis.set_axisbelow(True)
    fig.savefig(FIGURES / "t3_comparison.png", dpi=260)
    fig.savefig(FIGURES / "t3_comparison.svg")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.55), layout="constrained")
    for label, color in colors.items():
        rows = rss_rows(label)
        pids = list(dict.fromkeys(row["pid"] for row in rows))
        for index, pid in enumerate(pids):
            group = [row for row in rows if row["pid"] == pid]
            axes[0].plot([row["seconds"] for row in group], [row["mib"] for row in group],
                         color=color, alpha=0.7, linewidth=1,
                         label=labels[label] if index == 0 else None)
    rows = rss_rows("current_long")
    axes[1].plot([row["seconds"] for row in rows], [row["mib"] for row in rows],
                 color=colors["current_t3"], linewidth=1.1)
    axes[0].set(title="T3: three independent runs/version", xlabel="Server elapsed time (s)", ylabel="Server RSS (MiB)")
    axes[0].legend(frameon=False)
    axes[1].set(title="Current version: 300 s observation", xlabel="Server elapsed time (s)", ylabel="Server RSS (MiB)")
    for axis in axes:
        axis.grid(alpha=0.18)
    fig.savefig(FIGURES / "rss_observation.png", dpi=260)
    fig.savefig(FIGURES / "rss_observation.svg")
    plt.close(fig)


if __name__ == "__main__":
    summary = summarize()
    figures(summary)
    print(json.dumps(summary, indent=2))
