"""Plot oracle-verified recovery results; no estimated measurements."""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
before = json.loads((HERE / "before.json").read_text(encoding="utf-8"))
current = json.loads((HERE / "current.json").read_text(encoding="utf-8"))
names = list(current["summary"])
labels = ["Small\nlate join", "Large\nhistory", "Large\nresume", "300 active\nsegments", "One-slot\nlive queue"]
positions = list(range(len(names)))

fig, axes = plt.subplots(1, 2, figsize=(11, 3.7), constrained_layout=True)
for version, data, offset, color in (("Before", before, -.18, "#a9b8c5"),
                                      ("Batched", current, .18, "#143b61")):
    successes = []
    for name in names:
        successes.append(data["summary"][name]["successful_runs"])
    axes[0].bar([x + offset for x in positions], successes, width=.36,
                label=version, color=color)
    for x, count in zip(positions, successes):
        axes[0].text(x + offset, count + .06, f"{count}/3", ha="center", fontsize=7)
axes[0].set(ylabel="Fully correct runs / 3", ylim=(0, 3.6), yticks=[0, 1, 2, 3],
            xticks=positions, xticklabels=labels)
axes[0].legend(fontsize=9)

medians = []
lower = []
upper = []
for name in names:
    summary = current["summary"][name]
    value = summary["median_complete_recovery_ms"]
    medians.append(value)
    lower.append(value - summary["min_complete_recovery_ms"])
    upper.append(summary["max_complete_recovery_ms"] - value)
axes[1].bar(positions, medians, yerr=[lower, upper], capsize=4, color="#143b61")
for x, value, whisker in zip(positions, medians, upper):
    axes[1].text(x, value + whisker + .7, f"{value:.2f}", ha="center", fontsize=9)
axes[1].set(ylabel="Complete recovery time (ms)", xticks=positions, xticklabels=labels,
            ylim=(0, max(medians) * 1.55))
for ax in axes:
    ax.grid(axis="y", alpha=.2)
    ax.set_axisbelow(True)
fig.suptitle("GB10 loopback: all required finals + latest active state verified\n"
             "Three independent connections per case; timing bars show median and observed range", fontsize=11)
fig.savefig(HERE / "recovery_comparison.png", dpi=180)
plt.close(fig)
print("Wrote recovery_comparison.png")
