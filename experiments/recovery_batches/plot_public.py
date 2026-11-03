"""Charts for the accepted public reruns; historical comparison is labeled."""

import csv
import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE / "evidence"
CURRENT = json.loads((HERE / "public_summary.json").read_text())
HISTORICAL = json.loads((HERE.parent / "delivery_20261103/analysis_summary.json").read_text())
BLUE = "#143b61"
GRAY = "#a9b8c5"


def errors(measured):
    return [[measured["median"] - measured["min"]],
            [measured["max"] - measured["median"]]]


def plot_t3():
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.3), constrained_layout=True)
    for index, group in enumerate(("normal", "slow")):
        for version, measured, offset, color in (
            ("Before acef9a3", HISTORICAL["before_t3"][group], -.18, GRAY),
            ("Recovery snapshot", CURRENT["tests"]["t3"]["groups"][group], .18, BLUE),
        ):
            axes[0].bar(index+offset, measured["p95_ms"]["median"], width=.36,
                        yerr=errors(measured["p95_ms"]), capsize=3, color=color,
                        label=version if index == 0 else None)
            axes[1].bar(index+offset, measured["final_completeness_pct"]["median"],
                        width=.36, color=color,
                        yerr=errors(measured["final_completeness_pct"]), capsize=3)
    axes[0].set(yscale="log", ylabel="P95 Caption Age (ms)")
    axes[0].legend(fontsize=8)
    axes[1].set(ylabel="Final Caption Completeness (%)", ylim=(0, 110))
    for ax in axes:
        ax.set(xticks=[0, 1], xticklabels=["Normal clients", "Slow clients"])
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle("T3: three-run medians and observed ranges · whole-version comparison", fontsize=11)
    fig.savefig(HERE / "t3_comparison.png", dpi=180)
    plt.close(fig)


def plot_overview():
    names = [name for name in ("t1", "t2", "t4", "t5") if name in CURRENT["tests"]]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
    positions = list(range(len(names)))
    for metric, offset, color, label in (("p95_ms", -.18, BLUE, "P95"),
                                        ("p99_ms", .18, GRAY, "P99")):
        for index, name in enumerate(names):
            value = CURRENT["tests"][name]["metrics"][metric]
            axes[0].bar(index+offset, value["median"], width=.36, color=color,
                        yerr=errors(value), capsize=3, label=label if index == 0 else None)
    axes[0].set(yscale="log", ylabel="Caption Age (ms)")
    axes[0].legend(fontsize=8)
    for metric, offset, color, label in (("fresh_pct", -.18, BLUE, "Fresh Caption Ratio"),
            ("final_completeness_pct", .18, GRAY, "Final Caption Completeness")):
        values = []
        for name in names:
            values.append(CURRENT["tests"][name]["metrics"][metric]["median"])
        axes[1].bar([x+offset for x in positions], values, width=.36, color=color, label=label)
    axes[1].set(ylabel="Ratio (%)", ylim=(0, 110))
    axes[1].legend(fontsize=8, loc="lower left")
    for ax in axes:
        ax.set(xticks=positions, xticklabels=[name.upper() for name in names])
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle("Recovery snapshot: independent public workloads, three runs each", fontsize=11)
    fig.savefig(HERE / "public_workloads.png", dpi=180)
    plt.close(fig)


