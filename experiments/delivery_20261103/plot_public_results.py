"""Render measured public results; report T5 uses the accepted repeat batch."""

import csv
import json
from pathlib import Path
from statistics import median

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import ScalarFormatter

ROOT = Path(__file__).resolve().parent
FIGURES = ROOT / "figures"
FIGURES.mkdir(exist_ok=True)
data = json.loads((ROOT / "public_summary.json").read_text())
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.facecolor": "white"})
COLORS = ["#32658a", "#dc762d", "#30845a"]


def save(fig, stem):
    fig.savefig(FIGURES / f"{stem}.png", dpi=220, bbox_inches="tight")
    fig.savefig(FIGURES / f"{stem}.svg", bbox_inches="tight")
    plt.close(fig)


def grouped(ax, tests, metrics, labels, percent=False):
    x = np.arange(len(tests))
    width = 0.23
    for index, (metric, label) in enumerate(zip(metrics, labels)):
        values = [[run[metric] for run in data[test]["runs"]] for test in tests]
        medians = [median(items) for items in values]
        error = [[center-min(items) for center, items in zip(medians, values)],
                 [max(items)-center for center, items in zip(medians, values)]]
        bars = ax.bar(x + (index-1)*width, medians, width, label=label,
                      color=COLORS[index], yerr=error, capsize=3,
                      error_kw={"linewidth": 0.8})
        if percent:
            for rect, value in zip(bars, medians):
                ax.text(rect.get_x()+rect.get_width()/2, value+1.4, f"{value:.1f}",
                        ha="center", va="bottom", fontsize=10, rotation=90)
    ax.set_xticks(x, [test.upper() + "\n(n=3)" for test in tests])
    ax.grid(axis="y", alpha=0.18)
    ax.set_axisbelow(True)
    if percent:
        ax.set_ylim(0, 116)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_ylabel("Ratio (%)")
    else:
        ax.set_yscale("log")
        ax.set_ylabel("Live age (ms, log scale)")
    ax.legend(loc="upper left", fontsize=10, frameon=False)


fig, axes = plt.subplots(1, 2, figsize=(10, 4.1), layout="constrained")
tests = ["t1", "t2", "t4", "t5"]
grouped(axes[0], tests, ["p50_ms", "p95_ms", "p99_ms"], ["P50", "P95", "P99"])
axes[0].set_title("Public-workload latency")
grouped(axes[1], tests, ["fresh_pct", "fresh_final_pct", "final_completeness_pct"],
        ["Fresh live (≤500 ms)", "Fresh finals (≤500 ms)", "Final completeness"], True)
axes[1].set_title("Freshness and final receipt")
axes[1].legend(loc="lower left", fontsize=9, framealpha=0.9)
save(fig, "public_workloads")

fig, axes = plt.subplots(1, 2, figsize=(10, 4.1), layout="constrained")
max_age = 0
for number in (1, 2, 3):
    with (ROOT / data["t5"]["source_directory"] / f"t5_run{number:02d}.csv").open(newline="") as file:
        values = sorted(float(row["latency_ms"]) for row in csv.DictReader(file)
                        if row["event_type"] == "CAPTION_UPDATE" and row["status"] == "accepted"
                        and row["latency_ms"])
    assert values
    max_age = max(max_age, values[-1])
    axes[0].plot(values, np.arange(1, len(values)+1)/len(values)*100,
                 label=f"Run {number}", color=COLORS[number-1], linewidth=1.6)
axes[0].axvline(500, color="#555555", linestyle="--", linewidth=1,
                label="500 ms freshness target")
axes[0].set_xscale("log")
axes[0].set_xlim(100, max(1500, max_age*1.05))
axes[0].set_xticks([100, 250, 500, 1000, 2000, 5000])
axes[0].xaxis.set_major_formatter(ScalarFormatter())
axes[0].set_ylim(0, 101)
axes[0].set_xlabel("Live age (ms, log scale)")
axes[0].set_ylabel("Cumulative live updates (%)")
axes[0].set_title("T5 receive-age distributions")
axes[0].legend(loc="lower right", fontsize=9)
axes[0].grid(alpha=0.18)
x = np.arange(3)
for index, (metric, label) in enumerate((("fresh_pct", "Fresh live"),
                                       ("fresh_final_pct", "Fresh finals"),
                                       ("final_completeness_pct", "Final completeness"))):
    values = [run[metric] for run in data["t5"]["runs"]]
    bars = axes[1].bar(x+(index-1)*0.24, values, 0.24, color=COLORS[index], label=label)
    for rect, value in zip(bars, values):
        axes[1].text(rect.get_x()+rect.get_width()/2, value+1, f"{value:.1f}",
                     ha="center", fontsize=10, rotation=90)
axes[1].set_xticks(x, ["Run 1", "Run 2", "Run 3"])
axes[1].set_ylim(0, 116)
axes[1].set_yticks([0, 25, 50, 75, 100])
axes[1].set_ylabel("Ratio (%)")
axes[1].set_title("T5 freshness and final receipt")
axes[1].legend(loc="lower left", fontsize=9)
axes[1].grid(axis="y", alpha=0.18)
axes[1].set_axisbelow(True)
save(fig, "t5_receive_age")
print(f"Rendered public overview and T5 distributions from accepted repeat; maximum live age {max_age:.2f} ms.")
