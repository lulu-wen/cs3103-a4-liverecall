"""Plot controlled queue outcomes and the deterministic retention capacity probe."""

import json
from pathlib import Path
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
data = json.loads((HERE / "comparison.json").read_text())
names = ["normal", "full_finals", "full_active", "near_final_capacity", "almost_final_capacity"]
labels = ["Normal", "Final FIFO\nfull", "Active state\nfull", "90% final-only\nservice load", "99% final-only\nservice load"]
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), constrained_layout=True)
for version, offset, color, label in (("fifo", -.18, "#a9b8c5", "FIFO reference"),
                                     ("priority", .18, "#143b61", "Final-aware")):
    for index, name in enumerate(names):
        rows = [r for r in data["rows"] if r["case"] == name and r["version"] == version]
        completeness = statistics.median(r["final_completeness_pct"] for r in rows)
        axes[0].bar(index+offset, completeness, width=.36, color=color,
                    label=label if index == 0 else None)
        if completeness == 0:
            axes[0].text(index+offset, 2, "0%", ha="center", fontsize=8)
        ages = [r["final_p95_ms"] for r in rows if r["final_p95_ms"] is not None]
        if ages:
            value = statistics.median(ages)
            axes[1].bar(index+offset, value, width=.36, color=color,
                        yerr=[[value-min(ages)], [max(ages)-value]], capsize=3)
        else:
            axes[1].text(index+offset, 3, "N/A", ha="center", fontsize=8)
axes[0].set(ylabel="Finals delivered on this connection (%)", ylim=(0, 112))
axes[0].legend(fontsize=8, loc="lower right")
axes[1].set(ylabel="Simulated final queue age P95 (ms)")
for ax in axes:
    ax.set(xticks=range(len(names)), xticklabels=labels)
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="y", alpha=.2)
    ax.set_axisbelow(True)
fig.suptitle("Virtual-time queue + policy experiment · three seeds · no network or recovery", fontsize=11)
fig.savefig(HERE / "scheduler_comparison.png", dpi=180)
plt.close(fig)

fig, ax = plt.subplots(figsize=(7, 3.2), constrained_layout=True)
rates = [r["finals_per_second"] for r in data["history_capacity"]]
ratios = [100*r["returned"]/r["required_recent"] for r in data["history_capacity"]]
ax.bar(range(len(rates)), ratios, color="#143b61")
for index, ratio in enumerate(ratios):
    ax.text(index, ratio+1, f"{ratio:.2f}%", ha="center")
ax.set(xticks=range(len(rates)), xticklabels=rates, xlabel="Synthetic finals / second",
       ylabel="Required last-60-second finals retained (%)", ylim=(0, 115))
ax.set_title("Deterministic retention capacity probe · 90 s / 5,000-item History")
fig.savefig(HERE / "history_capacity.png", dpi=180)
plt.close(fig)
print("Rendered scheduler_comparison.png and history_capacity.png")