def plot_t5():
    paths = sorted(EVIDENCE.glob("t5_run*.csv"))
    if len(paths) != 3:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), constrained_layout=True)
    for index, path in enumerate(paths):
        ages = []
        with path.open(newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                if row["event_type"] == "CAPTION_UPDATE" and row["status"] == "accepted":
                    ages.append(float(row["latency_ms"]))
        ages.sort()
        cumulative = [(n+1) / len(ages) for n in range(len(ages))]
        axes[0].plot(ages, cumulative, label=f"Run {index+1}")
    axes[0].axvline(500, color="black", linestyle="--", linewidth=1, label="500 ms")
    axes[0].set(xscale="log", xlabel="Caption Age (ms)", ylabel="Cumulative fraction")
    axes[0].legend(fontsize=8)
    metrics = CURRENT["tests"]["t5"]["metrics"]
    keys = ["fresh_pct", "fresh_final_pct", "final_completeness_pct"]
    values = [metrics[key]["median"] for key in keys]
    axes[1].bar(range(3), values, color=[BLUE, "#52738e", GRAY])
    axes[1].set(xticks=range(3), xticklabels=["Fresh\nupdates", "Fresh\nfinals", "Final\ncomplete"],
                ylabel="Ratio (%)", ylim=(0, 110))
    for index, value in enumerate(values):
        axes[1].text(index, value+1, f"{value:.2f}", ha="center", fontsize=9)
    fig.suptitle("T5 recovery snapshot: 120 ms ±30 ms jitter / 3% loss", fontsize=11)
    fig.savefig(HERE / "t5_receive_age.png", dpi=180)
    plt.close(fig)


def plot_memory():
    long_path = EVIDENCE / "rss300_rss.csv"
    if not long_path.exists():
        return
    with (EVIDENCE / "t3_rss.csv").open(newline="") as csv_file:
        t3_rows = list(csv.DictReader(csv_file))
    peaks = {}
    for row in t3_rows:
        pid = row["server_pid"]
        peaks[pid] = max(peaks.get(pid, 0), float(row["rss_mib"]))
    with long_path.open(newline="") as csv_file:
        long_rows = list(csv.DictReader(csv_file))
    early, late, after = [], [], []
    last_time = max(float(row["server_elapsed_s"]) for row in long_rows)
    for row in long_rows:
        elapsed, rss = float(row["server_elapsed_s"]), float(row["rss_mib"])
        if 10 <= elapsed <= 30:
            early.append(rss)
        if elapsed >= last_time - 30:
            late.append(rss)
        if elapsed >= 60:
            after.append((elapsed, rss))
    mean_time = statistics.mean(t for t, rss in after)
    mean_rss = statistics.mean(rss for t, rss in after)
    numerator = sum((t-mean_time)*(rss-mean_rss) for t, rss in after)
    denominator = sum((t-mean_time)**2 for t, rss in after)
    observation = {"t3_peak_mib_by_pid": peaks, "long_samples": len(long_rows),
        "long_duration_s": last_time, "early_median_mib": statistics.median(early),
        "late_median_mib": statistics.median(late),
        "after_60s_slope_mib_per_minute": numerator / denominator * 60,
        "long_peak_mib": max(float(row["rss_mib"]) for row in long_rows)}
    (HERE / "memory_summary.json").write_text(json.dumps(observation, indent=2)+"\n")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4), constrained_layout=True)
    old = HISTORICAL["before_t3"]["rss"]["peak_mib_by_run"]
    for index, values in enumerate((old, list(peaks.values()))):
        value = statistics.median(values)
        axes[0].bar(index, value, color=[GRAY, BLUE][index],
                    yerr=[[value-min(values)], [max(values)-value]], capsize=4)
    axes[0].set(xticks=[0, 1], xticklabels=["Before acef9a3", "Recovery snapshot"],
                ylabel="Peak server RSS (MiB)")
    axes[1].plot([float(r["server_elapsed_s"]) for r in long_rows],
                 [float(r["rss_mib"]) for r in long_rows], color=BLUE)
    axes[1].set(xlabel="Server elapsed time (s)", ylabel="RSS (MiB)")
    fig.suptitle("T3 peaks: three runs · supplementary RSS trace: one 300 s run", fontsize=11)
    fig.savefig(HERE / "rss_observation.png", dpi=180)
    plt.close(fig)


plot_t3()
plot_overview()
plot_t5()
plot_memory()
print("Rendered available public-test and memory charts")
